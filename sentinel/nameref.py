"""Name-similarity edges: a third relation, for rings joined by nothing else.

WHY. Fixing the edge-weight bug (see fusion.fuse) made every attribute weight
real for the first time, and a shared IP prefix correctly dropped to 0.45 -
below the pruning threshold the sweep then chose. That was right: coincidental
IP collisions had been fusing unrelated groups into blobs. But it silently
deleted the `name_twins` class, whose members share an IP and nothing else,
from the graph entirely. 5/5 to 0/5.

The answer is not to re-admit weak infrastructure edges. It is that a
near-identical NAME is itself a link. `SRI ELECTRONICS` and `SRl ELECTRONICS`
are related, and the relation does not depend on any attribute they share.

So this is a third relation alongside shared attributes and temporal
co-occurrence, and like co-timing it runs as a SECOND PASS over entities the
attribute graph could not connect - circumstantial evidence is never allowed to
reshape clusters built from settlement accounts.

WHAT COUNTS AS SIMILAR, and what deliberately does not.

An EXACT duplicate is excluded, and that exclusion is the whole thing working.
The first version linked identical names and was a disaster: six decoy false
positives, thirteen merged clusters, two ring classes destroyed. The cause was
plain once measured - two unrelated merchants sharing a name is a coincidence,
and on any real book it is a common one.

A NEAR-miss is different. Nobody arrives at `SRl ELECTRONICS` next to `SRI
ELECTRONICS` by accident; the whole value of the name is that it is almost
another one. So: same folded skeleton, DIFFERENT raw spelling.

A shared BRAND is excluded too - `SHREE TEXTILES BLR` and `SHREE TEXTILES DEL`
fold to different skeletons and are a chain advertising itself, which
verify._branded_chain exonerates. Deception is near-collision; disclosure is a
common prefix with distinct suffixes.
"""

from __future__ import annotations

from collections import defaultdict

from .signals.identity import MAX_EDITS, levenshtein, skeleton

MIN_NAME_LEN = 8      # short names collide by chance
MAX_DEGREE = 8        # a name everybody shares is a word, not a link
WEIGHT = 0.72         # below a settlement account, above an IP prefix


def name_edges(members, merchants, *, max_degree=MAX_DEGREE):
    """Pairs whose names are near-identical once lookalikes are folded."""
    named = []
    for m in members:
        raw = (merchants.get(m) or {}).get("name", "").strip()
        if len(raw) >= MIN_NAME_LEN:
            named.append((m, raw, skeleton(raw)))
    if len(named) < 3:
        return []

    # Bucket by skeleton first so this is not O(n^2) over the whole book: an
    # exact skeleton collision is the common case, and the edit-distance pass
    # only runs inside a bucket's neighbourhood.
    buckets = defaultdict(list)
    for m, raw, sk in named:
        buckets[sk].append((m, raw))

    edges, degree = [], defaultdict(int)
    for sk, group in buckets.items():
        if len(group) < 2 or len(group) > 40:
            continue
        for i, (a, ra) in enumerate(group):
            for b, rb in group[i + 1:]:
                if ra == rb:
                    continue          # identical names are coincidence, not deception
                if degree[a] >= max_degree or degree[b] >= max_degree:
                    continue
                degree[a] += 1
                degree[b] += 1
                edges.append((a, b, WEIGHT, ("name_twin",), ra, rb))

    # Near-misses that fold to different skeletons but are one edit apart.
    keys = sorted(buckets)
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            if abs(len(k1) - len(k2)) > MAX_EDITS:
                continue
            if levenshtein(k1, k2) > MAX_EDITS:
                continue
            for a, ra in buckets[k1]:
                for b, rb in buckets[k2]:
                    if ra == rb:
                        continue
                    if degree[a] >= max_degree or degree[b] >= max_degree:
                        continue
                    degree[a] += 1
                    degree[b] += 1
                    edges.append((a, b, WEIGHT, ("name_twin",), ra, rb))
    return edges


def describe(edges, members):
    """One sentence for the case file, or None."""
    inner = [e for e in edges if e[0] in members and e[1] in members]
    if not inner:
        return None
    a, b = inner[0][4], inner[0][5]
    return (f'{len(inner)} pairs in this group have near-identical names once '
            f'lookalike characters are folded - for example "{a}" and "{b}". '
            f'They share no account, device or handle.')
