"""
Manual/live smoke test: negotiate a rate against the real TMS ceiling, then
book the load once accepted. Uses the internal negotiation/tms_client
services directly (same as the other test_live_* scripts) rather than going
through the HTTP API — no OTP gating to worry about here.

Simulates a simple carrier negotiation strategy: open with `opening_offer`;
if countered, accept the counter on the next round (real carriers might push
back further, but this proves the mechanics end-to-end). If the offer starts
at or below the ceiling, it's accepted immediately in round 1.

Run inside the container:
    docker cp test_live_negotiate_and_book.py carrier-sales-middleware-middleware-1:/app/test_live_negotiate_and_book.py
    docker compose exec middleware python test_live_negotiate_and_book.py <LOAD_ID> <CARRIER_MC> <OPENING_OFFER>

Example:
    docker compose exec middleware python test_live_negotiate_and_book.py LD00323 876419 5800

Careful: a successful run actually books the load for real against the TMS.
If you want to just watch the negotiation without booking, use --no-book.
"""

import asyncio
import sys
import time

from app.services import negotiation
from app.services.negotiation import LoadNotAvailableError
from app.services.tms_client import TMSProtocolError, TMSUnavailableError, tms_client


async def main(load_id: str, carrier_mc: str, opening_offer: float, do_book: bool) -> int:
    call_id = f"test-call-{int(time.time())}"  # unique per run so state doesn't collide with prior tests
    print(f"call_id: {call_id}  load_id: {load_id}  carrier_mc: {carrier_mc}")
    print("-" * 60)

    offer = opening_offer
    accepted_rate = None

    for round_num in range(1, 5):  # negotiation engine caps at 3 rounds internally; 5 is just a safety bound here
        print(f"Round {round_num}: carrier offers {offer}")
        try:
            status, counter_offer, tms_round = await negotiation.counter(call_id, load_id, offer)
        except LoadNotAvailableError as exc:
            print(f"LOAD NOT AVAILABLE: status={exc.status} — cannot negotiate on this load. Pick another load_id.")
            return 1
        except TMSUnavailableError as exc:
            print(f"UNAVAILABLE: {exc}")
            return 1
        except TMSProtocolError as exc:
            print(f"PROTOCOL ERROR: {exc.code} — {exc.message}")
            return 1

        print(f"  -> status={status.value}  counter_offer={counter_offer}  round={tms_round}")

        if status.value == "accepted":
            accepted_rate = counter_offer  # counter_offer holds the accepted rate on acceptance
            print(f"\nNegotiation ACCEPTED at {accepted_rate}.")
            break
        elif status.value == "failed":
            print("\nNegotiation FAILED — no deal after max rounds. Nothing to book.")
            return 1
        else:
            # countered — simulate the carrier accepting the counter next round
            offer = counter_offer

    if accepted_rate is None:
        print("\nDidn't reach a resolution within the safety bound — check negotiation logic.")
        return 1

    if not do_book:
        print("\n--no-book passed — skipping the actual LOAD_BOOK call.")
        return 0

    print(f"\nBooking load {load_id} for carrier MC {carrier_mc} at agreed_rate={accepted_rate}...")
    try:
        result = await tms_client.book_load(load_id, carrier_mc, accepted_rate)
    except TMSUnavailableError as exc:
        print(f"UNAVAILABLE after retries exhausted: {exc}")
        return 1
    except TMSProtocolError as exc:
        if exc.code == "ALREADY_BOOKED":
            print("ALREADY_BOOKED — someone/something else booked this load first.")
        elif exc.code == "INVALID_RATE":
            print(f"INVALID_RATE — the TMS rejected {accepted_rate} as the agreed rate.")
        else:
            print(f"PROTOCOL ERROR: {exc.code} — {exc.message}")
        return 1

    print("BOOKED:")
    print(f"  booking_id: {result['booking_id']}")
    print(f"  status:     {result['status']}")
    print(f"  timestamp:  {result['timestamp']}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--no-book"]
    do_book = "--no-book" not in sys.argv
    if len(args) != 3:
        print("Usage: python test_live_negotiate_and_book.py <LOAD_ID> <CARRIER_MC> <OPENING_OFFER> [--no-book]")
        sys.exit(2)
    load_id, carrier_mc, opening_offer = args[0], args[1], float(args[2])
    sys.exit(asyncio.run(main(load_id, carrier_mc, opening_offer, do_book)))