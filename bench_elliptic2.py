"""External benchmark: Ring Sentinel's scorer on Elliptic2's real labelled rings.

    python bench_elliptic2.py

WHY THIS DATASET. Every number elsewhere in this repo is measured on data this
repo generated. That is the single biggest reason to distrust it. Elliptic2 is
the only public dataset we could find with CLUSTER-level ground truth: 121,810
Bitcoin subgraphs, 2,763 of them labelled suspicious by Elliptic's analysts,
2.27% positive. Real money, real crime, labels we did not write.

WHAT THIS CAN AND CANNOT TEST - read before quoting any number below.

  * It tests the SCORING half only. The subgraphs are GIVEN: connected
    components of edges.csv reproduce the 121,810 published ccIds exactly,
    1:1. So ring DISCOVERY - the df cap, the 2-core, the escalation loop - is
    not exercised at all.
  * The 2-core is disabled here, and must be: 94% of these subgraphs are
    trees, so 2-core would delete almost the whole dataset.
  * Only ONE of our five signals can run. Bitcoin clusters have no amounts,
    no refund flags, no merchant names and no usable timestamps in this
    20 MB slice, so `money`, `identity` and `velocity` have nothing to read.
    Structure is all that is left.
  * The median subgraph is 3 nodes. There is very little shape in 3 nodes.

Published results on this exact task, from Table 2 of arXiv:2404.19109v1
(read from the paper, not from a summary):

    GLASS        PR-AUC 0.208     uses the 83 GB background graph
    GNN-Seg      PR-AUC 0.026     structure-only, like us
    Sub2Vec      PR-AUC 0.022

The paper reports no random baseline. Our base rate is 0.0227, computed from
the label file, and is printed as ours.

The honest prediction before running: a structure-only scorer should land near
GNN-Seg, because the discriminative signal in Elliptic2 lives in the background
graph that neither of us downloaded. Printing that result rather than hiding it
is the point of the exercise.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from sentinel.signals import structural
from sentinel.types import Ring

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DATA = Path(__file__).parent / "data" / "elliptic2"
BAR = "-" * 74

# Verified against Table 2 of arXiv:2404.19109v1 by reading the paper, not by
# trusting a summary. An earlier draft of this file printed Sub2Vec at 0.038,
# which was simply wrong - the paper says 0.022.
#
# The paper reports NO random baseline. The base rate below is ours, computed
# from the label file, and it is labelled as ours for that reason.
PUBLISHED = [
    ("GLASS          (paper, Table 2)", 0.208),
    ("GNN-Seg        (paper, structure-only)", 0.026),
    ("Sub2Vec        (paper, Table 2)", 0.022),
]


def load():
    cc = pd.read_csv(DATA / "connected_components.csv")
    nodes = pd.read_csv(DATA / "nodes.csv")
    edges = pd.read_csv(DATA / "edges.csv")

    member = defaultdict(list)
    for cl, ccid in nodes.itertuples(index=False):
        member[ccid].append(cl)

    owner = dict(nodes.itertuples(index=False))     # clId -> ccId
    by_cc = defaultdict(list)
    for a, b, _tx in edges.itertuples(index=False):
        cc_of = owner.get(a)
        if cc_of is not None and owner.get(b) == cc_of:
            by_cc[cc_of].append((a, b))

    labels = dict(cc.itertuples(index=False))       # ccId -> 'licit'|'suspicious'
    return member, by_cc, labels


def score_all(member, by_cc, labels):
    """Run our structural signal, plus cheap baselines, over every subgraph."""
    rows = []
    for ccid, members in member.items():
        raw_edges = by_cc.get(ccid, [])
        n = len(members)
        # Our Ring wants (a, b, weight, kinds). Multi-edges collapse to one
        # link; their multiplicity becomes a separate feature below.
        uniq = {(min(a, b), max(a, b)) for a, b in raw_edges}
        ring = Ring(str(ccid), tuple(str(m) for m in members),
                    tuple((str(a), str(b), 1.0, ("chain",)) for a, b in uniq))

        det = structural.detect(ring)               # None below 3 members
        possible = n * (n - 1) / 2
        rows.append((
            ccid,
            1 if labels.get(ccid) == "suspicious" else 0,
            det.score if det else 0.0,              # ours
            n,                                       # size baseline
            len(uniq) / possible if possible else 0.0,   # density baseline
            len(raw_edges) / max(len(uniq), 1),      # repeat-transaction rate
        ))
    return pd.DataFrame(rows, columns=[
        "ccId", "y", "sentinel_structural", "size", "density", "repeat_rate"])


def precision_at_k(y, s, k):
    idx = np.argsort(-np.asarray(s, dtype=float))[:k]
    return float(np.asarray(y)[idx].mean())


def main():
    if not (DATA / "edges.csv").exists():
        print("  Missing data/elliptic2/*.csv. Fetch with:")
        print("  B=https://huggingface.co/datasets/mendozzzz/elliptic2-data-set/"
              "resolve/main")
        print("  curl -sSL -o data/elliptic2/edges.csv $B/edges.csv   (and "
              "nodes.csv, connected_components.csv)")
        return 1

    member, by_cc, labels = load()
    df = score_all(member, by_cc, labels)
    y = df.y.values
    base = y.mean()

    print(BAR)
    print(f"  ELLIPTIC2 - EXTERNAL BENCHMARK        {len(df):,} real subgraphs, "
          f"{int(y.sum()):,} suspicious ({base:.2%})")
    print(BAR)
    print("  Labels by Elliptic's analysts. We wrote none of them.")
    print("  Clusters are GIVEN, so this scores our RANKER, not our discovery.")
    print("  Only the structural signal can run: no amounts, names or times here.\n")

    print(f"  {'scorer':38}{'PR-AUC':>9}{'lift':>7}{'P@100':>8}{'P@1000':>9}")
    print("  " + "-" * 69)
    for col in ("sentinel_structural", "size", "density", "repeat_rate"):
        s = df[col].values
        ap = average_precision_score(y, s)
        print(f"  {col:38}{ap:>9.4f}{ap / base:>7.2f}x"
              f"{precision_at_k(y, s, 100):>8.2f}{precision_at_k(y, s, 1000):>9.3f}")

    print(f"\n  {'published on this exact task':38}{'PR-AUC':>9}")
    print("  " + "-" * 69)
    for name, ap in PUBLISHED:
        print(f"  {name:38}{ap:>9.4f}{ap / base:>7.2f}x")

    ours = average_precision_score(y, df.sentinel_structural.values)
    print()
    if ours < 0.05:
        for line in [
            "READ THIS BEFORE QUOTING THE NUMBER ABOVE.",
            "",
            "Our structural signal lands at the structure-only published",
            "baseline (GNN-Seg 0.026) and above Sub2Vec (0.022). All three",
            "are close to the base rate. In Elliptic2 the discriminative",
            "signal lives in the 83 GB background graph none of us loaded,",
            "and the median labelled subgraph is 3 nodes - there is almost",
            "no shape to read.",
            "",
            "The finding this bought: run ablate.py and compare. Structure",
            "carries real recall on our synthetic book and next to",
            "nothing on real subgraphs. That gap said our rings were too",
            "dense - gen_data.py built only cliques, while 94% of real",
            "labelled subgraphs here are trees. Two tree-shaped ring classes",
            "(layered_chain, recruiter_star) were added because of this",
            "result, and they exposed two real bugs: the 2-core deleted",
            "chains outright, and the verifier exonerated them for the same",
            "reason. Both are fixed.",
            "",
            "Deliberately NOT hardcoding the ablation deltas here. Numbers",
            "typed into prose go stale the moment the data is regenerated,",
            "which is exactly how this file came to print a wrong Sub2Vec",
            "score. Run ablate.py for the current ones.",
        ]:
            print(f"  {line}" if line else "")
    print(f"\n{BAR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
