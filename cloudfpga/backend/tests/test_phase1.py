import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient, Request
from openai import APIConnectionError

from backend.main import app
from backend.routers.rtl_gen import AutofixResponse, GenerateResponse
from backend.services import simulation
from backend.services.vcd import parse_vcd

AND_DESIGN = """\
module and_gate(input a, input b, output y);
    assign y = a & b;
endmodule
"""
AND_TESTBENCH = """\
module tb;
    reg a, b;
    wire y;
    and_gate dut(a, b, y);
    initial begin
        $dumpfile("dump.vcd");
        $dumpvars(0, tb);
        #1000 $finish;
    end
    initial begin
        {a, b} = 2'b00; #1 if (y !== 0) $fatal(1);
        {a, b} = 2'b01; #1 if (y !== 0) $fatal(1);
        {a, b} = 2'b10; #1 if (y !== 0) $fatal(1);
        {a, b} = 2'b11; #1 if (y !== 1) $fatal(1);
    end
endmodule
"""
SIMULATION_PAYLOAD = {"design_code": AND_DESIGN, "testbench_code": AND_TESTBENCH}


class SimulationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        scratch = contexts.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(scratch)
        contexts.enter_context(patch.object(simulation, "WORKSPACE_ROOT", self.root))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    def tearDown(self):
        self.assertEqual(list(self.root.iterdir()), [])

    async def simulate(self, **overrides):
        response = await self.client.post(
            "/api/rtl/simulate", json={**SIMULATION_PAYLOAD, **overrides}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_and_gate_with_real_tools(self):
        result = await self.simulate()
        self.assertEqual(
            set(result), {"success", "compile_logs", "sim_logs", "vcd_raw", "signals"}
        )
        self.assertTrue(result["success"], result)
        self.assertIn("$enddefinitions", result["vcd_raw"])
        signals = {signal["name"]: signal["wave"] for signal in result["signals"]}
        self.assertEqual(signals["y"], "00011")
        self.assertEqual(signals["dut.y"], signals["y"])

    async def test_compile_failure_preserves_diagnostics(self):
        result = await self.simulate(design_code="module and_gate(")
        self.assertFalse(result["success"])
        self.assertIn("syntax error", result["compile_logs"].lower())
        self.assertEqual(result["signals"], [])

    async def test_runtime_failure_preserves_diagnostics(self):
        result = await self.simulate(
            testbench_code="module tb; initial $fatal(1, \"failed assertion\"); endmodule"
        )
        self.assertFalse(result["success"])
        self.assertIn("failed assertion", result["sim_logs"])

    async def test_missing_vcd_is_reported(self):
        result = await self.simulate(testbench_code="module tb; initial $finish; endmodule")
        self.assertFalse(result["success"])
        self.assertIn("did not create dump.vcd", result["sim_logs"])

    async def test_external_access_is_rejected_before_compilation(self):
        unsafe_sources = (
            'module tb; initial $fopen("outside.txt", "w"); endmodule',
            '\x60include "outside.v"',
            'module tb; initial $dumpfile("../outside.vcd"); endmodule',
        )
        with patch.object(simulation.subprocess, "run") as run:
            for source in unsafe_sources:
                with self.subTest(source=source):
                    result = await self.simulate(testbench_code=source)
                    self.assertFalse(result["success"])
                    self.assertTrue(result["compile_logs"])
            run.assert_not_called()

    async def test_timeout_keeps_partial_output_and_cleans_workspace(self):
        timeout = subprocess.TimeoutExpired(["vvp"], 15, output=b"partial output")
        compile_success = subprocess.CompletedProcess(
            ["iverilog"], 0, stdout="", stderr=""
        )
        with patch.object(
            simulation.subprocess, "run", side_effect=[compile_success, timeout]
        ) as run:
            result = await self.simulate()
        self.assertFalse(result["success"])
        self.assertIn("partial output", result["sim_logs"])
        self.assertIn("timed out after 15 seconds", result["sim_logs"])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 15)
            self.assertTrue(call.kwargs["capture_output"])
            self.assertTrue(call.kwargs["text"])

    async def test_cors_allows_local_frontend(self):
        response = await self.client.options(
            "/api/rtl/simulate",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"], "http://localhost:3000"
        )


class VCDTests(unittest.TestCase):
    def test_scalar_bus_aliases_and_unknown_values_share_timestamps(self):
        raw = """\
$scope module tb $end
$var wire 1 ! clk $end
$var reg 4 " count [3:0] $end
$scope module dut $end
$var wire 1 ! clk $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
$dumpvars
x!
b0 "
$end
#5
1!
b10 "
#9
0!
#12
bz "
"""
        self.assertEqual(
            [signal.model_dump() for signal in parse_vcd(raw)],
            [
                {"name": "clk", "wave": "x100"},
                {"name": "count[3:0]", "wave": ["0", "2", "2", "zzzz"]},
                {"name": "dut.clk", "wave": "x100"},
            ],
        )

    def test_malformed_vcd(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            parse_vcd("$date now $end")


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_generate_and_autofix_use_their_response_schemas_and_rtl_rules(self):
        cases = (
            (
                "generate", {"prompt": "2-input AND gate", "module_name": "and_gate"},
                GenerateResponse(
                    design_code=AND_DESIGN, testbench_code=AND_TESTBENCH, explanation="AND"
                ),
            ),
            (
                "autofix", {**SIMULATION_PAYLOAD, "error_logs": "example diagnostic"},
                AutofixResponse(
                    fixed_design_code=AND_DESIGN,
                    fixed_testbench_code=AND_TESTBENCH,
                    fix_summary="Fixed",
                ),
            ),
        )
        for route, payload, result in cases:
            with (
                self.subTest(route=route),
                patch.dict(os.environ, {"OPENAI_API_KEY": "test", "OPENAI_MODEL": "test-model"}),
                patch("backend.services.rtl_generator.OpenAI") as openai,
            ):
                parse = openai.return_value.__enter__.return_value.responses.parse
                parse.return_value = SimpleNamespace(status="completed", output_parsed=result)
                response = await self.client.post(f"/api/rtl/{route}", json=payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), result.model_dump())
                kwargs = parse.call_args.kwargs
                self.assertIs(kwargs["text_format"], type(result))
                self.assertEqual(kwargs["model"], "test-model")
                self.assertEqual(json.loads(kwargs["input"]), payload)
                for rule in (
                    "IEEE 1364-2005", "synthesizable", "top module must be tb",
                    '$dumpfile("dump.vcd")', "$dumpvars(0, tb)", "#1000 $finish",
                ):
                    self.assertIn(rule, kwargs["instructions"])

    async def test_missing_key_is_reported(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            result = await self.client.post(
                "/api/rtl/generate", json={"prompt": "AND", "module_name": "and_gate"}
            )
        self.assertEqual(result.status_code, 503)

    async def test_openai_error_does_not_leak_provider_details(self):
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test"}),
            patch("backend.services.rtl_generator.OpenAI") as openai,
        ):
            parse = openai.return_value.__enter__.return_value.responses.parse
            parse.side_effect = APIConnectionError(request=Request("POST", "https://example.com"))
            result = await self.client.post(
                "/api/rtl/generate", json={"prompt": "AND", "module_name": "and_gate"}
            )
            self.assertEqual(result.status_code, 502)
            self.assertNotIn("example.com", result.text)

    async def test_invalid_module_name_is_rejected(self):
        result = await self.client.post(
            "/api/rtl/generate", json={"prompt": "AND", "module_name": "invalid name"}
        )
        self.assertEqual(result.status_code, 422)
