"""When the accounts were opened - the signal that separates a recruiter from
a group company.

WHY THIS EXISTS. `recruiter_quiet` is a criminal star whose members share the
recruiter's settlement account and device and then trade completely normally.
Shape could not separate it from a marketplace; conduct could not separate it
from a franchise. Its scores sat inside the legitimate band and the README said
plainly that it was unsolvable from the data we modelled.

It was unsolvable from the TRANSACTION data. The missing evidence was never on
the payments side: mules are onboarded together, in days. A real chain opens
outlets over years. Account-creation velocity is a standard industry signal,
and a competing buildathon submission independently trains on
`account_age_days`, which is some reassurance that this is a real feature
rather than one invented so our own planted case would light up.

THE TRAP, AND WHY A TIGHT WINDOW IS NOT GUILT. A brand entering a new city
opens five stores in the same week, on one settlement account, with one phone
number. On the tenure axis alone that is indistinguishable from a mule batch -
and `gen_data.py` plants exactly that as `decoy_simultaneous_launch`. So this
signal reports SYNCHRONY, not suspicion. What stops the honest chain being
flagged is elsewhere: it is dense rather than a star, and the verifier's
"related but unremarkable" check clears dense, quiet groups.

THE NULL MODEL, same discipline as temporal.py. Do not ask "is this window
short" in the abstract. Ask how short it is compared with what n accounts drawn
at random from this portfolio's own signup history would show. For n samples
spread over a range R, the expected spread is R*(n-1)/(n+1). A cluster far
below that opened together.
"""

from __future__ import annotations

from ..types import Detection, Ring

MIN_MEMBERS = 3


def _days(ring: Ring, merchants) -> list[int]:
    out = []
    for m in ring.members:
        raw = (merchants.get(m) or {}).get("signup_day")
        if raw not in (None, ""):
            try:
                out.append(int(raw))
            except ValueError:
                pass
    return out


def portfolio_range(merchants) -> int:
    """How far apart the oldest and newest accounts in the book are."""
    days = []
    for row in merchants.values():
        raw = row.get("signup_day")
        if raw not in (None, ""):
            try:
                days.append(int(raw))
            except ValueError:
                pass
    return (max(days) - min(days)) if len(days) > 1 else 0


def detect(ring: Ring, merchants) -> Detection | None:
    """None when the accounts carry no signup dates, or there are too few.

    Returning None rather than 0.0 matters here: a book without onboarding
    data has not been checked and found innocent, it simply cannot be checked,
    and fusion must not treat that as evidence of safety.
    """
    days = _days(ring, merchants)
    if len(days) < MIN_MEMBERS:
        return None

    span = portfolio_range(merchants)
    if span <= 0:
        return None

    n = len(days)
    spread = max(days) - min(days)
    expected = span * (n - 1) / (n + 1)
    if expected <= 0:
        return None

    ratio = spread / expected            # 1.0 = exactly as spread out as chance
    score = max(0.0, min(1.0, 1.0 - ratio * 3.0))

    reasons = []
    if ratio <= 0.25:
        newest = max(days)
        reasons.append(
            f"All {n} accounts were opened within {spread} days of each other, "
            f"against {expected:.0f} days expected for {n} accounts picked at "
            f"random from this book. They were onboarded as a batch"
            + (f", the newest {abs(newest)} days before the window opened."
               if newest < 0 else "."))
        reasons.append(
            "A batch signup is not an offence on its own - a chain opening "
            "several outlets at once looks the same. It is evidence only "
            "alongside what these accounts share and how they behave.")
    return Detection(score, reasons)
