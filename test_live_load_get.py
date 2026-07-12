"""
Manual/live smoke test for LOAD_GET against the real Legacy TMS.

Primary goal: resolve the open question from the handoff notes — is MAX_BUY
present on the real token's records, or does every load fall back to RATE
as the negotiation ceiling? This print statement makes that visible directly
instead of inferring it from behavior.

Run inside the container:
    docker cp test_live_load_get.py carrier-sales-middleware-middleware-1:/app/test_live_load_get.py
    docker compose exec middleware python test_live_load_get.py <LOAD_ID>

Example, using a real load_id from a search_loads run:
    docker compose exec middleware python test_live_load_get.py LD00323
"""

import asyncio
import sys

from app.services.tms_client import TMSProtocolError, TMSUnavailableError, tms_client


async def main(load_id: str) -> int:
    print(f"Querying LOAD_GET: LOAD_ID={load_id}")
    print(f"Target: {tms_client.host}:{tms_client.port} (retries={tms_client.max_retries})")
    print("-" * 60)

    try:
        detail, max_rate = await tms_client.get_load_detail(load_id)
    except TMSUnavailableError as exc:
        print(f"UNAVAILABLE after retries exhausted: {exc}")
        print("Fault injection is active on LOAD_GET too — re-run a couple times.")
        return 1
    except TMSProtocolError as exc:
        if exc.code == "UNKNOWN_LOAD":
            print(f"UNKNOWN_LOAD — '{load_id}' doesn't exist on the TMS. Grab a fresh id from search_loads.")
        else:
            print(f"PROTOCOL ERROR: {exc.code} — {exc.message}")
        return 1

    print(f"load_id:          {detail.load_id}")
    print(f"origin:           {detail.origin.city}, {detail.origin.state} {detail.origin.zip or ''}".rstrip())
    print(f"destination:      {detail.destination.city}, {detail.destination.state} {detail.destination.zip or ''}".rstrip())
    print(f"equipment_type:   {detail.equipment_type}")
    print(f"loadboard_rate:   {detail.loadboard_rate}")
    print(f"commodity_type:   {detail.commodity_type}")
    print(f"dimensions:       {detail.dimensions}")
    print(f"notes:            {detail.notes!r}")
    print(f"status:           {detail.status}")
    print(f"miles:            {detail.miles}")
    print(f"raw_fields:       {detail.raw_fields}")
    print()
    print(f"negotiation ceiling (max_rate) resolved to: {max_rate}")
    if max_rate == detail.loadboard_rate:
        print("  -> MAX_BUY was ABSENT on this record — fell back to RATE as ceiling.")
        print("     (Try a few more load_ids — MAX_BUY may only be flagged on some tokens/loads.)")
    else:
        print("  -> MAX_BUY was PRESENT and differs from RATE — real ceiling confirmed on this load.")

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_live_load_get.py <LOAD_ID>")
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))