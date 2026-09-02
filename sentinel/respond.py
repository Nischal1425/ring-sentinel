"""Bounded, gated, reversible responses - and an append-only record of each.

What this module CANNOT do is the point. There is no freeze, no block, no
account closure, no reserve seizure. The ladder tops out at moving a
settlement from T+2 to T+3, which auto-expires in 24 hours unless a human
confirms it, and which one click reverses.

Against the live Razorpay test-mode API the responder is handed exactly one
verb - PATCH /v1/payments/:id, writing a verdict into `notes` (15 keys, 256
chars each). It holds no capture, no refund, no transfer and no payout method.
It is incapable of moving money: an architectural property, not a policy
promise, and test_pipeline.py asserts the client exposes nothing else.

Every gate below is enforced in code, and the global daily cap is a circuit
breaker rather than a limit: if the system wants to hold more than the cap in
one day, the correct conclusion is that the detector broke, not that fraud
spiked. So it stops and pages a human.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .types import Action, Verdict

AUDIT = Path(__file__).resolve().parent.parent / "data" / "audit.jsonl"
STATE = Path(__file__).resolve().parent.parent / "data" / "run_state.json"

WATCH_AT = 50
REVIEW_AT = 70   # tune.py, on the tune half
HOLD_AT = 85

MIN_ATTR_TYPES = 2              # two independent kinds of evidence, or no hold
PER_RING_CAP = 25_00_000_00     # paise a single ring may have delayed
DAILY_CAP = 2_00_00_000_00      # circuit breaker across all rings, per run
HOLD_HOURS = 24
HYSTERESIS_RUNS = 2             # consecutive runs above threshold before a hold


def _load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def _streak(state: dict, ring_key: str, above: bool) -> int:
    """Consecutive runs this ring has been above the hold threshold."""
    prior = state.get(ring_key, 0)
    return prior + 1 if above else 0


def ring_key(verdict: Verdict) -> str:
    """Identity that survives ring-id renumbering between runs."""
    return "|".join(sorted(verdict.ring.members))


def decide(verdict: Verdict, exposure_paise: int, state: dict,
           budget_remaining: int) -> tuple[Action, list[str]]:
    """Pick the action, and say which gate stopped it going further."""
    gates: list[str] = []
    score = verdict.score

    if score < WATCH_AT:
        return Action.OBSERVE, []
    if score < REVIEW_AT:
        return Action.WATCH, []
    if score < HOLD_AT:
        return Action.REVIEW, []

    # Everything below here is the only path to touching a settlement.
    attr_types = {k for e in verdict.ring.edges for k in e[3]}
    streak = _streak(state, ring_key(verdict), True)

    if len(verdict.ring) < 3:
        gates.append("ring smaller than 3 members")
    if len(attr_types) < MIN_ATTR_TYPES:
        gates.append(
            f"linked by only {len(attr_types)} attribute type; "
            f"{MIN_ATTR_TYPES} independent types required to delay a settlement")
    if verdict.exonerations:
        gates.append(
            f"{len(verdict.exonerations)} innocent explanation(s) not ruled out")
    if exposure_paise > PER_RING_CAP:
        gates.append("exposure exceeds the per-ring cap")
    if exposure_paise > budget_remaining:
        gates.append("global daily hold budget exhausted - circuit breaker open")
    if streak < HYSTERESIS_RUNS:
        gates.append(
            f"seen above threshold on {streak} of {HYSTERESIS_RUNS} required "
            f"consecutive runs")

    return (Action.REVIEW, gates) if gates else (Action.HOLD_SETTLEMENT, [])


def record(verdict: Verdict, action: Action, exposure_paise: int,
           gates: list[str], *, audit_path: Path | None = None) -> dict:
    """Append one immutable action record. Reversal ships inside the record.

    The expiry and the reversal handle are not a later feature: an action a
    merchant cannot get undone is the thing this system exists to avoid.
    """
    now = datetime.now(timezone.utc)
    entry = {
        "ts": now.isoformat(timespec="seconds"),
        "ring_id": verdict.ring.ring_id,
        "members": list(verdict.ring.members),
        "action": action.value,
        "score": verdict.score,
        "raw_score": verdict.raw_score,
        "signals": verdict.signals,
        "reasons": verdict.reasons,
        "exonerations_considered": verdict.exonerations,
        "gates_that_stopped_escalation": gates,
        "exposure": exposure_paise,
        "expires_at": ((now + timedelta(hours=HOLD_HOURS)).isoformat(timespec="seconds")
                       if action is Action.HOLD_SETTLEMENT else None),
        "reversal": (f"sentinel reverse {verdict.ring.ring_id}"
                     if action is Action.HOLD_SETTLEMENT else None),
    }
    path = audit_path or AUDIT
    path.parent.mkdir(exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def run(verdicts, exposure_by_ring, *, audit_path=None, persist=True):
    """Decide and record for every ring, honouring the daily circuit breaker."""
    state = _load_state()
    budget, entries, new_state = DAILY_CAP, [], {}

    for v in sorted(verdicts, key=lambda x: -x.score):
        exposure = exposure_by_ring.get(v.ring.ring_id, 0)
        action, gates = decide(v, exposure, state, budget)
        if action is Action.HOLD_SETTLEMENT:
            budget -= exposure
        new_state[ring_key(v)] = _streak(state, ring_key(v), v.score >= HOLD_AT)
        entries.append(record(v, action, exposure, gates, audit_path=audit_path))

    if persist:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(new_state), encoding="utf-8")
    return entries, DAILY_CAP - budget
