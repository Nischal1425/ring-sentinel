"""Merchants to shared-attribute graph to candidate rings.

The whole difficulty here is the giant component. A handful of attribute
VALUES have enormous degree - every merchant is on gmail.com, thousands report
a device string of "Windows" - and one such value creates a clique of ten
thousand nodes. The graph is dead before any clustering runs.

Four mechanisms, in order, each the simplest thing that holds:

1. Type whitelist. Only identifier-shaped attributes link merchants. A
   category ("visa", "debit") is not a linkage attribute.

2. Hard document-frequency cap. Drop any attribute value shared by more than
   DF_CAP merchants, or by fewer than two. This is five lines and does most of
   the work: it kills free-mail domains and default device strings in one pass
   with no hand-maintained blocklist. The justification is one sentence a judge
   can check - an attribute shared by ten thousand merchants is not evidence of
   collusion, it is a category.

   It is a hard filter rather than a soft weight for an engineering reason: a
   soft weight still admits the ten-thousand-node clique into memory. Pair
   generation costs the sum of df squared, bounded at DF_CAP squared per value.
   That bound is what makes this tractable, and it is the answer to "does this
   scale".

3. Noisy-OR edge weights over the attributes a pair shares.

4. Connected components, then 2-core, then size-capped threshold escalation.

The 2-core step is what turns a cluster into a ring: strip degree-1 nodes,
because a merchant hanging off a single shared attribute is more likely a
coincidence than a conspirator.

ponytail: plain union-find components, no Louvain. On a df-capped, 2-cored
graph its partition is close to this one, and it would cost a dependency, a
resolution parameter to defend, and stochastic tie-breaking - bad in a demo
and worse in a system whose pitch is explainability. Escalate to community
detection only if the printed size histogram says components stay too big.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .fusion import fuse
from .nameref import name_edges
from .temporal import cooccurrence_edges
from .types import Detection, Ring

DATA = Path(__file__).resolve().parent.parent / "data"

# Only identifier-shaped attributes link merchants, and each carries a
# different amount of evidence. Sharing a bank account is nearly conclusive;
# sharing a free-mail domain is nearly meaningless.
ATTR_WEIGHTS = {
    "bank_account": 0.92,
    "upi_vpa": 0.88,
    "device_fp": 0.85,
    "phone": 0.70,
    "ip_prefix": 0.45,
    "email_domain": 0.12,
    # Not an attribute at all: a second RELATION. Two entities active on the
    # same days far more often than the portfolio's own rhythm predicts. This
    # is the edge that makes a ring sharing no identifier visible - see
    # temporal.py. Weighted below a shared bank account and above a shared IP:
    # co-timing is real evidence but it is circumstantial.
    "co_timing": 0.66,
    # A near-identical name is a link in its own right - see nameref.py. Third
    # relation, second pass, same rule: it never reshapes a cluster the
    # attribute graph already found.
    "name_twin": 0.72,
}

# Every value here is whatever tune.py last recommended from the TUNE half.
# They have moved twice during this project and both times the sweep was
# right and the guess was wrong, so they are not hand-set any more.
DF_CAP = 50          # shared by more merchants than this: a category, not a link
DF_MIN = 2           # shared by nobody else: not a link either
EDGE_MIN = 0.50      # prune weak edges before clustering
SIZE_CAP = 30        # anything larger is a blob; re-split it
MAX_ESCALATION = 4
MIN_RING = 3         # a pair is not a ring

# If the 2-core would delete more than this share of a component, the
# component IS a tree rather than a dense cluster with strays hanging off it,
# and stripping it is destroying evidence rather than cleaning it. Measured
# against bench_elliptic2.py: 94% of real labelled fraud subgraphs are trees,
# and our own layered_chain rings were being wiped out entirely - found only
# because a second relation happened to rescue them.
TREE_RESCUE = 0.5


def load_merchants(path: Path | None = None) -> dict[str, dict[str, str]]:
    with open(path or DATA / "merchants.csv", encoding="utf-8") as f:
        return {r["merchant_id"]: r for r in csv.DictReader(f)}


def invert(merchants, *, df_cap=DF_CAP, df_min=DF_MIN):
    """Map (attr_type, value) to members, after the document-frequency cap.

    Also returns what was dropped, because a filter that cannot say what it
    removed is not auditable.
    """
    index = defaultdict(set)
    for mid, row in merchants.items():
        for attr in ATTR_WEIGHTS:
            value = (row.get(attr) or "").strip()
            if value:
                index[(attr, value)].add(mid)

    kept, dropped_common, dropped_unique = {}, [], 0
    for key, members in index.items():
        if len(members) < df_min:
            dropped_unique += 1
        elif len(members) > df_cap:
            dropped_common.append((key, len(members)))
        else:
            kept[key] = frozenset(members)
    return kept, dropped_common, dropped_unique


def _idf(df: int, n: int, df_cap: int) -> float:
    """Rarity of an attribute value, normalised to 0-1 across the kept band."""
    hi, lo = math.log(n / DF_MIN), math.log(n / df_cap)
    if hi <= lo:
        return 1.0
    return max(0.0, min(1.0, (math.log(n / df) - lo) / (hi - lo)))


def build_edges(merchants, index, *, df_cap=DF_CAP, edge_min=EDGE_MIN):
    """Weighted edges between merchants sharing surviving attribute values."""
    n = len(merchants)
    shared: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)

    for (attr, _value), members in index.items():
        rarity = _idf(len(members), n, df_cap)
        ordered = sorted(members)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                pair = (a, b)
                # Several values of one attribute type count once, at their
                # strongest. Two merchants sharing three IPs are not three
                # times as linked as two merchants sharing one.
                shared[pair][attr] = max(shared[pair].get(attr, 0.0), rarity)

    edges = []
    for (a, b), attrs in shared.items():
        weight = fuse({k: Detection(v) for k, v in attrs.items()}, ATTR_WEIGHTS,
                      renormalise=False)
        if weight >= edge_min:
            # Every shared type, strongest first. The consumer that wants one
            # label takes [0]; the consumers that want variety take the lot.
            kinds = tuple(sorted(attrs, key=lambda k: -ATTR_WEIGHTS[k] * attrs[k]))
            edges.append((a, b, weight, kinds))
    return edges


def _components(nodes, edges):
    """Connected components over an edge list, as lists of member ids."""
    if not edges:
        return []
    idx = {m: i for i, m in enumerate(sorted(nodes))}
    rows = [idx[a] for a, b, *_ in edges if a in idx and b in idx]
    cols = [idx[b] for a, b, *_ in edges if a in idx and b in idx]
    if not rows:
        return []
    g = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(idx), len(idx)))
    _count, labels = connected_components(g, directed=False)

    out = defaultdict(list)
    back = {i: m for m, i in idx.items()}
    for i, lab in enumerate(labels):
        out[lab].append(back[i])
    return [v for v in out.values() if len(v) > 1]


def two_core(members, edges):
    """Strip degree-1 nodes until every survivor has at least two links."""
    alive = set(members)
    local = [(a, b) for a, b, *_ in edges if a in alive and b in alive]
    while alive:
        deg = defaultdict(int)
        for a, b in local:
            deg[a] += 1
            deg[b] += 1
        weak = {m for m in alive if deg[m] < 2}
        if not weak:
            break
        alive -= weak
        local = [(a, b) for a, b in local if a in alive and b in alive]
    return alive


def find_rings(merchants, edges, *, size_cap=SIZE_CAP, edge_min=EDGE_MIN,
               min_ring=MIN_RING):
    """Components, then 2-core, then raise the bar on anything still huge.

    Returns (rings, unresolved). The unresolved blobs are NOT emitted as rings:
    the system does not act on evidence it cannot decompose. That is the honest
    failure mode of a threshold-escalation loop, and naming it here is cheaper
    than being caught by it later.
    """
    rings, unresolved, tree_rescued = [], [], 0
    queue = [(list(merchants), edges, edge_min, 0)]

    while queue:
        nodes, sub_edges, threshold, depth = queue.pop()
        for comp in _components(nodes, sub_edges):
            core = two_core(comp, sub_edges)
            if len(core) < max(min_ring, len(comp) * TREE_RESCUE) <= len(comp):
                # Tree-shaped: keep it whole. The structural signal scores it
                # on its real (low) density, so a chain is not mistaken for a
                # clique - it simply stops being deleted before it is scored.
                core = set(comp)
                tree_rescued += 1
            if len(core) < min_ring:
                continue
            inner = [e for e in sub_edges if e[0] in core and e[1] in core]
            if len(core) <= size_cap:
                rings.append((sorted(core), inner))
            elif depth >= MAX_ESCALATION:
                unresolved.append(sorted(core))
            else:
                raised = threshold + (1.0 - threshold) * 0.35
                queue.append((sorted(core), [e for e in inner if e[2] >= raised],
                              raised, depth + 1))

    out = []
    for i, (members, inner) in enumerate(sorted(rings, key=lambda r: -len(r[0]))):
        out.append(Ring(f"R{i:04d}", tuple(members), tuple(inner)))
    find_rings.tree_rescued = tree_rescued
    return out, unresolved


def build(merchants=None, *, df_cap=DF_CAP, edge_min=EDGE_MIN, size_cap=SIZE_CAP,
          txns_by_merchant=None):
    """The whole pipeline, in two passes over two different relations.

    PASS 1 - shared attributes. Strong, identifier-based evidence.

    PASS 2 - temporal co-occurrence, run ONLY over entities pass 1 could not
    connect to anyone. Co-timing is circumstantial: real rings share rhythms,
    but so do two independent gangs that both run on Tuesdays. Folding it into
    one graph was measured and rejected - it merged distinct rings, took the
    largest cluster from 9 members to 20 and produced a blob that would not
    decompose. Letting a weak relation rewrite clusters built from bank
    accounts and device fingerprints is exactly backwards.

    So co-timing is a fallback, not a peer. It gets a chance to explain the
    accounts nothing else could, and it is never allowed to touch a cluster
    the attribute graph already found. Pass `txns_by_merchant` to enable it;
    leave it out for the attribute-only ablation score.py reports.
    """
    merchants = merchants if merchants is not None else load_merchants()
    index, dropped_common, dropped_unique = invert(merchants, df_cap=df_cap)
    edges = build_edges(merchants, index, df_cap=df_cap, edge_min=edge_min)
    rings, unresolved = find_rings(merchants, edges, size_cap=size_cap,
                                   edge_min=edge_min)

    n_attr, n_co, co_rings = len(edges), 0, 0
    if txns_by_merchant:
        claimed = {m for r in rings for m in r.members}
        loose = [m for m in merchants if m not in claimed]
        # Each weak relation gets its OWN pass. Combining their edge sets let a
        # co-timing link and a name link chain two unrelated rings into one
        # 13-member blob - the same failure as a coincidental attribute bridge,
        # one level down. Two weak relations are not one stronger relation.
        extra, extra_unresolved, n_co = [], [], 0
        for build_edges_fn, args in ((cooccurrence_edges, (txns_by_merchant,)),
                                     (name_edges, (merchants,))):
            still_loose = [m for m in loose
                           if m not in {x for r in extra for x in r.members}]
            found = build_edges_fn(still_loose, *args)
            n_co += len(found)
            if not found:
                continue
            got, unres = find_rings(still_loose,
                                    [(a, b, w, k) for a, b, w, k, *_ in found],
                                    size_cap=size_cap, edge_min=edge_min)
            extra += got
            extra_unresolved += unres

        if extra or extra_unresolved:
            co_rings = len(extra)
            rings = list(rings) + list(extra)
            unresolved = list(unresolved) + list(extra_unresolved)
            rings = [Ring(f"R{i:04d}", r.members, r.edges)
                     for i, r in enumerate(sorted(rings, key=lambda r: -len(r)))]

    stats = {
        "merchants": len(merchants),
        "attr_values_kept": len(index),
        "dropped_too_common": len(dropped_common),
        "dropped_unique": dropped_unique,
        "top_dropped": sorted(dropped_common, key=lambda kv: -kv[1])[:4],
        "edges": n_attr,
        "edges_co_timing": n_co,
        "rings": len(rings),
        "rings_from_co_timing": co_rings,
        "tree_rescued": getattr(find_rings, "tree_rescued", 0),
        "unresolved_dense": len(unresolved),
    }
    return rings, stats
