"""Noisy-OR evidence fusion.

Lifted from D:/Neurons Prototype/src/engine/fusion.py, where the argument was
made for messages and holds verbatim for rings:

    Corroboration should add; silence should not subtract.

These signals are specialists hunting different, non-exclusive kinds of
badness. A weighted mean is wrong because it treats a signal's silence as
evidence of innocence - a refund mill caught at 0.95 by the money signal would
be dragged to the middle merely because the name signal saw nothing odd about
the merchant names, and names are trivially varied.

    raw = 1 - PROD(1 - w_i * s_i)

renormalised by the most the PRESENT signals could have produced. That
renormalisation is what keeps a threshold meaning the same thing whether or
not every signal reported. A signal with nothing to judge returns None and is
dropped; a signal that looked and found nothing returns 0.0 and counts.

Used at two levels here: to weight one graph edge from the attributes two
merchants share, and to fuse the ring-level signals into a risk score.
"""

from __future__ import annotations

from .types import Detection


def fuse(detections: dict[str, Detection | None], weights: dict[str, float],
         *, renormalise: bool = True) -> float:
    """Combine present signals into a single 0.0-1.0 score.

    `renormalise` divides by the most the PRESENT inputs could have produced.
    That is right for RING SIGNALS - a signal with nothing to judge must not
    drag the score down - and wrong for EDGE WEIGHTS.

    It was used for both, and the consequence was severe: with a single input,
    residual and ceiling are identical and the result is always exactly 1.0.
    Every edge in the graph scored 1.000, so a shared free-mail domain weighted
    0.12 was indistinguishable from a shared settlement account weighted 0.92,
    the whole ATTR_WEIGHTS table was decorative, and EDGE_MIN pruned nothing.
    A coincidental IP-prefix collision was enough to fuse a criminal ring to a
    franchise chain into one 13-member blob.

    For edges, pass renormalise=False and take the raw noisy-OR, where a
    strong attribute yields a strong edge and a weak one does not.
    """
    present = [(weights.get(name, 0.0), d.score)
               for name, d in detections.items() if d is not None]
    present = [(w, s) for w, s in present if w > 0.0]
    if not present:
        return 0.0

    residual = 1.0   # probability that no signal fired
    ceiling = 1.0    # the same, if every present signal were fully certain
    for weight, score in present:
        residual *= 1.0 - weight * score
        ceiling *= 1.0 - weight

    if not renormalise:
        return min(1.0, 1.0 - residual)
    if ceiling >= 1.0:
        return 0.0
    return min(1.0, (1.0 - residual) / (1.0 - ceiling))


def explain(detections: dict[str, Detection | None], weights: dict[str, float],
            limit: int = 6) -> list[str]:
    """Top reasons, interleaved across signals, most significant first.

    Interleaved rather than drained one signal at a time: the money signal
    alone can produce four sentences about refund velocity, which would fill
    the list and leave a name-collision finding unmentioned. Breadth of
    evidence tells a reviewer more than four variations on one point.
    """
    ranked = sorted(
        ((n, d) for n, d in detections.items() if d is not None),
        key=lambda kv: weights.get(kv[0], 0.0) * kv[1].score,
        reverse=True,
    )
    buckets = [d.reasons for _, d in ranked]
    out: list[str] = []
    for rank in range(max((len(b) for b in buckets), default=0)):
        out += [b[rank] for b in buckets if rank < len(b)]

    seen: set[str] = set()
    return [r for r in out if not (r in seen or seen.add(r))][:limit]
