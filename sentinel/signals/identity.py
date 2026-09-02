"""Names: are these the same business wearing several hats?

Ported from D:/Neurons Prototype/src/engine/impersonation.py, retargeted from
"is this SMS impersonating a bank" to "are these merchants impersonating each
other". The mechanism is unchanged: fold confusable characters to a skeleton
(the UTS #39 idea), then allow a bounded edit distance on top.

No brand list. The comparison is between members of the same cluster, so there
is nothing to hardcode and nothing to memorise - which is also what makes the
salted-identifier test in test_pipeline.py pass.
"""

from __future__ import annotations

from itertools import combinations

from ..types import Detection, Ring

MIN_MEMBERS = 3
MAX_EDITS = 2

# Confusable folding. Digits and lookalike letters collapse to one canonical
# form, so SHREE / 5HREE / SHR33 share a skeleton.
_FOLD = str.maketrans({
    "0": "O", "1": "I", "l": "I", "|": "I", "3": "E", "4": "A",
    "5": "S", "6": "G", "7": "T", "8": "B", "9": "G", "2": "Z",
})


def skeleton(name: str) -> str:
    """Canonical form: upper-cased, confusables folded, spacing collapsed."""
    return " ".join(name.upper().translate(_FOLD).split())


def levenshtein(a: str, b: str, max_distance: int = MAX_EDITS) -> int:
    """Edit distance, abandoned early once it exceeds max_distance."""
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > max_distance:
            return max_distance + 1
        prev = cur
    return prev[-1]


def detect(ring: Ring, merchants) -> Detection | None:
    """None when there are too few names to compare."""
    n = len(ring)
    if n < MIN_MEMBERS:
        return None

    names = [(m, (merchants.get(m, {}).get("name") or "").strip())
             for m in ring.members]
    names = [(m, nm) for m, nm in names if nm]
    if len(names) < MIN_MEMBERS:
        return None

    hits, examples = 0, []
    pairs = list(combinations(names, 2))
    for (_ma, na), (_mb, nb) in pairs:
        sa, sb = skeleton(na), skeleton(nb)
        if sa == sb or levenshtein(sa, sb) <= MAX_EDITS:
            hits += 1
            if len(examples) < 3 and na != nb:
                examples.append(f'"{na}" / "{nb}"')

    share = hits / len(pairs) if pairs else 0.0
    reasons = []
    if examples:
        reasons.append(
            f"{hits} of {len(pairs)} name pairs are near-identical once lookalike "
            f"characters are folded: {'; '.join(examples)}.")
    elif share:
        reasons.append(f"{hits} of {len(pairs)} member names are duplicates.")
    return Detection(min(1.0, share * 1.4), reasons)
