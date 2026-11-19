import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.services.vcd import Signal, parse_vcd

WORKSPACE_ROOT = Path(__file__).resolve().parents[1] / "tmp_workspaces"
MAX_VCD_BYTES = 4 * 1024 * 1024

# Inspect tokens outside comments, string literals and escaped identifiers.
VERILOG_TOKENS = re.compile(
    r'//[^\r\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\\[^\s]+'
    r"|[a-z_][a-z0-9_$]*|[$\x60][a-z0-9_$]*|\S",
    re.IGNORECASE | re.DOTALL | re.ASCII,
)
SAFE_DIRECTIVES = {"timescale", "default_nettype", "resetall"}
SAFE_SYSTEM_NAMES = {
    "$display", "$write", "$strobe", "$monitor", "$monitoron", "$monitoroff",
    "$finish", "$stop", "$fatal", "$error", "$warning", "$info",
    "$dumpfile", "$dumpvars", "$dumpall", "$dumpon", "$dumpoff",
    "$time", "$stime", "$realtime", "$timeformat", "$printtimescale",
    "$signed", "$unsigned", "$random", "$clog2",
    "$bitstoreal", "$realtobits", "$itor", "$rtoi",
}


class SimulationResult(BaseModel):
    success: bool = False
    compile_logs: str = ""
    sim_logs: str = ""
    vcd_raw: str = ""
    signals: list[Signal] = Field(default_factory=list)


def _validate_source(source: str) -> None:
    tokens = [
        match.group()
        for match in VERILOG_TOKENS.finditer(source)
        if not match.group().startswith(("//", "/*"))
    ]
    for index, token in enumerate(tokens):
        if token.startswith("\x60") and token[1:] not in SAFE_DIRECTIVES:
            raise ValueError(f"Unsupported Verilog preprocessor token: {token}.")
        if token.startswith("$") and token not in SAFE_SYSTEM_NAMES:
            raise ValueError(f"Unsupported Verilog system task or function: {token}.")
        if token == "$dumpfile" and tokens[index + 1:index + 4] != [
            "(", '"dump.vcd"', ")"
        ]:
            raise ValueError('$dumpfile must use the literal "dump.vcd".')


def _run(command: list[str], workspace: Path) -> tuple[bool, str]:
    try:
        process = subprocess.run(
            command,
            cwd=workspace,
            timeout=15,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return False, f"{command[0]} was not found on PATH."
    except subprocess.TimeoutExpired as exc:
        output = [
            item.decode("utf-8", errors="replace")
            for item in (exc.stdout, exc.stderr)
            if item
        ]
        output.append(f"{command[0]} timed out after 15 seconds.")
        return False, "\n".join(output)
    logs = "\n".join(
        stream.rstrip("\n") for stream in (process.stdout, process.stderr) if stream
    )
    if process.returncode and not logs:
        logs = f"{command[0]} exited with status {process.returncode}."
    return process.returncode == 0, logs


def simulate_rtl(design_code: str, testbench_code: str) -> SimulationResult:
    WORKSPACE_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    workspace = WORKSPACE_ROOT / str(uuid4())
    workspace.mkdir(mode=0o700)
    result = SimulationResult()
    try:
        try:
            _validate_source(design_code)
            _validate_source(testbench_code)
        except ValueError as exc:
            result.compile_logs = str(exc)
            return result

        (workspace / "design.v").write_text(design_code, encoding="utf-8")
        (workspace / "testbench.v").write_text(testbench_code, encoding="utf-8")
        compiled, result.compile_logs = _run(
            ["iverilog", "-g2005", "-s", "tb", "-o", "sim.vvp", "design.v", "testbench.v"],
            workspace,
        )
        if not compiled:
            return result

        simulated, result.sim_logs = _run(["vvp", "-n", "sim.vvp"], workspace)
        if not simulated:
            return result

        try:
            with (workspace / "dump.vcd").open("rb") as waveform:
                data = waveform.read(MAX_VCD_BYTES + 1)
            if len(data) > MAX_VCD_BYTES:
                raise ValueError("dump.vcd exceeds the 4 MiB limit.")
            result.vcd_raw = data.decode("utf-8", errors="replace")
            result.signals = parse_vcd(result.vcd_raw)
        except FileNotFoundError:
            result.sim_logs = f"{result.sim_logs}\nSimulation did not create dump.vcd.".strip()
            return result
        except ValueError as exc:
            result.sim_logs = f"{result.sim_logs}\n{exc}".strip()
            return result

        result.success = True
        return result
    finally:
        shutil.rmtree(workspace)
