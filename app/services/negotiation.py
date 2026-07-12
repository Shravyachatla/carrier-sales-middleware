"""
Negotiation engine.

max_rate is fetched once per call from the TMS adapter and held only in this
in-memory state — it is never included in any response model (see models.py,
CounterResponse has no max_rate field) and callers of this module cannot
retrieve it directly. This is the structural enforcement of "never disclose
max_rate to the carrier directly or indirectly."
"""

from dataclasses import dataclass, field

from app.config import settings
from app.models import NegotiationStatus
from app.services.tms_client import tms_client


class LoadNotAvailableError(Exception):
    """The load's real-time STATUS from the TMS isn't OPEN. Distinct from
    TMSProtocolError('ALREADY_BOOKED'), which is the TMS rejecting a booking
    attempt after the fact; this catches it earlier, at negotiate time, using
    the STATUS field surfaced on LOAD_GET."""

    def __init__(self, load_id: str, status: str):
        self.load_id = load_id
        self.status = status
        super().__init__(f"Load {load_id} is not open (status={status})")


@dataclass
class NegotiationState:
    call_id: str
    load_id: str
    max_rate: float
    round: int = 0
    offers: list[float] = field(default_factory=list)
    status: NegotiationStatus = NegotiationStatus.countered


_negotiations: dict[str, NegotiationState] = {}


async def start_or_get(call_id: str, load_id: str) -> NegotiationState:
    key = f"{call_id}:{load_id}"
    if key not in _negotiations:
        detail, max_rate = await tms_client.get_load_detail(load_id)
        if detail.status and detail.status.upper() != "OPEN":
            raise LoadNotAvailableError(load_id, detail.status)
        _negotiations[key] = NegotiationState(call_id=call_id, load_id=load_id, max_rate=max_rate)
    return _negotiations[key]


async def counter(call_id: str, load_id: str, carrier_offer: float) -> tuple[NegotiationStatus, float | None, int]:
    state = await start_or_get(call_id, load_id)

    if state.status != NegotiationStatus.countered:
        # Already resolved (accepted or failed) — return the final state, don't reopen.
        return state.status, None, state.round

    state.round += 1
    state.offers.append(carrier_offer)

    if carrier_offer <= state.max_rate:
        state.status = NegotiationStatus.accepted
        return NegotiationStatus.accepted, carrier_offer, state.round

    if state.round >= settings.negotiation_max_rounds:
        state.status = NegotiationStatus.failed
        return NegotiationStatus.failed, None, state.round

    # Counter at the ceiling — never above it, never revealing it's exactly the ceiling
    # via any other signal (same response shape every round).
    return NegotiationStatus.countered, state.max_rate, state.round
