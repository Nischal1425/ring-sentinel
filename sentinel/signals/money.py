"""What the money does: structuring, refund mills, chargeback concentration.

Every rate is a Wilson lower bound against the portfolio base rate, never a
raw ratio. A three-member ring with one chargeback out of one transaction has
a raw chargeback rate of 100%; the Wilson lower bound at 95% is about 20%,
which is the number an analyst should actually be shown. Rates computed on
small denominators are the most common way a fraud scorecard lies to itself,
and this is one line of arithmetic to stop it.

ponytail: no Benford test. It needs n >= 50 and a wide price range to mean
anything, it would have to abstain on most rings, and the structuring share
below already catches the amount-shaping class it would duplicate. Add it if
a ring class shows up that shapes amounts without clustering them.
"""

from __future__ import annotations

from collections import Counter
from math import sqrt

from ..types import Detection, Ring

# Two floors, because the components need different amounts of evidence.
# A RATE RATIO compares against a portfolio base rate and is meaningless on a
# handful of rows even with a Wilson bound. A SHARE is a description of the
# ring's own payments and is readable much sooner: "every one of these 8
# payments is a rupee under fifty thousand" is a real observation, not noise.
#
# Found by running against the live Razorpay test account: a real structuring
# ring of 8 orders scored 21 and was missed, because one floor of 12 silenced
# the whole signal. The synthetic rings all had hundreds of payments, so the
# offline scorecard never showed it.
MIN_TXNS = 6            # below this the signal abstains entirely
MIN_TXNS_FOR_RATES = 12  # below this, only the shares are reported
Z = 1.96  # 95%

# Amounts parked just below a reporting threshold. The threshold itself is a
# round number by construction; what matters is the shape of approach to it.
THRESHOLDS = (50_000_00, 2_00_000_00, 10_00_000_00)
BAND = 0.04  # "just under" means within 4%


def wilson_lower(successes: int, n: int, z: float = Z) -> float:
    """Lower bound of the 95% Wilson interval for a proportion."""
    if n == 0:
        return 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


def _rate_lift(successes: int, n: int, base: float) -> float:
    """How many times the portfolio base rate, using the conservative bound."""
    if base <= 0:
        return 0.0
    return wilson_lower(successes, n) / base


def detect(ring: Ring, txns_by_merchant, portfolio) -> Detection | None:
    """None when the ring has too little money movement to judge."""
    txns = [t for m in ring.members for t in txns_by_merchant.get(m, [])]
    if len(txns) < MIN_TXNS:
        return None

    n = len(txns)
    refunds = sum(t.is_refund for t in txns)
    chargebacks = sum(t.is_chargeback for t in txns)

    rates_ok = n >= MIN_TXNS_FOR_RATES
    refund_lift = _rate_lift(refunds, n, portfolio.refund_rate) if rates_ok else 0.0
    cb_lift = _rate_lift(chargebacks, n, portfolio.chargeback_rate) if rates_ok else 0.0

    just_under = sum(
        1 for t in txns
        if any(thresh * (1 - BAND) <= t.amount < thresh for thresh in THRESHOLDS)
    )
    structuring = just_under / n

    # A handful of amounts repeated endlessly is machine behaviour, not trade.
    modal_share = Counter(t.amount for t in txns).most_common(1)[0][1] / n

    score = min(1.0, max(
        min(refund_lift / 6.0, 1.0),
        min(cb_lift / 6.0, 1.0),
        min(structuring * 2.2, 1.0),
        min(max(0.0, modal_share - 0.10) * 3.0, 1.0),
    ))

    reasons = []
    if refund_lift >= 2.0:
        reasons.append(
            f"Refunds run {refund_lift:.1f}x the portfolio rate "
            f"({refunds} of {n} payments, lower-bound estimate) - money arrives "
            f"and leaves again rather than being earned.")
    if cb_lift >= 2.0:
        reasons.append(
            f"Chargebacks run {cb_lift:.1f}x the portfolio rate "
            f"({chargebacks} of {n}, lower-bound estimate).")
    if structuring >= 0.20:
        reasons.append(
            f"{structuring:.0%} of payments sit just below a reporting threshold. "
            f"Genuine ticket sizes do not cluster under round numbers.")
    if modal_share >= 0.20:
        reasons.append(
            f"A single amount accounts for {modal_share:.0%} of all payments.")
    if not rates_ok and reasons:
        reasons.append(
            f"Caveat: only {n} payments here, so refund and chargeback rates "
            f"are NOT part of this score - too few to compare against the "
            f"portfolio without inventing precision.")
    return Detection(score, reasons)
