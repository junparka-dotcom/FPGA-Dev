from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services.rtl_generator import (
    RTLGenerationError,
    RTLProviderNotConfigured,
    ResponseModel,
    request_rtl,
)

router = APIRouter(prefix="/api/rtl", tags=["RTL"])


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    module_name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_$]*$")


class GenerateResponse(BaseModel):
    design_code: str
    testbench_code: str
    explanation: str


class AutofixRequest(BaseModel):
    design_code: str
    testbench_code: str
    error_logs: str


class AutofixResponse(BaseModel):
    fixed_design_code: str
    fixed_testbench_code: str
    fix_summary: str


@router.post("/generate")
def generate(request: GenerateRequest) -> GenerateResponse:
    return _complete(
        request,
        "Generate the RTL and its testbench from the user request. "
        "Use module_name exactly for the design's top module.",
        GenerateResponse,
    )


@router.post("/autofix")
def autofix(request: AutofixRequest) -> AutofixResponse:
    return _complete(
        request,
        "Repair the supplied RTL and testbench using the tool diagnostics. "
        "Preserve the intended behavior and the design's public module interface.",
        AutofixResponse,
    )


def _complete(
    request: BaseModel, task: str, response_model: type[ResponseModel]
) -> ResponseModel:
    try:
        return request_rtl(request.model_dump_json(), task, response_model)
    except RTLProviderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RTLGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
