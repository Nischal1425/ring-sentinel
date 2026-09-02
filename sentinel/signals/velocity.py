"""Timing: coordinated, or just busy at the same time as everyone else?

Every number here is portfolio-relative. Absolute burst detection is the
largest false-positive source in Indian payments: Diwali stocking, payday,
and GST due dates make the whole book spike at once, and a detector using an
absolute baseline flags the entire population on those days.

So the measure is EXCESS concentration - how much of the ring's activity falls
on days the rest of the portfolio was not also busy. A ring that bursts
exactly when everyone bursts scores near zero, which is the correct answer and
is what the decoy_festive class exists to prove.
"""

from __future__ import annotations

from collections import defaultdict

from ..types import Detection, Ring

MIN_TXNS = 12


def detect(ring: Ring, txns_by_merchant, portfolio) -> Detection | None:
    """None when there is too little activity for timing to mean anything."""
    ring_txns = [t for m in ring.members for t in txns_by_merchant.get(m, [])]
    if len(ring_txns) < MIN_TXNS:
        return None

    per_day: dict[int, int] = defaultdict(int)
    actors_per_day: dict[int, set] = defaultdict(set)
    for t in ring_txns:
        per_day[t.day] += 1
        actors_per_day[t.day].add(t.merchant_id)

    total = len(ring_txns)

    # One-sided total-variation distance from the portfolio's own day profile.
    # Only days where the ring is busier than the portfolio contribute.
    excess = sum(max(0.0, count / total - portfolio.day_share(day))
                 for day, count in per_day.items())

    # Synchrony: on the ring's busiest days, how much of the ring moved at once.
    # Coordination means members acting together, not one member acting a lot.
    n = len(ring)
    busiest = sorted(per_day, key=lambda d: -per_day[d])[:5]
    synchrony = (sum(len(actors_per_day[d]) for d in busiest) / (5 * n)) if busiest and n else 0.0

    score = max(0.0, min(1.0, 0.65 * excess + 0.35 * synchrony))

    reasons = []
    if excess >= 0.25:
        peak = max(per_day, key=lambda d: per_day[d])
        reasons.append(
            f"{excess:.0%} of this group's activity falls on days the rest of the "
            f"book was quiet - concentrated on day {peak}, not spread like normal "
            f"trading.")
    if synchrony >= 0.5:
        reasons.append(
            f"On its busiest days {synchrony:.0%} of the {n} accounts transacted "
            f"together. Independent merchants do not share a calendar.")
    if excess < 0.12:
        reasons.append(
            "Activity tracks the portfolio's own seasonal pattern - this burst is "
            "shared with the wider book and is not evidence on its own.")
    return Detection(score, reasons)
