"""
OTP generation, delivery, and verification.

Delivery is stubbed to console logging (OTP_PROVIDER=console) until a real
SMS/email provider is wired in — swap _deliver() to call Twilio/SendGrid/etc.
without changing anything else here.

Anti-bypass design (see architecture doc section 6):
- OTPs are tied to a carrier_id resolved server-side from FMCSA verification,
  never to a phone/email the caller supplies mid-call.
- There is no "resend to a different number" path — this file exposes no way
  to redirect delivery, by design.
- Verification state lives here, not in agent/prompt state — negotiation and
  load-search endpoints call `is_otp_verified(call_id)` themselves rather
  than trusting anything the voice agent claims.
"""

import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger("otp_service")


@dataclass
class OtpRecord:
    otp_id: str
    call_id: str
    carrier_id: str
    code: str
    created_at: float
    attempts: int = 0
    verified: bool = False


# In-memory store — fine for a POC / single-instance deploy. Swap for Twin or
# Redis if the middleware needs to run multi-instance.
_otps: dict[str, OtpRecord] = {}
_verified_calls: set[str] = set()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _deliver(channel: str, destination: str, code: str) -> None:
    if settings.otp_provider == "console":
        logger.info("OTP for %s via %s: %s", destination, channel, code)
        return
    raise NotImplementedError(f"OTP provider '{settings.otp_provider}' not wired in yet")


def send_otp(call_id: str, carrier_id: str, channel: str, destination: str) -> tuple[str, float]:
    otp_id = str(uuid.uuid4())
    code = _generate_code()
    record = OtpRecord(otp_id=otp_id, call_id=call_id, carrier_id=carrier_id, code=code, created_at=time.time())
    _otps[otp_id] = record
    _deliver(channel, destination, code)
    expires_at = record.created_at + settings.otp_expiry_seconds
    return otp_id, expires_at


def verify_otp(otp_id: str, code: str) -> tuple[bool, int]:
    record = _otps.get(otp_id)
    if record is None:
        return False, 0

    attempts_remaining = settings.otp_max_attempts - record.attempts
    if attempts_remaining <= 0:
        return False, 0

    if time.time() > record.created_at + settings.otp_expiry_seconds:
        return False, 0

    record.attempts += 1
    attempts_remaining = settings.otp_max_attempts - record.attempts

    if secrets.compare_digest(record.code, code.strip()):
        record.verified = True
        _verified_calls.add(record.call_id)
        return True, attempts_remaining

    return False, attempts_remaining


def is_otp_verified(call_id: str) -> bool:
    """Source of truth used by other endpoints — never trust agent-reported state."""
    return call_id in _verified_calls
