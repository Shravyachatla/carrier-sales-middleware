from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.models import (
    SendOtpRequest,
    SendOtpResponse,
    VerifyCarrierRequest,
    VerifyCarrierResponse,
    VerifyOtpRequest,
    VerifyOtpResponse,
)
from app.services import fmcsa_client, otp_service

router = APIRouter(prefix="/carriers", tags=["carriers"], dependencies=[Depends(require_auth)])

# Minimal in-memory lookup of verified carriers' on-file contact info.
# TODO: replace with a real carrier profile source (Twin or the TMS) — OTP must
# go to an on-file contact, never a number/email supplied mid-call.
_carrier_contacts: dict[str, dict[str, str]] = {}


@router.post("/verify", response_model=VerifyCarrierResponse)
async def verify_carrier(req: VerifyCarrierRequest) -> VerifyCarrierResponse:
    result = await fmcsa_client.verify_carrier(req.mc_number)
    if result.authorized and result.carrier_id:
        _carrier_contacts[result.carrier_id] = {"name": result.carrier_name or ""}
    return result


@router.post("/otp/send", response_model=SendOtpResponse)
async def send_otp(req: SendOtpRequest) -> SendOtpResponse:
    if req.carrier_id not in _carrier_contacts:
        raise HTTPException(status_code=400, detail="Carrier must pass FMCSA verification before OTP")

    # TODO: pull the real on-file destination (phone/email) once carrier
    # profile data is available — placeholder destination for now.
    destination = f"on-file-{req.channel}-for-{req.carrier_id}"
    otp_id, expires_at = otp_service.send_otp(req.call_id, req.carrier_id, req.channel, destination)
    return SendOtpResponse(otp_id=otp_id, expires_at=datetime.fromtimestamp(expires_at))


@router.post("/otp/verify", response_model=VerifyOtpResponse)
async def verify_otp(req: VerifyOtpRequest) -> VerifyOtpResponse:
    verified, attempts_remaining = otp_service.verify_otp(req.otp_id, req.code)
    return VerifyOtpResponse(verified=verified, attempts_remaining=attempts_remaining)
