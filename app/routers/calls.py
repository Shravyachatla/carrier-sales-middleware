import logging

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.models import CallLogRequest, CallLogResponse, HandoffRequest, HandoffResponse

router = APIRouter(prefix="/calls", tags=["calls"], dependencies=[Depends(require_auth)])
logger = logging.getLogger("calls")


@router.post("/{call_id}/handoff", response_model=HandoffResponse)
async def handoff(call_id: str, req: HandoffRequest) -> HandoffResponse:
    # Transfers don't work with web calls per the brief — mocked.
    logger.info("Mock handoff for call %s, booking %s", call_id, req.booking_id)
    return HandoffResponse()


@router.post("/{call_id}/log", response_model=CallLogResponse)
async def log_call(call_id: str, req: CallLogRequest) -> CallLogResponse:
    # TODO: write to Twin instead of application logs once the Twin write
    # path/schema is confirmed on the HappyRobot platform side.
    logger.info("Call log %s: %s", call_id, req.model_dump())
    return CallLogResponse()
