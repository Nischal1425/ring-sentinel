"""Temporal co-occurrence edges: catching rings that share nothing but a clock.

THE BLIND SPOT THIS CLOSES. A shared-attribute graph cannot represent a ring
whose members share no bank account, no device, no handle - only timing. We
plant exactly such rings in gen_data.py and the attribute graph scores them
0/3, correctly and by construction. This module is the second edge type that
makes them visible.

THE TRAP, AND WHY NAIVE CO-OCCURRENCE FAILS. Everybody transacts on payday.
Everybody transacts before Diwali. Count raw "days both were active" and the
busiest, most legitimate merchants top the list, because popularity dominates
coordination. Any burst detector without a null model flags the whole book on
1 November.

THE NULL MODEL. Compare observed co-activity against what CHANCE would produce
for these two specific entities on these specific days. For entity a, let

    q_a(d)  =  n_a * p(d)

where n_a is how many days a was active at all and p(d) is the portfolio's own
share of activity on day d. So a busy entity is expected to appear often, and
every entity is expected to appear more on days the whole book was busy.

    observed_ab  = sum_d  1[a active on d] * 1[b active on d]
    expected_ab  = sum_d  q_a(d) * q_b(d)
    var_ab       = sum_d  q_a(d)q_b(d) * (1 - q_a(d)q_b(d))     Poisson-binomial
    z_ab         = (observed - expected) / sqrt(var)

Two merchants who both trade heavily through the festive season have a large
observed AND a large expected, so z stays near zero - which is the correct
answer, and is what makes the decoy_festive class survive this. Two dormant
accounts that wake up on the same nine scattered days have a tiny expected and
a large z.

This is the statistical form of the lockstep-behaviour idea from the
coordinated-account literature (SynchroTrap, CopyCatch): coordination is
co-occurrence in excess of a popularity-aware baseline, not co-occurrence.

COST. One (entities x days) matrix multiply. At 1,451 x 184 that is a 1451^2
result, ~16 MB and milliseconds. ponytail: dense numpy, no sparse machinery,
until the book outgrows memory - at ~50k entities this needs blocking or a
minhash/LSH candidate pass first.
"""

from __future__ import annotations

import numpy as np

# Chosen from the TUNE half, and chosen as the MIDPOINT of the gap rather than
# hugging the boundary. On the tune half the worst legitimate pair scores 3.33
# and the weakest real coordinated pair scores 12.43 - a nine-sigma void with
# nothing in it. Setting the floor at ceil(worst legitimate) = 4.0 is defensible
# and was what we shipped; it then failed on held-out data where a festive pair
# reached 4.16. A threshold with no margin is a threshold fitted to one sample.
# The midpoint holds on both halves with room on either side.
Z_MIN = 8.0          # standard deviations above chance before we draw an edge
MIN_ACTIVE_DAYS = 3  # an entity seen on 1-2 days has no rhythm to compare
MAX_DEGREE = 12      # keep the densest nodes from dominating


def _activity_matrix(members, txns_by_merchant):
    """(entities x days) 0/1 - was this entity active on this day at all.

    Binary rather than counts on purpose: one merchant sending 400 payments in
    a day should not outweigh six merchants each sending one on the same day.
    Coordination is about WHO moves together, not volume.
    """
    days = [t.day for m in members for t in txns_by_merchant.get(m, [])]
    if not days:
        return np.zeros((len(members), 1), dtype=np.float64), 0
    span = max(days) + 1
    A = np.zeros((len(members), span), dtype=np.float64)
    for i, m in enumerate(members):
        for t in txns_by_merchant.get(m, []):
            A[i, t.day] = 1.0
    return A, span


def cooccurrence_edges(members, txns_by_merchant, *, z_min=Z_MIN,
                       max_degree=MAX_DEGREE):
    """Entity pairs whose shared activity days exceed chance. Returns edges."""
    members = list(members)
    n = len(members)
    if n < 3:
        return []

    A, span = _activity_matrix(members, txns_by_merchant)
    if span < 2:
        return []

    active_days = A.sum(axis=1)                       # n_a per entity
    day_pop = A.sum(axis=0)
    total = day_pop.sum()
    if total <= 0:
        return []
    p_day = day_pop / total                           # p(d)

    # q[a, d] = expected chance of a being active on d, popularity-aware.
    # Clipped to 1.0: a probability cannot exceed one, and without the clip a
    # very active entity on a very busy day produces q > 1 and a negative
    # variance term.
    q = np.clip(np.outer(active_days, p_day), 0.0, 1.0)

    observed = A @ A.T
    expected = q @ q.T
    qq = q * q
    # var = sum_d q_a q_b (1 - q_a q_b); the second term is bounded by the
    # same matrix product with the squared entries.
    var = expected - (qq @ qq.T)
    np.fill_diagonal(observed, 0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(var > 1e-9, (observed - expected) / np.sqrt(np.maximum(var, 1e-9)), 0.0)
    np.fill_diagonal(z, 0.0)

    too_quiet = active_days < MIN_ACTIVE_DAYS
    z[too_quiet, :] = 0.0
    z[:, too_quiet] = 0.0

    edges = []
    for i in range(n):
        row = z[i]
        # Keep only this node's strongest partners. A node linked to everyone
        # is a popularity artifact the null model failed to absorb, not a ring.
        cand = np.argsort(row)[::-1][:max_degree]
        for j in cand:
            if j <= i or row[j] < z_min:
                continue
            shared = int(observed[i, j])
            exp = expected[i, j]
            # Weight saturates: 10 sigma is not twice as damning as 5.
            weight = float(min(1.0, (row[j] - z_min) / (3.0 * z_min) + 0.55))
            edges.append((members[i], members[j], weight, ("co_timing",),
                          shared, float(exp)))
    return edges


def describe(edges, members):
    """One plain-language sentence for the case file, or None."""
    inner = [e for e in edges if e[0] in members and e[1] in members]
    if not inner:
        return None
    shared = max(e[4] for e in inner)
    expected = min(e[5] for e in inner)
    return (f"{len(inner)} pairs in this group were active on the same days far "
            f"more often than chance allows - up to {shared} shared days where "
            f"the portfolio's own rhythm predicts {expected:.1f}. They share no "
            f"account, device or handle; only a calendar.")
