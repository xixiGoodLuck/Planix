from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..schemas import AgentRunRequest
from ..services.runtime import RuntimeOrchestrator

router = APIRouter(prefix="/api/runtime", tags=["runtime"])

runtime = RuntimeOrchestrator()


@router.post("/run")
def run_agent_runtime(payload: AgentRunRequest) -> StreamingResponse:
    return StreamingResponse(
        runtime.run(payload),
        media_type="application/x-ndjson",
    )
