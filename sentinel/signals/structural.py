"""Shape of the cluster: is it a conspiracy or a coincidence?

A ring is near-clique - members are linked to each other. A coincidental
cluster is a star (one hub everybody touches, e.g. a payment aggregator or a
reseller platform) or a chain. Star shape is treated as NEGATIVE evidence
rather than merely weak evidence, because the aggregator false positive is one
of the four decoy classes and left alone it dominates.
"""

from __future__ import annotations

from collections import defaultdict

from ..graph import ATTR_WEIGHTS
from ..types import Detection, Ring

MIN_MEMBERS = 3

# At or above this an attribute is IDENTITY (a settlement account, a device
# fingerprint, a UPI handle). Below it, it is INFRASTRUCTURE (an IP prefix, a
# mail domain) - the kind of thing a platform legitimately shares with the
# merchants sitting on it.
IDENTITY_LINK = 0.75


def _largest_component_share(ring: Ring) -> float:
    """Fraction of members sitting in the ring's largest connected piece."""
    adj = defaultdict(set)
    for a, b, *_ in ring.edges:
        adj[a].add(b)
        adj[b].add(a)
    seen, best = set(), 0
    for m in ring.members:
        if m in seen:
            continue
        stack, size = [m], 0
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            size += 1
            stack.extend(adj[x] - seen)
        best = max(best, size)
    return best / len(ring) if len(ring) else 0.0


def detect(ring: Ring) -> Detection | None:
    """None when the cluster is too small for shape to mean anything."""
    n = len(ring)
    if n < MIN_MEMBERS:
        return None

    possible = n * (n - 1) / 2
    density = len(ring.edges) / possible if possible else 0.0

    deg: dict[str, int] = defaultdict(int)
    for a, b, *_ in ring.edges:
        deg[a] += 1
        deg[b] += 1
    star_ratio = (max(deg.values()) / (n - 1)) if deg and n > 1 else 0.0

    # How many independent kinds of evidence link these members. One shared
    # attribute type is a thin story; three is hard to explain innocently.
    kinds = {k for e in ring.edges for k in e[3]}
    variety = min(len(kinds) / 3.0, 1.0)

    # How STRONG the links are, independent of how many there are. Density is
    # the wrong lens for a chain: six merchants joined in an unbroken line by
    # shared bank accounts are damning at 0.30 density, because no two
    # unrelated merchants share a settlement account at all. Rewarding only
    # density meant a layered network scored below a coincidental cluster -
    # and bench_elliptic2.py says trees, not cliques, are the common real
    # shape, so that bias mattered more than it looked.
    strength = (sum(e[2] for e in ring.edges) / len(ring.edges)) if ring.edges else 0.0

    # Connectivity, not density: is every member reachable from every other?
    # A whole ring joined in one piece is a different object from three pairs
    # that happen to sit in the same component.
    reach = _largest_component_share(ring)

    # There are two innocent reasons to be connected, and they have different
    # shapes. A MARKETPLACE is a star and shares infrastructure - the platform
    # IP. A GROUP COMPANY is dense and shares ownership - one settlement
    # account linking every outlet to every other. Neither is a star that
    # shares IDENTITY: a platform does not put its sellers on its own bank
    # account, and a group company is not a star.
    #
    # So a hub-and-spoke built from identity links is positive evidence, not
    # neutral - and density is actively misleading here. Without this term the
    # detector scored innocent franchises ABOVE criminal recruiters, purely
    # because franchises are denser.
    strongest = max((ATTR_WEIGHTS.get(k, 0.0) for e in ring.edges for k in e[3]),
                    default=0.0)
    # Density matters as much as the hub ratio. In a COMPLETE graph every node
    # has degree n-1, so star_ratio is 1.00 for a clique - a four-outlet
    # franchise looked exactly like a star until this was added.
    identity_star = (star_ratio >= 0.85 and density <= 0.5
                     and strongest >= IDENTITY_LINK)

    star_penalty = (max(0.0, star_ratio - density)
                    if star_ratio > 0.85 and not identity_star else 0.0)
    shape = max(density, strength * reach, 0.85 if identity_star else 0.0)
    score = max(0.0, min(1.0, (0.60 * shape + 0.20 * variety
                               + 0.20 * strength) - star_penalty))

    reasons = []
    if identity_star:
        reasons.append(
            f"One account sits at the centre of {star_ratio:.0%} of the links, "
            f"and what it shares with the others is identity, not "
            f"infrastructure. A marketplace shares an IP with its sellers; it "
            f"does not put them on its own settlement account or device.")
    if density < 0.5 and strength >= 0.8 and reach >= 0.9:
        reasons.append(
            f"These {n} accounts form one connected chain rather than a cluster, "
            f"and every link is a strong shared identifier (mean strength "
            f"{strength:.2f}). Sparse does not mean weak: unrelated merchants do "
            f"not share settlement accounts or device fingerprints at all.")
    if density >= 0.6:
        reasons.append(
            f"{len(ring.edges)} of {int(possible)} possible links between the "
            f"{n} accounts are present ({density:.0%} dense) - they are connected "
            f"to each other, not merely to a common third party.")
    if len(kinds) >= 2:
        reasons.append(
            f"Linked by {len(kinds)} independent attribute types "
            f"({', '.join(sorted(k.replace('_', ' ') for k in kinds))}).")
    if star_penalty:
        reasons.append(
            f"Shape is hub-and-spoke ({star_ratio:.0%} of links touch one account), "
            f"which is what a marketplace or aggregator looks like. Score reduced.")
    return Detection(score, reasons)
