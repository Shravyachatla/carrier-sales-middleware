"""
FMCSA QCMobile API client — verifies active operating authority by MC number.

Docs: https://mobile.fmcsa.dot.gov/QCDevsite/docs/getStarted
Endpoint shape: GET /qc/services/carriers/{mc_number}?webKey={api_key}

NOTE: verify the exact field names/response shape against the FMCSA docs
Christine linked once you've registered a key — the API has a few historical
quirks (e.g. some deployments key off DOT number rather than MC number, and
"allowedToOperate" vs "status" naming has shifted across versions). Adjust
_is_authorized() below if the real payload differs from what's assumed here.
"""

import httpx

from app.config import settings
from app.models import VerifyCarrierResponse

FMCSA_BASE_URL = "https://mobile.fmcsa.dot.gov/qc/services/carriers"


async def verify_carrier(mc_number: str) -> VerifyCarrierResponse:
    mc_number = mc_number.strip().upper().removeprefix("MC").removeprefix("-").strip()

    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            # IMPORTANT: /carriers/{id} looks up by USDOT number, not MC number.
            # MC numbers must go through the docket-number path, or FMCSA will
            # silently match against an unrelated carrier that happens to share
            # that number as their DOT number.
            resp = await client.get(
                f"{FMCSA_BASE_URL}/docket-number/{mc_number}",
                params={"webKey": settings.fmcsa_api_key},
            )
        except httpx.RequestError as exc:
            return VerifyCarrierResponse(authorized=False, reason=f"FMCSA lookup failed: {exc}")

    if resp.status_code == 404:
        return VerifyCarrierResponse(authorized=False, reason="MC number not found")
    if resp.status_code != 200:
        return VerifyCarrierResponse(authorized=False, reason=f"FMCSA API error ({resp.status_code})")

    data = resp.json()
    content = data.get("content")

    # The docket-number (MC) endpoint returns content as a LIST of matches
    # (a docket number can have multiple historical carrier records), unlike
    # the DOT-number endpoint which returns a single object. Handle both.
    if isinstance(content, list):
        carrier = (content[0] or {}).get("carrier") if content else {}
    else:
        carrier = (content or {}).get("carrier") or {}

    if not carrier:
        return VerifyCarrierResponse(authorized=False, reason="No carrier record found")

    authorized = _is_authorized(carrier)
    return VerifyCarrierResponse(
        authorized=authorized,
        carrier_id=str(carrier.get("dotNumber", mc_number)),
        carrier_name=carrier.get("legalName"),
        reason=None if authorized else "Carrier does not have active operating authority",
    )


def _is_authorized(carrier: dict) -> bool:
    # TODO: confirm exact field name/values once a real response is inspected —
    # the QCMobile API has used both `allowedToOperate` (Y/N) and status codes
    # across versions.
    allowed = carrier.get("allowedToOperate")
    if allowed is not None:
        return str(allowed).upper() == "Y"
    status = carrier.get("statusCode") or carrier.get("status")
    return str(status).upper() == "A"
