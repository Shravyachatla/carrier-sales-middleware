"""
Legacy TMS adapter — built against the real protocol spec (HR-LTMS-PR-001,
FORM-9100 REV 1.0).

Protocol summary:
- Raw TCP, ASCII, one request per connection (fresh connection each call).
- Request:  CMD:<command>|AUTH:<token>|<FIELD>:<VALUE>|...\r\n
- Response (success): zero or more "<FIELD>:<VALUE>|..." record lines,
  terminated by a line reading exactly "END".
- Response (error): a single line "ERR|CODE:<code>|MSG:<msg>".
- Field values are space-padded on the right; we .rstrip() everything, which
  also handles the documented case of a blank NOTES field collapsing to "".
- Faults are NOT signaled — no fault code, no marker. Categories observed:
  timeout (no response at all), partial response (valid prefix, no END),
  malformed response (broken framing), delayed termination (valid response,
  connection held open past when the client expects to close). We treat
  "closed/EOF before END and before an ERR line" as a soft fault and retry;
  a fully-parsed response is accepted immediately without waiting for the
  server to close the socket (handles delayed termination without hanging).

DIAGNOSTIC INSTRUMENTATION (temporary — see _diag logger below):
Added to investigate a bug where live-agent calls reproducibly hit
"MISSING_FIELD: Invalid EQTYPE" on search_loads (every attempt, all retries
exhausted) despite identical parameters succeeding 5/5 in direct script
tests, and despite this happening both during AND after the HappyRobot
Voice incident (so that incident is now considered an unlikely cause).
Leading hypothesis: the TMS server is a single-connection socket server,
and something (possibly HappyRobot's own tool-node timeout/retry firing a
duplicate call before the first response lands) is causing two+ requests
to hit the TMS in close succession, with responses getting cross-talked
or mixed. This instrumentation makes that directly observable: every
attempt gets a short attempt_id, a millisecond timestamp, and we track how
many requests are simultaneously "in flight" (connection open, awaiting
response) per command. If two attempts for the same command are ever in
flight at once, `_diag` logs a WARNING at the moment of collision. Once
the real cause is confirmed, strip this section back out (it's fenced off
in `_diag`-prefixed lines and the `_InflightTracker` class so it's easy to
find/remove without touching the actual protocol logic).
"""

import asyncio
import logging
import time
import uuid

from app.config import settings
from app.models import LoadDetail, LoadLocation, LoadSummary

logger = logging.getLogger("tms_client")
_diag = logging.getLogger("tms_client.diagnostic")

MAX_FRAME_BYTES = 4096


class TMSUnavailableError(Exception):
    """Transport-level failure after retries exhausted (timeout / malformed / connection error)."""


class TMSProtocolError(Exception):
    """A well-formed ERR response from the TMS — a real domain answer, not a transport fault."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class _TransportFault(Exception):
    """Internal signal for a single attempt's soft failure — triggers retry, never surfaced directly."""


class _InflightTracker:
    """DIAGNOSTIC ONLY. Tracks how many requests are currently open per TMS
    command, so we can catch/log the moment two attempts for the same
    command overlap in time — direct evidence for or against the "duplicate
    concurrent request" hypothesis. Safe under asyncio's single-threaded
    cooperative scheduling (no lock needed; increments/decrements happen
    without an intervening await)."""

    def __init__(self) -> None:
        self._active: dict[str, list[str]] = {}

    def enter(self, command: str, attempt_id: str) -> list[str]:
        others = list(self._active.get(command, []))
        self._active.setdefault(command, []).append(attempt_id)
        return others  # attempt_ids already in flight for this command, if any

    def exit(self, command: str, attempt_id: str) -> None:
        ids = self._active.get(command)
        if ids and attempt_id in ids:
            ids.remove(attempt_id)


_inflight = _InflightTracker()


class TMSClient:
    def __init__(self) -> None:
        self.host = settings.tms_host
        self.port = settings.tms_port
        self.token = settings.tms_auth_token
        self.timeout = settings.tms_timeout_seconds
        self.max_retries = settings.tms_max_retries

    # ---- public operations ----

    async def health_check(self) -> bool:
        """DEBUG_ECHO bypasses fault injection — good for a liveness probe."""
        try:
            fields = await self._execute("DEBUG_ECHO", {"MSG": "health"})
            return fields.get("MSG") == "health"
        except Exception:
            return False

    async def search_loads(
        self, equipment_type: str, origin: str | None, destination: str | None
    ) -> list[LoadSummary]:
        # Defensive strip: an LLM-generated tool-call argument can include a
        # stray trailing/leading space invisible in a JSON viewer. The real
        # TMS's parser may reject "DRY_VAN " as an invalid EQTYPE even though
        # it displays identically to "DRY_VAN" — strip before it ever reaches
        # the wire, rather than assuming caller input is already clean.
        fields: dict[str, str] = {"EQTYPE": equipment_type.strip().upper()}
        if origin:
            fields.update(self._location_fields("ORIG", origin.strip()))
        if destination:
            fields.update(self._location_fields("DEST", destination.strip()))

        records = await self._execute_multi("LOAD_QUERY", fields)
        return [self._parse_summary(r) for r in records]

    async def get_load_detail(self, load_id: str) -> tuple[LoadDetail, float | None]:
        """Returns (detail, max_rate). max_rate is MAX_BUY — present only on
        tokens flagged for it. Never forward it anywhere carrier-facing."""
        records = await self._execute_multi("LOAD_GET", {"LOAD_ID": load_id})
        if not records:
            raise TMSProtocolError("UNKNOWN_LOAD", "load not found")
        return self._parse_detail(records[0])

    async def book_load(self, load_id: str, carrier_mc: str, agreed_rate: float) -> dict:
        """Returns {booking_id, status, timestamp}."""
        fields = {
            "LOAD_ID": load_id,
            "MC_NUM": carrier_mc,
            "AGREED_RATE": str(agreed_rate),
        }
        records = await self._execute_multi("LOAD_BOOK", fields)
        if not records:
            raise TMSUnavailableError("Booking confirmation missing from response")
        record = records[0]
        return {
            "booking_id": record.get("BOOKING_REF", ""),
            "status": record.get("STATUS", ""),
            "timestamp": record.get("TIMESTAMP", ""),
        }

    # ---- field <-> model mapping ----

    def _location_fields(self, prefix: str, value: str) -> dict[str, str]:
        """Origin/destination accept city, state, or ZIP. Heuristic: 2 letters ->
        state, 5 digits -> ZIP, else city. Confirm against real behavior once
        connected — the spec doesn't pin down disambiguation beyond this."""
        v = value.strip()
        if len(v) == 2 and v.isalpha():
            return {f"{prefix}_STATE": v.upper()}
        if v.isdigit() and len(v) == 5:
            return {f"{prefix}_ZIP": v}
        return {f"{prefix}_CITY": v}

    def _parse_summary(self, record: dict[str, str]) -> LoadSummary:
        return LoadSummary(
            load_id=record.get("LOAD_ID", ""),
            origin=LoadLocation(
                city=record.get("ORIG_CITY"),
                state=record.get("ORIG_STATE"),
                zip=record.get("ORIG_ZIP"),
            ),
            destination=LoadLocation(
                city=record.get("DEST_CITY"),
                state=record.get("DEST_STATE"),
                zip=record.get("DEST_ZIP"),
            ),
            equipment_type=record.get("EQTYPE"),
        )

    def _parse_detail(self, record: dict[str, str]) -> tuple[LoadDetail, float | None]:
        summary = self._parse_summary(record)
        known_keys = {
            "LOAD_ID", "ORIG_CITY", "ORIG_STATE", "ORIG_ZIP",
            "DEST_CITY", "DEST_STATE", "DEST_ZIP", "EQTYPE",
            "RATE", "MAX_BUY", "COMMODITY", "DIMS", "NOTES",
            "STATUS", "MILES",
        }
        max_rate_raw = record.get("MAX_BUY")
        max_rate = float(max_rate_raw) if max_rate_raw else None
        if max_rate is None:
            # Per spec: MAX_BUY is absent on tokens not flagged for it.
            # Falling back to the posted rate as a conservative ceiling —
            # never pay more than what's listed — but this should be
            # confirmed once we know which behavior our real token has.
            logger.warning("MAX_BUY absent for load %s — falling back to RATE as ceiling", summary.load_id)

        rate_raw = record.get("RATE")
        loadboard_rate = float(rate_raw) if rate_raw else None

        miles_raw = record.get("MILES")
        try:
            miles = int(miles_raw) if miles_raw else None
        except ValueError:
            logger.warning("MILES field %r for load %s is not an integer — leaving unset", miles_raw, summary.load_id)
            miles = None

        detail = LoadDetail(
            **summary.model_dump(),
            loadboard_rate=loadboard_rate,
            commodity_type=record.get("COMMODITY"),
            dimensions=record.get("DIMS"),
            notes=record.get("NOTES"),
            status=record.get("STATUS"),
            miles=miles,
            raw_fields={k: v for k, v in record.items() if k not in known_keys},
        )
        return detail, (max_rate if max_rate is not None else loadboard_rate)

    # ---- protocol encode/decode ----

    def _encode_request(self, command: str, fields: dict[str, str]) -> bytes:
        parts = [f"CMD:{command}", f"AUTH:{self.token}"]
        for key, value in fields.items():
            if "|" in str(value) or "\r" in str(value) or "\n" in str(value):
                raise ValueError(f"Field {key} contains a disallowed character (| or CRLF)")
            parts.append(f"{key}:{value}")
        line = "|".join(parts) + "\r\n"
        encoded = line.encode("ascii")
        if len(encoded) > MAX_FRAME_BYTES:
            raise ValueError("Request exceeds max frame size")
        return encoded

    def _parse_line(self, line: str) -> dict[str, str]:
        """Split a KEY:VALUE|KEY:VALUE... line into a dict, stripping trailing padding."""
        fields: dict[str, str] = {}
        for part in line.split("|"):
            if ":" not in part:
                raise _TransportFault(f"Malformed field (no ':' separator): {part!r}")
            key, _, value = part.partition(":")
            fields[key.strip()] = value.rstrip()
        return fields

    # ---- execution: single call site, retry/backoff, fault handling ----

    async def _execute(self, command: str, fields: dict[str, str]) -> dict[str, str]:
        """For commands with exactly one meaningful response line (DEBUG_ECHO, LOAD_BOOK)."""
        records = await self._execute_multi(command, fields, single=True)
        return records[0] if records else {}

    # Error codes we've confirmed, through direct real-server testing, are
    # genuine business responses (not fault injection): booking/rate rejections
    # that came back consistently and meaningfully. Anything else — including
    # a code that LOOKS like a legitimate validation error — gets treated as a
    # possible fault and retried, because we've directly observed the real TMS
    # return "MISSING_FIELD: Invalid EQTYPE" for a request whose EQTYPE was
    # verified correct (same value succeeded moments later with no change).
    # The spec's own warning that fault injection is "undetectable/unsignaled"
    # apparently extends to ERR responses themselves, not just malformed/
    # timeout/partial transport behavior.
    _TRUSTED_ERROR_CODES = {"ALREADY_BOOKED", "INVALID_RATE", "UNKNOWN_LOAD"}

    async def _execute_multi(
        self, command: str, fields: dict[str, str], single: bool = False
    ) -> list[dict[str, str]]:
        request = self._encode_request(command, fields)
        last_error: Exception | None = None

        # DIAGNOSTIC: one id per logical call (shared across its retry attempts)
        # so a grep for this id shows the whole retry sequence for one caller.
        call_diag_id = uuid.uuid4().hex[:8]
        _diag.info(
            "[%s] %s call started fields=%s at t=%.3f",
            call_diag_id, command, {k: v for k, v in fields.items() if k != "AUTH"}, time.time(),
        )

        for attempt in range(self.max_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._send_once(request, single, command, call_diag_id, attempt),
                    timeout=self.timeout,
                )
            except TMSProtocolError as exc:
                if exc.code in self._TRUSTED_ERROR_CODES:
                    raise  # confirmed genuine domain answer — never retry, never swallow
                last_error = exc
                wait = 0.5 * (2 ** attempt)
                logger.warning(
                    "TMS %s attempt %d/%d got unrecognized ERR %s (%s) — treating as "
                    "possible fault injection, retrying in %.1fs",
                    command, attempt + 1, self.max_retries + 1, exc.code, exc.message, wait,
                )
                _diag.warning(
                    "[%s] attempt %d/%d ERR code=%s msg=%s at t=%.3f",
                    call_diag_id, attempt + 1, self.max_retries + 1, exc.code, exc.message, time.time(),
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(wait)
            except (asyncio.TimeoutError, ConnectionError, OSError, _TransportFault) as exc:
                last_error = exc
                wait = 0.5 * (2 ** attempt)
                logger.warning(
                    "TMS %s attempt %d/%d failed (%s), retrying in %.1fs",
                    command, attempt + 1, self.max_retries + 1, exc, wait,
                )
                _diag.warning(
                    "[%s] attempt %d/%d transport fault=%s at t=%.3f",
                    call_diag_id, attempt + 1, self.max_retries + 1, exc, time.time(),
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(wait)

        _diag.error(
            "[%s] %s exhausted all retries, last_error=%s at t=%.3f",
            call_diag_id, command, last_error, time.time(),
        )

        if isinstance(last_error, TMSProtocolError):
            raise last_error  # exhausted retries on a persistent unrecognized ERR — surface it
        raise TMSUnavailableError(
            f"TMS {command} unreachable/unusable after {self.max_retries + 1} attempts"
        ) from last_error

    async def _send_once(
        self, request: bytes, single: bool, command: str = "?", call_diag_id: str = "", attempt: int = 0
    ) -> list[dict[str, str]]:
        # DIAGNOSTIC: unique id for this specific socket attempt, plus
        # in-flight tracking to directly catch overlapping requests to the
        # same command hitting the TMS's single-connection socket at once.
        attempt_diag_id = f"{call_diag_id}-{attempt}"
        others_in_flight = _inflight.enter(command, attempt_diag_id)
        t_open = time.time()
        if others_in_flight:
            _diag.warning(
                "[%s] COLLISION: opening connection for %s while attempt(s) %s are still "
                "in flight for the same command — possible duplicate/concurrent delivery "
                "hitting the single-connection TMS socket at t=%.3f",
                attempt_diag_id, command, others_in_flight, t_open,
            )
        else:
            _diag.info("[%s] opening connection for %s at t=%.3f", attempt_diag_id, command, t_open)

        try:
            reader, writer = await asyncio.open_connection(self.host, self.port)
            try:
                writer.write(request)
                await writer.drain()
                _diag.debug("[%s] request bytes sent: %r", attempt_diag_id, request)

                first_raw = await reader.readline()
                if not first_raw:
                    _diag.warning(
                        "[%s] connection closed with no data at t=%.3f (elapsed %.3fs)",
                        attempt_diag_id, time.time(), time.time() - t_open,
                    )
                    raise _TransportFault("Connection closed with no data (timeout-style fault)")
                first_line = first_raw.decode("ascii", errors="replace").rstrip("\r\n")
                _diag.debug(
                    "[%s] first line received: %r at t=%.3f (elapsed %.3fs)",
                    attempt_diag_id, first_line, time.time(), time.time() - t_open,
                )

                if first_line.startswith("ERR|") or first_line.startswith("ERR"):
                    err_fields = self._parse_line(first_line[len("ERR"):].lstrip("|"))
                    raise TMSProtocolError(
                        err_fields.get("CODE", "UNKNOWN"), err_fields.get("MSG", "no message")
                    )

                if first_line == "END":
                    # Zero-result response: END arrived as the very first line.
                    # Nothing more to read — don't fall into the loop below,
                    # which would wait forever (or fault) expecting a second END.
                    try:
                        await asyncio.wait_for(reader.readline(), timeout=0.1)
                    except (asyncio.TimeoutError, Exception):
                        pass
                    return []

                records = [self._parse_line(first_line)] if first_line else []

                if single:
                    # DEBUG_ECHO / LOAD_BOOK: one record line then END expected,
                    # but we don't need to block waiting for END/close — we have
                    # what we need. Still drain the END line if it arrives promptly
                    # so the fault categories (partial/delayed) don't leak into
                    # the next connection's read.
                    try:
                        await asyncio.wait_for(reader.readline(), timeout=0.5)
                    except (asyncio.TimeoutError, Exception):
                        pass
                    return records

                while True:
                    raw = await reader.readline()
                    if not raw:
                        # Connection closed before END — partial response fault.
                        raise _TransportFault("Connection closed before END terminator (partial response)")
                    line = raw.decode("ascii", errors="replace").rstrip("\r\n")
                    if line == "END":
                        break
                    if not line:
                        continue
                    records.append(self._parse_line(line))

                return records
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
        finally:
            _inflight.exit(command, attempt_diag_id)
            _diag.info("[%s] connection closed, total elapsed %.3fs", attempt_diag_id, time.time() - t_open)


tms_client = TMSClient()
