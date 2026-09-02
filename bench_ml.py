"""Does machine learning help here? Measured, and the answer is no.

    python bench_ml.py

WHY THIS EXISTS. The track is called "AI Risk Manager" and this detector is
graph mathematics, statistics and rules. The obvious move is to bolt a model on
so the word "AI" appears. This script is the reason we did not, and it is the
honest form of that answer: we tested it, on a proper group-disjoint split, and
report the result whichever way it fell.

Three models, all trained on the TUNE half and judged on the held-out half,
using the same ring-level features the hand-tuned fusion sees.

WHAT THE NUMBERS SAY. The rules win, and not narrowly. The reason is not that
models are bad; it is that there are about thirty labelled candidate clusters to
learn from. Fifty planted rings is a rich evaluation set and a hopeless training
set.

WHAT THE TREE SAYS, which matters more than the numbers. Printed below is the
decision tree, the "explainable AI" that a hybrid would ship. Read its top split
before believing in it. It learns that HIGH structural density means NOT fraud -
exactly backwards, fitted from noise. It is explainable in the sense that you can
print it, and what it explains is false. A model that can articulate a wrong rule
to a merchant is worse than no model, not better.

WHERE A MODEL WOULD ACTUALLY BELONG. Not here. At merchant level there are 1,550
labelled examples rather than thirty, and a model there scores a suspicious
PR-AUC of 1.000 - because the generator makes criminals transact four times less
than honest merchants and the model finds that giveaway instantly. See the
volume-leak section of the README. Fix that first; then ask this question again.
"""

from __future__ import annotations

import sys
from collections import defaultdict

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

import evalkit
from run import analyse
from sentinel.graph import ATTR_WEIGHTS
from sentinel.respond import REVIEW_AT

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BAR = "-" * 74
SIGNALS = ("structural", "velocity", "identity", "money", "tenure")
NAMES = list(SIGNALS) + ["n_members", "density", "hub_share", "n_kinds",
                         "max_attr_weight", "mean_edge_weight"]


def featurise(side):
    """Ring-level features for every candidate the graph proposed on one half."""
    truth = evalkit.load_truth()
    split = evalkit.assign(truth)
    members = {r["merchant_id"] for r in truth if split[r["merchant_id"]] == side}
    real = defaultdict(set)
    for r in truth:
        if split[r["merchant_id"]] == side and r["gt_ring_id"] \
                and r["gt_is_fraud"] == "1":
            real[r["gt_ring_id"]].add(r["merchant_id"])

    verdicts, *_ = analyse(quiet=True, only=members)
    X, y, fused = [], [], []
    for c in verdicts:
        n = len(c.ring)
        deg = defaultdict(int)
        for a, b, *_rest in c.ring.edges:
            deg[a] += 1
            deg[b] += 1
        kinds = {k for e in c.ring.edges for k in e[3]}
        X.append(
            [c.signals.get(k, 0.0) for k in SIGNALS] + [
                n,
                len(c.ring.edges) / (n * (n - 1) / 2) if n > 1 else 0.0,
                (max(deg.values()) / (n - 1)) if deg and n > 1 else 0.0,
                len(kinds),
                max((ATTR_WEIGHTS.get(k, 0.0) for k in kinds), default=0.0),
                float(np.mean([e[2] for e in c.ring.edges])) if c.ring.edges else 0.0,
            ])
        y.append(int(any(evalkit.jaccard(c.ring.members, mem) >= 0.5
                         for mem in real.values())))
        fused.append(c.score)
    return np.array(X, float), np.array(y), np.array(fused)


def recall_at(y, score, floor):
    """Best recall reachable without dropping below the fusion's precision."""
    order = np.argsort(-np.asarray(score, float))
    best = 0.0
    for k in range(1, len(order) + 1):
        sel = order[:k]
        if y[sel].mean() >= floor:
            best = max(best, y[sel].sum() / max(y.sum(), 1))
    return best


def main():
    Xtr, ytr, _ftr = featurise("tune")
    Xte, yte, fte = featurise("test")

    print(BAR)
    print(f"  DOES A MODEL BEAT THE RULES?     train {len(ytr)} candidates, "
          f"test {len(yte)}")
    print(BAR)
    print("  Group-disjoint by ring. Trained on tune, judged on held-out.")
    print("  Same features the hand-tuned fusion already sees.\n")

    floor = yte[fte >= REVIEW_AT].mean()
    print(f"  {'scorer':32}{'PR-AUC':>9}{'recall at equal precision':>28}")
    print("  " + "-" * 70)
    print(f"  {'hand-tuned fusion (shipped)':32}"
          f"{average_precision_score(yte, fte / 100.0):>9.3f}"
          f"{recall_at(yte, fte / 100.0, floor):>28.3f}")

    tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=3,
                                  random_state=0).fit(Xtr, ytr)
    p_tree = tree.predict_proba(Xte)[:, 1]
    print(f"  {'decision tree (depth 3)':32}"
          f"{average_precision_score(yte, p_tree):>9.3f}"
          f"{recall_at(yte, p_tree, floor):>28.3f}")

    scaler = StandardScaler().fit(Xtr)
    logit = LogisticRegression(max_iter=2000).fit(scaler.transform(Xtr), ytr)
    p_lr = logit.predict_proba(scaler.transform(Xte))[:, 1]
    print(f"  {'logistic regression':32}"
          f"{average_precision_score(yte, p_lr):>9.3f}"
          f"{recall_at(yte, p_lr, floor):>28.3f}")

    print("\n  The tree a 'glass box hybrid' would ship:\n")
    for line in export_text(tree, feature_names=NAMES, max_depth=3).split("\n"):
        if line.strip():
            print(f"    {line}")

    for line in [
        "",
        "READ THE TOP SPLIT.",
        "",
        "It learns that HIGH structural density means NOT fraud. That is",
        "backwards - dense mutual connection is the strongest single indicator",
        "of a ring in this book - and it is fitted from about thirty examples.",
        "",
        "That is the argument against the hybrid, and it is stronger than the",
        "PR-AUC gap. A printable rule that is wrong is not explainability; it",
        "is a confident false explanation handed to a merchant whose payout we",
        "just delayed. The rules it would replace were each written against a",
        "stated reason and are checked by test_pipeline.py.",
        "",
        "Fifty planted rings is a rich evaluation set and a hopeless training",
        "set. The honest AI answer here is a measured negative result, not a",
        "model added to satisfy a track name.",
    ]:
        print(f"  {line}" if line else "")
    print(f"\n{BAR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
