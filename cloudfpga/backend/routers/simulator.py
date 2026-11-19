from fastapi import APIRouter
from pydantic import BaseModel

from backend.services.simulation import SimulationResult, simulate_rtl

router = APIRouter(prefix="/api/rtl", tags=["RTL"])


class SimulationRequest(BaseModel):
    design_code: str
    testbench_code: str


@router.post("/simulate")
def simulate(request: SimulationRequest) -> SimulationResult:
    return simulate_rtl(request.design_code, request.testbench_code)
