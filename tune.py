"""Sweep the graph parameters and the flag threshold. ON THE TUNE HALF ONLY.

    python tune.py                 recall under a 10% decoy false-positive ceiling
    python tune.py --max-fp 0.05   stricter

Lifted in shape from D:/Neurons Prototype/scripts/tune_fusion.py, including
its caching trick: build and score once per graph configuration, then apply
the flag thresholds afterwards, so the grid costs nine pipeline runs rather
than forty-five.

What is NOT carried over is that script's own admitted flaw. Its docstring
says: "tuning on the evaluation set means the resulting numbers are no longer
fully held out". Here the sweep only ever sees the tune half. score.py reports
the test half. That separation is the point, and it is asserted in
test_pipeline.py rather than promised here.

The objective is deliberately not accuracy. It is the best ring recall that
stays under a ceiling on DECOY false positives - the legitimate franchise
chains, aggregators, family businesses and festival bursts that the generator
plants specifically to be flagged by a careless detector. An unbounded-FP
optimum is unshippable no matter what the arithmetic says.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

import evalkit
from evalkit import jaccard
from run import analyse
from sentinel import detect, verify
from sentinel.graph import build, load_merchants
from sentinel.ingest import Portfolio, by_merchant, load_txns
from sentinel.types import Verdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DF_CAPS = (30, 50, 100)
EDGE_MINS = (0.40, 0.50, 0.60, 0.75, 0.86)  # now meaningful: see fusion.fuse
THRESHOLDS = (50, 55, 60, 65, 70, 75)


def score_config(members, df_cap, edge_min):
    """Build and score once. Thresholds are applied by the caller, afterwards."""
    merchants = {m: r for m, r in load_merchants().items() if m in members}
    txns = [t for t in load_txns() if t.merchant_id in merchants]
    tbm = by_merchant(txns)
    portfolio = Portfolio.build(txns)
    # Sweep the system we actually ship. Tuning an attribute-only graph and
    # then deploying with the co-timing relation on would be tuning a
    # different program than the one score.py grades.
    rings, _stats = build(merchants, df_cap=df_cap, edge_min=edge_min,
                          txns_by_merchant=tbm)

    out = []
    for ring in rings:
        raw, signals, reasons = detect.score(ring, merchants, tbm, portfolio)
        discount, exonerations = verify.verify(ring, tbm, portfolio, signals, merchants)
        out.append(Verdict(ring=ring, score=round(raw * discount), raw_score=raw,
                           reasons=reasons, exonerations=exonerations,
                           signals=signals))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-fp", type=float, default=0.0,
                    help="ceiling on the decoy false-positive rate")
    args = ap.parse_args()

    truth = evalkit.load_truth()
    split = evalkit.assign(truth)
    evalkit.assert_disjoint(truth, split)

    members = {r["merchant_id"] for r in truth if split[r["merchant_id"]] == "tune"}
    true_rings, decoys = defaultdict(set), defaultdict(set)
    for r in truth:
        if split[r["merchant_id"]] != "tune" or not r["gt_ring_id"]:
            continue
        (true_rings if r["gt_is_fraud"] == "1" else decoys)[r["gt_ring_id"]].add(
            r["merchant_id"])

    print(f"  Tuning on the TUNE half only: {len(members):,} merchants, "
          f"{len(true_rings)} rings, {len(decoys)} decoys")
    print(f"  Decoy false-positive ceiling: {args.max_fp:.0%}\n")

    cache = {}
    for df_cap in DF_CAPS:
        for edge_min in EDGE_MINS:
            cache[(df_cap, edge_min)] = score_config(members, df_cap, edge_min)
            print(f"    scored df_cap={df_cap:<4} edge_min={edge_min}")

    rows = []
    for (df_cap, edge_min), verdicts in cache.items():
        for thresh in THRESHOLDS:
            flagged = [v for v in verdicts if v.score >= thresh]
            matched, matched_pred = evalkit.match(true_rings, flagged)
            fp = sum(1 for mem in decoys.values()
                     if any(jaccard(v.ring.members, mem) >= 0.5 for v in flagged))
            recall = len(matched) / max(len(true_rings), 1)
            precision = len(matched_pred) / max(len(flagged), 1)
            rows.append((df_cap, edge_min, thresh, recall,
                         fp / max(len(decoys), 1), precision, len(flagged)))

    print(f"\n{'df_cap':>8}{'edge':>7}{'flag_at':>9}{'recall':>9}"
          f"{'decoy FP':>10}{'precision':>11}{'flagged':>9}")
    print("  " + "-" * 61)

    ok = [r for r in rows if r[4] <= args.max_fp]
    ok.sort(key=lambda r: (-r[3], -r[5]))
    best = ok[0] if ok else None

    for row in (ok[:10] or sorted(rows, key=lambda r: r[4])[:10]):
        df_cap, edge_min, thresh, recall, fp, prec, n = row
        mark = "  <-- best under ceiling" if row is best else ""
        print(f"{df_cap:>8}{edge_min:>7}{thresh:>9}{recall:>9.3f}"
              f"{fp:>10.1%}{prec:>11.3f}{n:>9}{mark}")

    if best:
        df_cap, edge_min, thresh, recall, fp, prec, _n = best
        print(f"\n  Recommended: DF_CAP={df_cap}, EDGE_MIN={edge_min}, "
              f"REVIEW_AT={thresh}")
        print(f"    ring recall {recall:.3f} at {fp:.1%} decoy false positives, "
              f"precision {prec:.3f}")
        print(f"    Set these in sentinel/graph.py and sentinel/respond.py.")
        print(f"\n  These numbers are from the TUNE half. The held-out result is")
        print(f"  whatever score.py prints, and it will be worse. That gap is the")
        print(f"  honest measurement.")
    else:
        print(f"\n  No configuration stays under a {args.max_fp:.0%} decoy "
              f"false-positive rate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
