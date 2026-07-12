from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.models import BookLoadRequest, BookLoadResponse, CounterRequest, CounterResponse
from app.services import negotiation, otp_service
from app.services.negotiation import LoadNotAvailableError
from app.services.tms_client import TMSProtocolError, TMSUnavailableError, tms_client

router = APIRouter(tags=["negotiations"], dependencies=[Depends(require_auth)])


@router.post("/negotiations/counter", response_model=CounterResponse)
async def counter(req: CounterRequest) -> CounterResponse:
    if not otp_service.is_otp_verified(req.call_id):
        raise HTTPException(status_code=403, detail="OTP verification required before negotiation")

    try:
        status, counter_offer, round_num = await negotiation.counter(req.call_id, req.load_id, req.carrier_offer)
    except LoadNotAvailableError as exc:
        raise HTTPException(status_code=409, detail=f"Load is no longer available (status={exc.status})")
    except TMSUnavailableError:
        raise HTTPException(status_code=502, detail="Load system is temporarily unavailable")
    except TMSProtocolError as exc:
        if exc.code == "UNKNOWN_LOAD":
            raise HTTPException(status_code=404, detail="Load not found")
        raise HTTPException(status_code=400, detail=f"{exc.code}: {exc.message}")

    return CounterResponse(status=status, counter_offer=counter_offer, round=round_num)


@router.post("/loads/{load_id}/book", response_model=BookLoadResponse)
async def book_load(load_id: str, req: BookLoadRequest, carrier_mc: str) -> BookLoadResponse:
    if not otp_service.is_otp_verified(req.call_id):
        raise HTTPException(status_code=403, detail="OTP verification required before booking")

    try:
        result = await tms_client.book_load(load_id, carrier_mc, req.agreed_rate)
    except TMSUnavailableError:
        raise HTTPException(status_code=502, detail="Booking system is temporarily unavailable")
    except TMSProtocolError as exc:
        if exc.code == "ALREADY_BOOKED":
            raise HTTPException(status_code=409, detail="Load is no longer available")
        if exc.code == "INVALID_RATE":
            raise HTTPException(status_code=422, detail="Agreed rate was rejected by the TMS")
        if exc.code == "UNKNOWN_LOAD":
            raise HTTPException(status_code=404, detail="Load not found")
        raise HTTPException(status_code=400, detail=f"{exc.code}: {exc.message}")

    return BookLoadResponse(booking_id=result["booking_id"], status=result["status"])
