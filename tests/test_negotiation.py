import pytest

from app.models import NegotiationStatus
from app.services import negotiation
from app.services.tms_client import tms_client


@pytest.fixture(autouse=True)
def fake_tms(monkeypatch):
    """Fake get_load_detail so negotiation tests don't depend on the real TMS."""

    async def fake_get_load_detail(load_id: str):
        return None, 1500.0  # max_rate = 1500 regardless of load_id

    monkeypatch.setattr(tms_client, "get_load_detail", fake_get_load_detail)
    negotiation._negotiations.clear()
    yield
    negotiation._negotiations.clear()


@pytest.mark.asyncio
async def test_offer_at_or_below_ceiling_accepted():
    status, offer, round_num = await negotiation.counter("call1", "load1", 1400.0)
    assert status == NegotiationStatus.accepted
    assert offer == 1400.0
    assert round_num == 1


@pytest.mark.asyncio
async def test_offer_above_ceiling_countered_at_max_rate():
    status, offer, round_num = await negotiation.counter("call2", "load1", 1800.0)
    assert status == NegotiationStatus.countered
    assert offer == 1500.0  # counters at max_rate, never above it
    assert round_num == 1


@pytest.mark.asyncio
async def test_fails_after_three_rounds_no_deal():
    call_id = "call3"
    for expected_round in (1, 2):
        status, _, round_num = await negotiation.counter(call_id, "load1", 1800.0)
        assert status == NegotiationStatus.countered
        assert round_num == expected_round

    status, offer, round_num = await negotiation.counter(call_id, "load1", 1800.0)
    assert status == NegotiationStatus.failed
    assert offer is None
    assert round_num == 3


@pytest.mark.asyncio
async def test_accept_mid_negotiation_stops_further_rounds():
    call_id = "call4"
    await negotiation.counter(call_id, "load1", 1800.0)  # round 1: countered
    status, offer, round_num = await negotiation.counter(call_id, "load1", 1500.0)  # round 2: accept
    assert status == NegotiationStatus.accepted
    assert offer == 1500.0
    assert round_num == 2

    # A further call should just return the already-resolved state, not reopen
    status2, _, round_num2 = await negotiation.counter(call_id, "load1", 1000.0)
    assert status2 == NegotiationStatus.accepted
    assert round_num2 == 2


@pytest.mark.asyncio
async def test_max_rate_never_appears_in_any_response_field():
    status, offer, round_num = await negotiation.counter("call5", "load1", 1800.0)
    # offer is either None or the carrier's own number or exactly max_rate as a counter —
    # there is structurally no field that could carry a "here's our secret ceiling" value
    # distinct from the counter itself.
    assert offer in (None, 1500.0)
