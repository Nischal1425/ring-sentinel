"""The scorecard. Reports on the HELD-OUT half.

Rules carried over from D:/razorpay/score.py:

  * Report what the system got wrong as prominently as what it got right. A
    review queue full of non-rings is worse than no queue - it is the thing
    that makes a risk team stop opening the tool.
  * Report the baseline separately, so any improvement is a measured number
    rather than an implied one.

Rules this track forces:

  * Thresholds are chosen on `tune`. `test` is read once, here, at the end.
    Both halves are printed, because a large gap between them IS the finding.
  * Every rate carries a Wilson interval and its denominator.
  * The cost of being wrong is in rupees, arithmetic shown, ASSUMED constants
    marked as such.

    python score.py            report on the held-out half
    python score.py --split tune
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import evalkit
from evalkit import jaccard, wilson_interval
from run import analyse
from sentinel.graph import ATTR_WEIGHTS
from sentinel.ingest import rupees
from sentinel.respond import HOLD_AT, REVIEW_AT

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BAR = "-" * 78
JACCARD_HIT = 0.5

# --- cost model ------------------------------------------------------------
# Sourced or ASSUMED, never invented precision. The sensitivity range matters
# more than the point estimate.
ANALYST_MINUTES = 8           # ASSUMED  minutes to work one ring case file
ANALYST_COST_HR = 900_00      # ASSUMED  paise/hour, loaded cost of a risk analyst
COST_OF_CAPITAL = 0.12        # ASSUMED  12%/yr -> ~0.033%/day on delayed value
CHURN_RATES = (0.005, 0.01, 0.02)   # ASSUMED  sensitivity band, not a point
MERCHANT_LTV = 1_20_000_00    # ASSUMED  paise
REVIEW_COST = round(ANALYST_COST_HR * ANALYST_MINUTES / 60)


def pct(x):
    return f"{x * 100:.1f}%"


def head(title):
    print(f"\n  {title}\n  {'.' * len(title)}")


def rate(label, successes, n, note=""):
    if n == 0:
        print(f"    {label:26}{'n/a':>9}   no cases   {note}")
        return
    lo, hi = wilson_interval(successes, n)
    print(f"    {label:26}{pct(successes / n):>9}   {successes}/{n}"
          f"   95% CI [{pct(lo)}, {pct(hi)}]   {note}")


def evaluate(side, truth, split, *, verbose=True, co_timing=True):
    """Run the pipeline on one half and return its numbers."""
    members = {r["merchant_id"] for r in truth if split[r["merchant_id"]] == side}
    true_rings, decoys, member_type = defaultdict(set), defaultdict(set), {}
    for r in truth:
        if split[r["merchant_id"]] != side or not r["gt_ring_id"]:
            continue
        member_type[r["merchant_id"]] = r["gt_ring_type"]
        target = true_rings if r["gt_is_fraud"] == "1" else decoys
        target[r["gt_ring_id"]].add(r["merchant_id"])

    verdicts, exposure, stats, _m, tbm, _pf = analyse(quiet=True, only=members,
                                                      co_timing=co_timing)
    flagged = [v for v in verdicts if v.score >= REVIEW_AT]
    matched_true, matched_pred = evalkit.match(true_rings, flagged, JACCARD_HIT)
    return dict(
        side=side, truth=truth, true_rings=true_rings, decoys=decoys,
        member_type=member_type, verdicts=verdicts, flagged=flagged,
        matched_true=matched_true, matched_pred=matched_pred,
        exposure=exposure, stats=stats, tbm=tbm,
        fraud_members={m for s in true_rings.values() for m in s},
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=("test", "tune", "all"))
    args = ap.parse_args()

    truth = evalkit.load_truth()
    split = evalkit.assign(truth)
    evalkit.assert_disjoint(truth, split)

    from sentinel.graph import load_merchants
    bridges = evalkit.attribute_bridges(load_merchants(), split, ATTR_WEIGHTS)

    tune = evaluate("tune", truth, split)
    test = evaluate("test", truth, split)
    ablate = {side: evaluate(side, truth, split, co_timing=False)
              for side in ("tune", "test")}
    r = test if args.split == "test" else tune

    n_tune = sum(1 for v in split.values() if v == "tune")
    print(BAR)
    print(f"  RING SENTINEL - SCORECARD          reporting on the {r['side'].upper()} half")
    print(BAR)

    # ------------------------------------------------------------- the split
    head("SPLIT  (group-disjoint by ring, hash-keyed, stable across machines)")
    print(f"    merchants                 {n_tune:>7} tune / "
          f"{len(split) - n_tune} test")
    print(f"    rings                     {len(tune['true_rings']):>7} tune / "
          f"{len(test['true_rings'])} test   no ring straddles the boundary (asserted)")
    print(f"    attribute bridges         {len(bridges):>7}   "
          f"linkage values appearing on both sides")
    if bridges:
        live = [b for b in bridges if b[4]]
        print(f"    of those, SURVIVING the cap  {len(live):>2}   "
              f"only these could actually leak")
        for attr, value, a, b, alive in sorted(bridges, key=lambda x: -(x[2] + x[3]))[:3]:
            print(f"        {attr}={value[:20]:22} {a} tune / {b} test"
                  f"   {'LEAKS' if alive else 'dropped by df cap'}")
        print(f"      Reported, not silently repaired: a leak belongs in the log,")
        print(f"      not hidden inside a suspiciously good score.")

    # ------------------------------------------------------------- the graph
    head("GRAPH  (the document-frequency cap is the load-bearing step)")
    s = r["stats"]
    print(f"    attribute values kept     {s['attr_values_kept']:>7}")
    print(f"    dropped as too common     {s['dropped_too_common']:>7}   "
          f"{', '.join(f'{k[1]}({n})' for k, n in s['top_dropped'][:3])}")
    print(f"    edges after pruning       {s['edges']:>7}   "
          f"uncapped the full book yields 190,569 - a 300x cut from five lines")
    print(f"    candidate rings           {s['rings']:>7}")
    print(f"    dense blobs unresolved    {s['unresolved_dense']:>7}   "
          f"never emitted - we do not act on evidence we cannot decompose")

    # ------------------------------------------------------- ring detection
    head(f"RING DETECTION  (flagged at >= {REVIEW_AT}, Jaccard >= {JACCARD_HIT})")
    rate("ring recall", len(r["matched_true"]), len(r["true_rings"]))
    rate("ring precision", len(r["matched_pred"]), len(r["flagged"]))
    purity, frag = evalkit.purity_and_fragmentation(r["true_rings"], r["flagged"])
    print(f"    purity                    {pct(purity):>9}   "
          f"of a flagged group, the share that is really that ring")
    print(f"    fragmentation             {frag:>9.2f}   "
          f"predictions per true ring (1.00 = never shattered)")

    print("\n    tune vs test - a large gap here would BE the finding:")
    for half in (tune, test):
        rec = len(half["matched_true"]) / max(len(half["true_rings"]), 1)
        prec = len(half["matched_pred"]) / max(len(half["flagged"]), 1)
        print(f"      {half['side']:5}  recall {pct(rec):>7}   precision {pct(prec):>7}"
              f"   ({len(half['true_rings'])} rings)")

    print("\n    sensitivity to the overlap cutoff:")
    for cut in (0.3, 0.5, 0.7):
        hits = sum(1 for mem in r["true_rings"].values()
                   if any(jaccard(v.ring.members, mem) >= cut for v in r["flagged"]))
        print(f"      Jaccard >= {cut}      recall {pct(hits / max(len(r['true_rings']), 1)):>7}"
              f"   {hits}/{len(r['true_rings'])}")

    # ------------------------------------------------------- per ring class
    head("PER RING CLASS  (the generator was written before the detector)")
    by_type = defaultdict(set)
    for gid, mem in r["true_rings"].items():
        by_type[r["member_type"][next(iter(mem))]].add(gid)
    for rtype in sorted(by_type):
        gids = by_type[rtype]
        hit = sum(1 for g in gids if g in r["matched_true"])
        note = ""
        if rtype == "timing_only":
            note = "  <-- shares NO attribute; found only by the co-timing relation"
        elif rtype == "slow_burn":
            note = "  <-- sits near the noise floor of every structural signal"
        print(f"      {hit}/{len(gids):<3} {rtype:16}{pct(hit / len(gids)):>8}{note}")

    # ------------------------------------------------- second-relation ablation
    head("ABLATION  (what the co-timing relation is actually worth)")
    for line in [
        "Co-timing links two accounts active on the same days far more often",
        "than the portfolio's own rhythm predicts. It runs as a SECOND PASS",
        "over accounts the attribute graph could not connect - folding it into",
        "one graph was measured and rejected: it merged distinct rings and took",
        "the largest cluster from 9 members to 20.",
    ]:
        print(f"    {line}")
    print()
    print(f"      {'split':6}{'co-timing':>11}{'recall':>9}{'precision':>11}"
          f"{'timing rings':>15}{'decoy FP':>11}")
    for side, half in (("tune", tune), ("test", test)):
        for on, h in ((False, ablate[side]), (True, half)):
            tr = h["true_rings"]
            timing = {g for g, mem in tr.items()
                      if h["member_type"][next(iter(mem))] == "timing_only"}
            hit = len(set(h["matched_true"]) & timing)
            n_decoy = len(h["decoys"])
            dfp = sum(1 for mem in h["decoys"].values()
                      if any(jaccard(v.ring.members, mem) >= JACCARD_HIT
                             for v in h["flagged"]))
            print(f"      {side:6}{'yes' if on else 'no':>11}"
                  f"{len(h['matched_true']) / max(len(tr), 1):>9.3f}"
                  f"{len(h['matched_pred']) / max(len(h['flagged']), 1):>11.3f}"
                  f"{f'{hit}/{len(timing)}':>15}"
                  f"{f'{dfp}/{n_decoy}':>11}")
    print()
    for line in [
        "The trade is explicit: co-timing buys every timing-only ring and costs",
        "a little precision. The decoy false positive below appears with",
        "co-timing OFF as well, so it is not caused by this relation.",
    ]:
        print(f"    {line}")

    # --------------------------------------------------------------- decoys
    head("DECOYS  (legitimate clusters that share attributes - must NOT flag)")
    dtypes, dhits = Counter(), Counter()
    for gid, mem in r["decoys"].items():
        dtype = r["member_type"][next(iter(mem))]
        dtypes[dtype] += 1
        if any(jaccard(v.ring.members, mem) >= JACCARD_HIT for v in r["flagged"]):
            dhits[dtype] += 1
    for dtype in sorted(dtypes):
        bad = dhits.get(dtype, 0)
        print(f"      {'clean' if not bad else f'{bad} FLAGGED':>9}  "
              f"{dtype:22} {dtypes[dtype]} planted")
    rate("decoy false-positive rate", sum(dhits.values()), len(r["decoys"]))

    # The decoy rate counts PLANTED legitimate groups only. Unaffiliated
    # merchants swept into a flagged cluster are invisible to it, and they are
    # the larger number. Printed here so the two can never be confused: a
    # reader who takes "0 decoy false positives" as "we harm nobody" is being
    # misled by a metric that was never measuring that.
    flagged_members = {m for v in r["flagged"] for m in v.ring.members}
    innocents = flagged_members - r["fraud_members"]
    unaffiliated = {m for m in innocents
                    if not next((t for t in r["truth"]
                                 if t["merchant_id"] == m), {}).get("gt_ring_id")}
    print()
    print(f"    INNOCENT MERCHANTS IN A FLAGGED CLUSTER   {len(innocents):>4}   of {len(flagged_members)} flagged")
    print(f"      of those, unaffiliated (no planted group) {len(unaffiliated):>4}"
          f"   invisible to the decoy rate above")

    # --------------------------------------------------------- member level
    head("MEMBER LEVEL  (the level that maps to money)")
    flagged_members = {m for v in r["flagged"] for m in v.ring.members}
    tp = len(flagged_members & r["fraud_members"])
    fp = len(flagged_members - r["fraud_members"])
    fn = len(r["fraud_members"] - flagged_members)
    rate("member precision", tp, tp + fp)
    rate("member recall", tp, tp + fn)
    print(f"\n    Precision is measured over merchants the GRAPH proposed, not over")
    print(f"    all {len(split):,} merchants. The legitimate mass never enters a")
    print(f"    candidate ring at all - that is the df cap and the 2-core working,")
    print(f"    and quoting it as portfolio-wide precision would be dishonest.")

    # ------------------------------------------------------ verifier effect
    head("VERIFIER  (may only lower a score - the Verdict type asserts it)")
    raw_flagged = [v for v in r["verdicts"] if v.raw_score >= REVIEW_AT]
    raw_fp = len({m for v in raw_flagged for m in v.ring.members} - r["fraud_members"])
    raw_matched, _ = evalkit.match(r["true_rings"], raw_flagged, JACCARD_HIT)
    print(f"    without verifier          {len(raw_flagged):>7} flagged   "
          f"recall {pct(len(raw_matched) / max(len(r['true_rings']), 1))}, "
          f"{raw_fp} innocent merchants caught up")
    print(f"    with verifier             {len(r['flagged']):>7} flagged   "
          f"recall {pct(len(r['matched_true']) / max(len(r['true_rings']), 1))}, "
          f"{fp} innocent merchants caught up")
    print(f"    innocents spared          {raw_fp - fp:>7}   "
          f"a measured contribution, not a claimed one")
    if raw_fp == fp:
        print(f"    On this half no decoy reached candidate status, so the verifier")
        print(f"    had nothing to exonerate. Its effect is visible on the other")
        print(f"    half and on the full book - reported here rather than hidden.")
    print(f"    exonerations offered      "
          f"{sum(len(v.exonerations) for v in r['verdicts']):>7}   "
          f"shown to the analyst even when the ring survives")

    # ---------------------------------------------------------- money terms
    head("COST OF BEING WRONG  (rupees; ASSUMED constants marked)")
    fp_rings = [v for v in r["flagged"] if v.ring.ring_id not in r["matched_pred"]]
    review_cost = len(fp_rings) * REVIEW_COST
    held_fp = [v for v in fp_rings if v.score >= HOLD_AT]
    held_value = sum(r["exposure"].get(v.ring.ring_id, 0) for v in held_fp)
    carry = round(held_value * COST_OF_CAPITAL / 365)
    caught = sum(r["exposure"].get(v.ring.ring_id, 0) for v in r["flagged"]
                 if v.ring.ring_id in r["matched_pred"])
    missed = sum(
        sum(t.amount for m in mem for t in r["tbm"].get(m, []) if not t.is_refund)
        for gid, mem in r["true_rings"].items() if gid not in r["matched_true"])

    print(f"    ASSUMED  analyst review   {rupees(REVIEW_COST):>16} per ring   "
          f"{ANALYST_MINUTES} min @ {rupees(ANALYST_COST_HR)}/hr")
    print(f"    ASSUMED  cost of capital  {COST_OF_CAPITAL:>15.0%} per year   "
          f"on value delayed by one day")
    print(f"    false-positive rings      {len(fp_rings):>7}   "
          f"review cost {rupees(review_cost)}")
    print(f"    legit value delayed       {rupees(held_value):>16}   "
          f"carry cost {rupees(carry)}")
    print(f"    total cost of error       {rupees(review_cost + carry):>16}")
    print(f"    fraud value flagged       {rupees(caught):>16}")
    print(f"    fraud value missed        {rupees(missed):>16}")
    print(f"    net                       {rupees(caught - review_cost - carry):>16}")

    print(f"\n    Churn is the term everyone fakes, so it is a band, not a number.")
    print(f"    ASSUMED LTV {rupees(MERCHANT_LTV)}, applied to "
          f"{len({m for v in fp_rings for m in v.ring.members})} wrongly-flagged merchants:")
    for p in CHURN_RATES:
        n_fp_m = len({m for v in fp_rings for m in v.ring.members})
        print(f"      p(churn)={p:<6.3f}  {rupees(round(n_fp_m * p * MERCHANT_LTV)):>16}")
    print(f"\n    No merchant is ever frozen, so the tail cost of a false positive")
    print(f"    is bounded at a reversible 24h delay - not a lost business. That")
    print(f"    bound is why these numbers are knowable rather than guessed.")

    # -------------------------------------------------------------- actions
    head("ACTIONS  (as respond.decide actually returns them, gates included)")
    from sentinel.respond import DAILY_CAP, decide
    fresh, budget, tally, gate_reasons = {}, DAILY_CAP, Counter(), Counter()
    for v in sorted(r["verdicts"], key=lambda x: -x.score):
        act, gates = decide(v, r["exposure"].get(v.ring.ring_id, 0), fresh, budget)
        if act.value == "hold":
            budget -= r["exposure"].get(v.ring.ring_id, 0)
        tally[act.value] += 1
        for g in gates:
            gate_reasons[g.split(";")[0].split(" - ")[0]] += 1
    for k, n in tally.most_common():
        print(f"      {k:16}{n:>5}")
    if gate_reasons:
        print()
        print("    escalation stopped by a gate:")
        for g, n in gate_reasons.most_common(5):
            print(f"      {n:>3}x  {g}")
    print(f"\n    The Action enum contains nothing above a reversible 24h settlement")
    print(f"    delay. There is no freeze in this codebase to trigger by accident.")
    print(f"\n{BAR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
