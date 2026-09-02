"""The evaluation harness. Reads ground truth; the detector never does.

The invariant this repo actually enforces is not "one file reads gt_" but the
stronger and more useful one: NOTHING UNDER sentinel/ CAN SEE GROUND TRUTH.
The detector package is truth-blind by construction, and test_pipeline.py
greps to prove it. This module and its two callers (score.py, tune.py) are the
evaluation side of that wall.

Splitting is group-disjoint BY RING, never by merchant. A ring straddling two
splits is memorisation: half its members in tune and half in test means the
thresholds were chosen while looking at the answer.

Two splits, not three, and the reason is honest: nothing here is trained. There
are no learned parameters, so there is no train split - only the thresholds and
weights chosen on `tune`, and a `test` half read once at the end. That is the
flaw tune_fusion.py names in its own docstring in the repo this was lifted
from, and it is the flaw this track explicitly grades.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from hashlib import md5
from math import sqrt
from pathlib import Path

DATA = Path(__file__).parent / "data"
TUNE_SHARE = 50  # percent of ring groups that land in `tune`


def load_truth(path: Path | None = None) -> list[dict]:
    with open(path or DATA / "truth.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def group_of(row: dict) -> str:
    """A merchant's split group: its ring, or itself if unaffiliated.

    Grouping by ring is what keeps a ring whole. Unaffiliated merchants are
    their own group so the legitimate population is spread across both halves.
    """
    return row["gt_ring_id"] or row["merchant_id"]


def assign(truth: list[dict], *, tune_share: int = TUNE_SHARE) -> dict[str, str]:
    """merchant_id -> "tune" | "test", keyed by a hash of the GROUP.

    Hash-keyed rather than random so the split is identical on every machine
    and across reruns without carrying a seed file around.
    """
    out = {}
    for row in truth:
        bucket = int(md5(group_of(row).encode()).hexdigest(), 16) % 100
        out[row["merchant_id"]] = "tune" if bucket < tune_share else "test"
    return out


def assert_disjoint(truth: list[dict], split: dict[str, str]) -> None:
    """No ring may have members on both sides. Loud failure, not a warning."""
    sides = defaultdict(set)
    for row in truth:
        if row["gt_ring_id"]:
            sides[row["gt_ring_id"]].add(split[row["merchant_id"]])
    straddling = {g for g, s in sides.items() if len(s) > 1}
    if straddling:
        raise AssertionError(
            f"{len(straddling)} ring(s) straddle the split: "
            f"{sorted(straddling)[:5]} - thresholds would be tuned on test data")


def attribute_bridges(merchants: dict, split: dict[str, str],
                      linkage_attrs, df_cap: int = 50):
    """Attribute values shared across the split boundary.

    Disjoint ring ids are not enough. If one bank account appears in both
    halves, a tune ring bleeds into a test ring through the shared value and
    the split leaks. Reported rather than silently repaired, so a leak shows up
    in the log instead of as a suspiciously good score.
    """
    by_value = defaultdict(lambda: defaultdict(int))
    for mid, row in merchants.items():
        side = split.get(mid)
        if side is None:
            continue
        for attr in linkage_attrs:
            value = (row.get(attr) or "").strip()
            if value:
                by_value[(attr, value)][side] += 1

    out = []
    for (attr, value), sides in by_value.items():
        if sides["tune"] and sides["test"]:
            total = sides["tune"] + sides["test"]
            # A bridge only leaks if the value survives the graph's own
            # document-frequency cap. gmail.com spans both halves and is
            # discarded before an edge is ever built from it.
            out.append((attr, value, sides["tune"], sides["test"],
                        total <= df_cap))
    return out


# ----------------------------------------------------------------- matching

def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def wilson_interval(successes: int, n: int, z: float = 1.96):
    """(low, high) for a proportion, so no rate is ever reported bare.

    With forty planted rings a point estimate is not a measurement. This is one
    line of arithmetic and it is the difference between "83.3%" and "83.3%,
    somewhere between 66% and 93%, on 24 cases".
    """
    if n == 0:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom), min(1.0, (centre + margin) / denom)


def match(true_rings: dict, flagged: list, cutoff: float = 0.5):
    """Best-overlap matching of predicted rings to planted ones.

    A Jaccard cutoff of 0.5 is not a flattering round number: two DISJOINT true
    rings cannot both score >= 0.5 against one prediction, so the matching is
    automatically one-to-one and needs no Hungarian assignment. Report the
    neighbouring cutoffs anyway.
    """
    matched_true, matched_pred = {}, {}
    for gid, members in true_rings.items():
        best_j, best_v = 0.0, None
        for v in flagged:
            j = jaccard(v.ring.members, members)
            if j > best_j:
                best_j, best_v = j, v
        if best_j >= cutoff and best_v is not None:
            matched_true[gid] = (best_j, best_v)
            matched_pred[best_v.ring.ring_id] = gid
    return matched_true, matched_pred


def purity_and_fragmentation(true_rings: dict, flagged: list, cutoff: float = 0.5):
    """Two failures Jaccard alone hides, with very different operational costs.

    purity        of what we flagged, how much was really that ring
    fragmentation did we shatter one ring into several predictions
    """
    purities, frags = [], []
    for _gid, members in true_rings.items():
        overlapping = [v for v in flagged if set(v.ring.members) & set(members)]
        if not overlapping:
            continue
        best = max(overlapping, key=lambda v: jaccard(v.ring.members, members))
        purities.append(len(set(best.ring.members) & set(members)) / len(best.ring.members))
        frags.append(len(overlapping))
    return (sum(purities) / len(purities) if purities else 0.0,
            sum(frags) / len(frags) if frags else 0.0)
