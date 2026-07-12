# Northstar KPIs — Inbound Carrier Sales Automation

## Purpose
These are the metrics that define whether this system is actually solving
HappyRobot Logistics' stated problems (missed calls, inconsistent negotiation,
manual FMCSA errors, no audit trail, dispatcher burnout) — not just "does the
demo work."

## Primary KPIs

| KPI | Definition | Target | How it's measured |
|---|---|---|---|
| **Call completion rate** | % of calls that reach a terminal state (booked, failed_negotiation, no_match, failed_verification) without erroring out | ≥ 95% | `call_logs.jsonl` — count terminal outcomes vs. total calls started |
| **Verification accuracy** | % of FMCSA verifications that correctly authorize/reject vs. ground truth | 100% | Compare `fmcsa_verified` against known-good/known-bad test MC numbers |
| **OTP bypass rate** | % of calls that reach `search_loads` without a genuine `verified=true` OTP result | 0% | Server-side `otp_verified` in call_tracker (never agent-self-reported) vs. any successful `search_loads` call |
| **Rate ceiling violations** | % of bookings where `agreed_rate` > `max_rate` | 0% | Compare `agreed_rate` in booking against `max_rate` fetched during negotiation (log-only comparison; never exposed to carrier) |
| **Negotiation round compliance** | % of negotiations that stayed within the 3-round cap | 100% | `rounds_negotiated` in call log, must be ≤ 3 |
| **TMS fault resilience** | % of TMS transport faults (timeout/malformed/partial) that were retried and recovered, vs. surfaced as user-facing errors | ≥ 90% recovery on transient faults | `tms_client.diagnostic` logs — count retried-then-succeeded vs. retried-then-failed |
| **Average time to load quote** | Time from `search_loads` call to carrier receiving a rate (via `get_load_detail`) | < 5s | Timestamp delta in call trace |
| **Booking success rate** | % of negotiations that reach `accepted` status that are then successfully booked | ≥ 95% (allowing for legitimate `ALREADY_BOOKED` races) | Compare accepted negotiations vs. successful `book_load` calls |

## Secondary / operational KPIs

| KPI | Definition | Target |
|---|---|---|
| Call audit completeness | % of calls with a non-null server-side trace in call_tracker | 100% |
| Dispatcher escalation rate | % of calls requiring the mocked handoff (`/calls/{id}/handoff`) | Informational — track trend, no target |
| Peak-hour call handling | Calls per minute the system can sustain during simulated Monday-AM / Friday-PM load | TBD — depends on ngrok/deploy tier; document actual ceiling, not a target |

## What's explicitly NOT a KPI
- Raw call volume — more calls isn't better if quality drops
- Negotiation "win rate" for the broker — the system enforces the ceiling correctly regardless of whether that favors the broker or carrier on a given call; the target is *ceiling compliance*, not *margin maximization*
