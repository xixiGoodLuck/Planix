from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..schemas import (
    CommandApproveRequest,
    CommandChatRequest,
    CommandThreadSummaryOut,
    CommandThreadOut,
)
from ..services.command_agent import CommandAgentService

router = APIRouter(prefix="/api/command", tags=["command"])


@router.post("/chat")
def post_command_chat(payload: CommandChatRequest) -> StreamingResponse:
    service = CommandAgentService()
    return StreamingResponse(service.stream_chat(payload), media_type="application/x-ndjson")


@router.post("/approve")
def post_command_approve(payload: CommandApproveRequest) -> StreamingResponse:
    service = CommandAgentService()
    return StreamingResponse(service.stream_approve(payload), media_type="application/x-ndjson")


@router.get("/threads", response_model=list[CommandThreadSummaryOut])
def list_command_threads(limit: int = 50) -> list[CommandThreadSummaryOut]:
    return CommandAgentService().list_threads(limit=limit)


@router.get("/thread/{thread_id}", response_model=CommandThreadOut)
def get_command_thread(thread_id: str) -> CommandThreadOut:
    return CommandAgentService().get_thread(thread_id)


@router.delete("/thread/{thread_id}")
def delete_command_thread(thread_id: str) -> dict[str, int]:
    return CommandAgentService().delete_thread(thread_id)
