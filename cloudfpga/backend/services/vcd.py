from collections.abc import Iterator
from dataclasses import dataclass, field

from pydantic import BaseModel

MAX_WAVE_SAMPLES = 250_000
DUMP_COMMANDS = {"$dumpvars", "$dumpall", "$dumpon", "$dumpoff", "$end"}


class Signal(BaseModel):
    name: str
    wave: str | list[str]


@dataclass
class _Trace:
    name: str
    identifier: str
    width: int
    real: bool
    samples: list[str] = field(default_factory=list)

    def sample(self, value: str) -> None:
        if not self.real and self.width > 1 and value != "x":
            if set(value) <= {"0", "1"}:
                value = str(int(value, 2))
            else:
                pad = value[0] if value[0] in "xz" else "0"
                value = value.rjust(self.width, pad)
        self.samples.append(value)

    def signal(self) -> Signal:
        wave = self.samples if self.real or self.width > 1 else "".join(self.samples)
        return Signal(name=self.name, wave=wave)


def _read_declarations(tokens: Iterator[str]) -> list[_Trace]:
    scopes: list[str] = []
    traces: list[_Trace] = []
    for command in tokens:
        args = []
        for token in tokens:
            if token == "$end":
                break
            args.append(token)
        else:
            raise ValueError("Unterminated VCD declaration.")

        if command == "$enddefinitions":
            return traces
        if command == "$scope":
            if len(args) != 2:
                raise ValueError("Invalid VCD scope.")
            scopes.append(args[1])
        elif command == "$upscope":
            if not scopes:
                raise ValueError("Unmatched VCD scope.")
            scopes.pop()
        elif command == "$var":
            if len(args) < 4:
                raise ValueError("Invalid VCD variable.")
            kind, size, identifier, reference, *selection = args
            width = int(size)
            if not 1 <= width <= 4096:
                raise ValueError("VCD signal width must be between 1 and 4096 bits.")
            hierarchy = scopes[1:] if scopes[:1] == ["tb"] else scopes
            name = ".".join([*hierarchy, reference + "".join(selection)])
            traces.append(_Trace(name, identifier, width, kind in {"real", "realtime"}))
    raise ValueError("VCD is missing $enddefinitions.")


def parse_vcd(vcd_raw: str) -> list[Signal]:
    """Sample all signals after the changes at each VCD timestamp."""
    tokens = iter(vcd_raw.split())
    traces = _read_declarations(tokens)
    values: dict[str, str] = {}
    timestamp = 0
    pending = False
    sample_count = 0

    def record_sample() -> None:
        nonlocal sample_count
        sample_count += len(traces)
        if sample_count > MAX_WAVE_SAMPLES:
            raise ValueError("VCD exceeds the waveform sample limit.")
        for trace in traces:
            trace.sample(values.get(trace.identifier, "x"))

    for token in tokens:
        prefix = token[0].lower()
        if prefix == "#":
            next_time = int(token[1:])
            if next_time < timestamp:
                raise ValueError("VCD timestamps are out of order.")
            if pending and next_time != timestamp:
                record_sample()
            timestamp = next_time
            pending = True
        elif token in DUMP_COMMANDS:
            continue
        elif token.startswith("$"):
            for argument in tokens:
                if argument == "$end":
                    break
            else:
                raise ValueError("Unterminated VCD command.")
        elif prefix in "01xz" and len(token) > 1:
            values[token[1:]] = prefix
            pending = True
        elif prefix in "br" and len(token) > 1:
            identifier = next(tokens, None)
            value = token[1:].lower()
            if identifier is None or (
                prefix == "b" and (len(value) > 4096 or set(value) - set("01xz"))
            ):
                raise ValueError("Invalid VCD value change.")
            values[identifier] = value
            pending = True
        else:
            raise ValueError("Invalid VCD data.")
    if pending:
        record_sample()
    return [trace.signal() for trace in traces]
