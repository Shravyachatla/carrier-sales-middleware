from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.models import LoadDetail, LoadSearchRequest, LoadSearchResponse
from app.services import otp_service
from app.services.tms_client import TMSProtocolError, TMSUnavailableError, tms_client

router = APIRouter(prefix="/loads", tags=["loads"], dependencies=[Depends(require_auth)])


@router.post("/search", response_model=LoadSearchResponse)
async def search_loads(req: LoadSearchRequest, call_id: str) -> LoadSearchResponse:
    if not otp_service.is_otp_verified(call_id):
        raise HTTPException(status_code=403, detail="OTP verification required before load search")

    try:
        loads = await tms_client.search_loads(req.equipment_type, req.origin, req.destination)
    except TMSUnavailableError:
        raise HTTPException(status_code=502, detail="Load system is temporarily unavailable")
    except TMSProtocolError as exc:
        raise HTTPException(status_code=400, detail=f"{exc.code}: {exc.message}")

    return LoadSearchResponse(loads=loads)


@router.get("/{load_id}", response_model=LoadDetail)
async def get_load_detail(load_id: str, call_id: str) -> LoadDetail:
    if not otp_service.is_otp_verified(call_id):
        raise HTTPException(status_code=403, detail="OTP verification required before load detail")

    try:
        load, _max_rate = await tms_client.get_load_detail(load_id)
    except TMSUnavailableError:
        raise HTTPException(status_code=502, detail="Load system is temporarily unavailable")
    except TMSProtocolError as exc:
        if exc.code == "UNKNOWN_LOAD":
            raise HTTPException(status_code=404, detail="Load not found")
        raise HTTPException(status_code=400, detail=f"{exc.code}: {exc.message}")

    return load  # max_rate deliberately dropped — never returned here
