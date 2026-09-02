"""Fuse the five ring signals into one explained score.

Two levels, and the second is the entire differentiation claim:

    structural_evidence = fuse(shape, timing, names, money)

A ring must be able to score high while every one of its individual payments
looks ordinary. That is precisely what a transaction-level model cannot do,
and it is asserted as a test rather than claimed in a slide - see
test_ring_fires_below_transaction_threshold in test_pipeline.py.
"""

from __future__ import annotations

from .fusion import explain, fuse
from .signals import identity, money, structural, tenure, velocity
from .types import Ring

# Hand-set, tuned by tune.py against a false-positive ceiling on the tune
# split. Money and names carry the most weight because they are the hardest
# to produce innocently; shape carries the least because franchises and
# aggregators are legitimately dense.
WEIGHTS = {
    "money": 0.78,
    "identity": 0.72,
    "velocity": 0.60,
    "tenure": 0.58,
    "structural": 0.52,
}


def signals_for(ring: Ring, merchants, txns_by_merchant, portfolio):
    """Each signal returns a Detection, or None when it has nothing to judge."""
    return {
        "structural": structural.detect(ring),
        "velocity": velocity.detect(ring, txns_by_merchant, portfolio),
        "identity": identity.detect(ring, merchants),
        "money": money.detect(ring, txns_by_merchant, portfolio),
        "tenure": tenure.detect(ring, merchants),
    }


def score(ring: Ring, merchants, txns_by_merchant, portfolio, *, weights=None):
    """Return (0-100 score, per-signal scores, ranked reasons)."""
    w = weights or WEIGHTS
    dets = signals_for(ring, merchants, txns_by_merchant, portfolio)
    raw = fuse(dets, w)
    reasons = explain(dets, w)
    per_signal = {k: round(d.score, 4) for k, d in dets.items() if d is not None}
    return round(raw * 100), per_signal, reasons
