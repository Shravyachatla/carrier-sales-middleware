"""
Manual/live smoke test for LOAD_QUERY against the real Legacy TMS.

Unlike DEBUG_ECHO, LOAD_QUERY has fault injection active — expect some runs
to trigger a retry (logged as a WARNING from tms_client) before succeeding,
and occasionally to exhaust retries and raise TMSUnavailableError. Both are
useful signal: a clean run proves the happy path, a retried run proves the
fault-detection/retry logic actually engages against the real server (not
just in the unit tests' simulated faults).

Run inside the container:
    docker compose exec middleware python test_live_search_loads.py

Try a few different EQTYPE / origin / destination combos — we don't yet know
which lanes/equipment types have data behind them on the real token.
"""

import asyncio
import sys

from app.services.tms_client import TMSProtocolError, TMSUnavailableError, tms_client

# Adjust these per run to probe different lanes/equipment.
EQUIPMENT_TYPE = "DRY_VAN"
ORIGIN = None
DESTINATION = None

async def main() -> int:
    print(f"Querying LOAD_QUERY: EQTYPE={EQUIPMENT_TYPE} ORIGIN={ORIGIN} DEST={DESTINATION}")
    print(f"Target: {tms_client.host}:{tms_client.port} (retries={tms_client.max_retries})")
    print("-" * 60)

    try:
        loads = await tms_client.search_loads(EQUIPMENT_TYPE, ORIGIN, DESTINATION)
    except TMSUnavailableError as exc:
        print(f"UNAVAILABLE after retries exhausted: {exc}")
        return 1
    except TMSProtocolError as exc:
        print(f"PROTOCOL ERROR (real domain answer, not a transport fault): {exc.code} — {exc.message}")
        return 1

    print(f"Got {len(loads)} load(s) back:\n")
    for load in loads:
        print(f"  {load.load_id}  {load.equipment_type}")
        print(f"    origin:      {load.origin.city}, {load.origin.state} {load.origin.zip or ''}".rstrip())
        print(f"    destination: {load.destination.city}, {load.destination.state} {load.destination.zip or ''}".rstrip())
        print()

    if not loads:
        print("Zero results.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))