"""Knock out one thing at a time and measure what it was worth.

    python ablate.py

Every claim about "the verifier helps" or "co-timing is worth it" or "the df cap
is load-bearing" should be a number, not an adjective. This runs each ablation
on the HELD-OUT half and prints the delta.

The most useful output here is the one that embarrassed us: bench_elliptic2.py
shows the structural signal is worth almost nothing on real Elliptic2 subgraphs,
while the table below shows what it is worth on ours. Two measurements that
disagree usually mean the synthetic data is too kind, not that the real data is
broken.
"""

from __future__ import annotations

import sys
from collections import defaultdict

import evalkit
from run import analyse
from sentinel import detect
from sentinel.respond import REVIEW_AT

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BAR = "-" * 70


def measure(members, true_rings, decoys, **kw):
    verdicts, _exp, stats, *_ = analyse(quiet=True, only=members, **kw)
    flagged = [v for v in verdicts if v.score >= REVIEW_AT]
    matched, matched_pred = evalkit.match(true_rings, flagged)
    dfp = sum(1 for mem in decoys.values()
              if any(evalkit.jaccard(v.ring.members, mem) >= 0.5 for v in flagged))
    return (len(matched) / max(len(true_rings), 1),
            len(matched_pred) / max(len(flagged), 1),
            dfp, stats["edges"])


def main():
    truth = evalkit.load_truth()
    split = evalkit.assign(truth)
    members = {r["merchant_id"] for r in truth if split[r["merchant_id"]] == "test"}
    true_rings, decoys = defaultdict(set), defaultdict(set)
    for r in truth:
        if split[r["merchant_id"]] != "test" or not r["gt_ring_id"]:
            continue
        (true_rings if r["gt_is_fraud"] == "1" else decoys)[r["gt_ring_id"]].add(
            r["merchant_id"])

    print(BAR)
    print(f"  ABLATIONS on the HELD-OUT half   {len(true_rings)} rings, "
          f"{len(decoys)} decoys")
    print(BAR)

    base = measure(members, true_rings, decoys)
    print(f"\n  {'configuration':30}{'recall':>9}{'prec':>8}{'decoyFP':>9}{'delta':>9}")
    print("  " + "-" * 65)
    print(f"  {'full system':30}{base[0]:>9.3f}{base[1]:>8.3f}{base[2]:>9}")

    # --- one signal at a time -------------------------------------------
    full_weights = dict(detect.WEIGHTS)
    for name in sorted(full_weights):
        detect.WEIGHTS = {k: v for k, v in full_weights.items() if k != name}
        r = measure(members, true_rings, decoys)
        print(f"  {'no ' + name + ' signal':28}{r[0]:>9.3f}{r[1]:>8.3f}"
              f"{r[2]:>9}{r[3]:>9,}{r[0] - base[0]:>+9.3f}")
    detect.WEIGHTS = full_weights

    # --- the second relation --------------------------------------------
    r = measure(members, true_rings, decoys, co_timing=False)
    print(f"  {'no co-timing relation':28}{r[0]:>9.3f}{r[1]:>8.3f}"
          f"{r[2]:>9}{r[3]:>9,}{r[0] - base[0]:>+9.3f}")

    # --- the document-frequency cap -------------------------------------
    for cap in (20, 100, 1_000_000):
        r = measure(members, true_rings, decoys, df_cap=cap)
        label = f"df cap = {cap:,}" if cap < 1_000_000 else "df cap OFF"
        print(f"  {label:28}{r[0]:>9.3f}{r[1]:>8.3f}{r[2]:>9}{r[3]:>9,}"
              f"{r[0] - base[0]:>+9.3f}")

    for line in [
        "",
        "Read the signal rows against bench_elliptic2.py, where the same",
        "structural signal is worth almost nothing on real labelled",
        "subgraphs. That gap is what prompted the tree-shaped ring classes",
        "(layered_chain, recruiter_star), because the generator had been",
        "building only cliques while 94% of real subgraphs are trees.",
        "",
        "The df-cap rows are a COST control, not a quality one: turning it",
        "off barely moves recall and explodes the edge count. And a signal",
        "showing 0.000 here is not useless - tenure reads 0.000 because the",
        "split put four of five recruiter_quiet rings in the other half.",
        "Read the per-class table in score.py before drawing conclusions",
        "from any single row above.",
    ]:
        print(f"  {line}" if line else "")
    print(f"\n{BAR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
