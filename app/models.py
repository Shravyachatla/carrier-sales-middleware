from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ---- Carriers / verification ----

class VerifyCarrierRequest(BaseModel):
    mc_number: str


class VerifyCarrierResponse(BaseModel):
    authorized: bool
    carrier_id: str | None = None
    carrier_name: str | None = None
    reason: str | None = None


class SendOtpRequest(BaseModel):
    call_id: str
    carrier_id: str
    channel: str = Field(pattern="^(sms|email)$")


class SendOtpResponse(BaseModel):
    otp_id: str
    expires_at: datetime


class VerifyOtpRequest(BaseModel):
    otp_id: str
    code: str


class VerifyOtpResponse(BaseModel):
    verified: bool
    attempts_remaining: int


# ---- Loads ----

class LoadSearchRequest(BaseModel):
    equipment_type: str
    origin: str | None = None
    destination: str | None = None


class LoadLocation(BaseModel):
    city: str | None = None
    state: str | None = None
    zip: str | None = None


class LoadSummary(BaseModel):
    """What LOAD_QUERY returns — origin/destination/equipment only, per the TMS spec."""
    load_id: str
    origin: LoadLocation
    destination: LoadLocation
    equipment_type: str | None = None


class LoadDetail(LoadSummary):
    """What LOAD_GET returns — adds schedule, cargo, notes, and pricing.
    Exact field names beyond RATE/COMMODITY/DIMS/NOTES aren't enumerated in the
    TMS spec ("operators are expected to determine field names from transcripts") —
    raw_fields carries anything not explicitly mapped so nothing is silently dropped.
    """
    loadboard_rate: float | None = None
    commodity_type: str | None = None
    dimensions: str | None = None
    notes: str | None = None
    status: str | None = None
    miles: int | None = None
    raw_fields: dict[str, str] = {}
    # max_rate (MAX_BUY) is intentionally NOT a field here — it's handled
    # separately by the TMS client and never included in any response model
    # that could reach the carrier.


class LoadSearchResponse(BaseModel):
    loads: list[LoadSummary]


# ---- Negotiation ----

class NegotiationStatus(str, Enum):
    accepted = "accepted"
    countered = "countered"
    failed = "failed"


class CounterRequest(BaseModel):
    call_id: str
    load_id: str
    carrier_offer: float


class CounterResponse(BaseModel):
    status: NegotiationStatus
    counter_offer: float | None = None
    round: int


class BookLoadRequest(BaseModel):
    call_id: str
    agreed_rate: float


class BookLoadResponse(BaseModel):
    booking_id: str
    status: str


# ---- Calls ----

class CallOutcome(str, Enum):
    booked = "booked"
    failed_verification = "failed_verification"
    failed_negotiation = "failed_negotiation"
    no_match = "no_match"
    error = "error"


class HandoffRequest(BaseModel):
    booking_id: str


class HandoffResponse(BaseModel):
    handoff_status: str = "mocked_success"


class CallLogRequest(BaseModel):
    call_id: str
    carrier_mc: str | None = None
    carrier_name: str | None = None
    fmcsa_verified: bool = False
    otp_verified: bool = False
    load_id: str | None = None
    loadboard_rate: float | None = None
    agreed_rate: float | None = None
    rounds_negotiated: int = 0
    outcome: CallOutcome
    notes: str | None = None


class CallLogResponse(BaseModel):
    logged: bool = True
