"""The second opinion, which may only ever lower a score.

This is the direct answer to Razorpay's documented failure mode - merchants
frozen "without specific reasons or evidence". On a system whose whole claim
is that its actions are defensible, the second pass must be structurally
incapable of manufacturing guilt. So it returns a discount in [0, 1] and the
Verdict constructor asserts score <= raw_score.

Its job is to try to KILL each candidate ring by finding the innocent
explanation. What survives that is worth an analyst's time; what does not was
never evidence.

The exonerations are reported even when the ring survives, because "we
considered and rejected these three innocent explanations" is what makes an
action defensible to the merchant on the other end of it.
"""

from __future__ import annotations

from collections import defaultdict

from .graph import ATTR_WEIGHTS, EDGE_MIN
from .types import Ring

MIN_RING = 3


# Below this, one shared attribute is a coincidence with innocent
# explanations. At or above it, one is already hard to explain away: several
# unrelated merchants do not share a bank account or a UPI handle by accident.
WEAK_ATTR = 0.75


# A signal at or above this is independent corroboration: whatever the graph
# says, something about this group's actual behaviour is abnormal.
CORROBORATION = 0.70


# What a group DOES, as opposed to what it IS. Shape and tenure are properties
# of the accounts; money, names and timing are conduct. The distinction is
# load-bearing: "related but unremarkable" means "behaves like an ordinary
# business", and a chain that opened five outlets in one week has not DONE
# anything - it just exists. Counting tenure as behaviour flagged every one of
# the decoy_simultaneous_launch clusters the moment the tenure signal shipped.
NOT_BEHAVIOUR = ("structural", "tenure")


def _behavioural(signals):
    """Strongest signal describing CONDUCT. Shape and tenure are not conduct."""
    return max((v for k, v in (signals or {}).items() if k not in NOT_BEHAVIOUR),
               default=0.0)


def _single_attribute(ring: Ring, signals):
    """The only link is ONE WEAK attribute type, and nothing else is odd.

    Deliberately not "one attribute type" alone. Plenty of real rings share
    exactly one thing - a settlement account, a UPI handle - and discounting
    those was suppressing the strongest evidence in the book.

    Nor does it fire when an independent signal corroborates. Six merchants on
    one office IP is nothing; six merchants on one office IP with near-identical
    names is not nothing, and the weak link is no longer the whole story.
    """
    kinds = {k for e in ring.edges for k in e[3]}
    if len(kinds) != 1:
        return None
    kind = next(iter(kinds))
    if ATTR_WEIGHTS.get(kind, 0.0) >= WEAK_ATTR:
        return None
    if _behavioural(signals) >= CORROBORATION:
        return None
    return 0.62, (
        f"Every link rests on one shared {kind.replace('_', ' ')} and nothing "
        f"else, and that attribute is weak on its own - an office router, a "
        f"shared accountant or a recycled number all produce it.")


def _strongest_link(ring: Ring) -> float:
    """Weight of the strongest attribute type linking anyone in this ring."""
    return max((ATTR_WEIGHTS.get(k, 0.0) for e in ring.edges for k in e[3]),
               default=0.0)


def _hub_shaped(ring: Ring, signals=None):
    """One account touches everyone, members do not touch each other.

    Shape alone is NOT enough to clear a star, and assuming it was is how this
    check cleared criminal recruiters. A marketplace hub shares INFRASTRUCTURE
    with its sellers - an IP, a gateway, a platform. It does not share its
    settlement account or its device fingerprint with them, because those are
    not platform properties; they are identity.

    So a star built on weak, infrastructural links is a marketplace and gets
    exonerated. A star built on a shared bank account is a recruiter and does
    not. The distinguishing evidence is WHAT is shared, not the shape.
    """
    n = len(ring)
    if n < MIN_RING:
        return None

    # Shape is an argument about the GRAPH. It cannot clear a group whose
    # CONDUCT is independently damning, because conduct does not depend on the
    # graph at all. Two name-twin rings scoring identity 1.00 were being
    # discounted to a third of their score by this check, on the grounds that
    # they were shaped like a marketplace - while their names said otherwise.
    # Same gate _single_attribute already applies, for the same reason.
    if _behavioural(signals) >= CORROBORATION:
        return None
    if _strongest_link(ring) >= WEAK_ATTR:
        return None
    deg = defaultdict(int)
    for a, b, *_ in ring.edges:
        deg[a] += 1
        deg[b] += 1
    if not deg:
        return None
    hub_share = max(deg.values()) / (n - 1)
    density = len(ring.edges) / (n * (n - 1) / 2)
    if hub_share >= 0.9 and density <= 0.5:
        return 0.45, (
            f"Shape is a hub with spokes: one account accounts for "
            f"{hub_share:.0%} of the links while overall density is only "
            f"{density:.0%}. Marketplaces, resellers and payment aggregators "
            f"look exactly like this.")
    return None


def _rides_the_portfolio(ring, txns_by_merchant, portfolio):
    """The burst is the whole book's burst - a festival, not a conspiracy."""
    txns = [t for m in ring.members for t in txns_by_merchant.get(m, [])]
    if len(txns) < 12:
        return None
    per_day = defaultdict(int)
    for t in txns:
        per_day[t.day] += 1
    total = len(txns)
    excess = sum(max(0.0, c / total - portfolio.day_share(d))
                 for d, c in per_day.items())
    if excess < 0.12:
        return 0.40, (
            f"Activity follows the portfolio's own calendar (excess concentration "
            f"only {excess:.0%}). The whole book was busy on these days - festive "
            f"stocking, payday and tax dates do this - so the timing is not "
            f"evidence.")
    return None


def _threshold_artifact(ring: Ring, signals=None):
    """Does the ring survive a 10% tightening of the edge threshold?

    A cluster that only exists at one exact setting of our own parameter is an
    artifact of the tool, not a fact about the merchants. Cheap to check and
    disproportionately persuasive.
    """
    if not ring.edges:
        return None

    # ...but only when nothing else is shouting. This check reasons entirely
    # about OUR OWN parameter, and money, names and timing do not depend on it.
    # A refund mill scoring money 1.00 was discounted from 97 to 48 here because
    # its edges happened to sit just under the tightened cutoff - a fact about
    # our threshold, offered as though it were a fact about the merchants.
    if _behavioural(signals) >= CORROBORATION:
        return None
    # Perturb OUR OWN global threshold by 10%, not the ring's weakest edge.
    # Tightening relative to the ring's own minimum always removes that edge,
    # which made this check fire on nearly everything.
    cutoff = EDGE_MIN + (1.0 - EDGE_MIN) * 0.10
    kept = [e for e in ring.edges if e[2] >= cutoff]

    # Survival means STAYING CONNECTED, not surviving a 2-core. Using 2-core
    # here exonerated every chain-shaped ring automatically: a path always
    # unravels under degree-1 stripping, so a layered mule network scoring 96
    # was being discounted to 48 by a check that had assumed cliques. Same
    # wrong assumption as find_rings had, in a second place.
    survivors = _largest_connected(ring.members, kept)
    if len(survivors) < MIN_RING:
        return 0.50, (
            f"The group does not survive a 10% tightening of the linkage "
            f"threshold - only {len(survivors)} of {len(ring)} accounts remain "
            f"connected. This cluster is partly an artifact of where we set our "
            f"own parameter.")
    return None


def _related_but_unremarkable(ring: Ring, signals):
    """Clearly related accounts that do nothing unusual.

    This is what a franchise chain, a group company or a family business looks
    like: several outlets on one settlement account and one phone, dense by
    construction, trading normally. The graph is right that they are connected
    and wrong that it matters.

    Structure alone must never be enough to act on. Being related is not an
    offence; behaving like a ring is.
    """
    if not signals or signals.get("structural", 0.0) < 0.6:
        return None
    behaviour = _behavioural(signals)
    if behaviour >= 0.5:
        return None

    # Being related is not an offence - but being opened TOGETHER is not merely
    # being related. A group company accumulates outlets over years; a batch of
    # accounts registered in the same fortnight is a fact that wants explaining,
    # and "they trade normally" does not explain it. An honest chain that really
    # did launch several outlets at once is still cleared, by _branded_chain,
    # on the evidence that it advertises itself as one business.
    if (signals.get("tenure") or 0.0) >= 0.6:
        return None

    # A franchise or group company is DENSE: every outlet sits on the owner's
    # account, which links them all to each other. A star is not that shape.
    # A hub sharing its settlement account with spokes that have nothing to do
    # with one another is a recruiter, and quiet trading does not excuse it.
    deg = defaultdict(int)
    for a, b, *_ in ring.edges:
        deg[a] += 1
        deg[b] += 1
    n = len(ring)
    hub_share = (max(deg.values()) / (n - 1)) if deg and n > 1 else 0.0
    density = len(ring.edges) / (n * (n - 1) / 2) if n > 1 else 0.0
    if hub_share >= 0.9 and density <= 0.5 and _strongest_link(ring) >= WEAK_ATTR:
        return None
    return 0.35, (
        f"These accounts are genuinely related, but nothing they DO is unusual "
        f"- strongest behavioural signal is only {behaviour:.2f}. Common "
        f"ownership, a franchise chain or a group company all look like this. "
        f"Being connected is not an offence.")


def _largest_connected(members, edges):
    """Biggest set of members still joined to one another by `edges`."""
    adj = defaultdict(set)
    for a, b, *_ in edges:
        adj[a].add(b)
        adj[b].add(a)
    seen, best = set(), set()
    for m in members:
        if m in seen:
            continue
        stack, comp = [m], set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.add(x)
            stack.extend(adj[x] - seen)
        if len(comp) > len(best):
            best = comp
    return best


def _branded_chain(ring: Ring, merchants):
    """Member names share a brand and differ only by a location suffix.

    "SHREE TEXTILES BLR", "SHREE TEXTILES DEL", "SHREE TEXTILES MUM" is a chain
    advertising that it is one business. A recruiter's mules are named
    independently, because looking related is the last thing they want.

    This is the opposite of the identity signal's homoglyph check, and the
    difference is the point: `SRI` versus `SRl` is a deception, a shared brand
    plus a distinct city is a disclosure. Needed because some recruiter rings
    are genuine cliques, so neither shape nor tenure separates them from a
    franchise - only the naming does.
    """
    names = [(merchants.get(m) or {}).get("name", "").strip().upper()
             for m in ring.members]
    names = [n for n in names if n]
    if len(names) < MIN_RING:
        return None

    prefix = names[0]
    for n in names[1:]:
        while prefix and not n.startswith(prefix):
            prefix = prefix[:-1]
    mean_len = sum(len(n) for n in names) / len(names)
    if len(prefix.strip()) < 6 or len(prefix) < 0.55 * mean_len:
        return None
    if len({n[len(prefix):].strip() for n in names}) < len(names):
        return None          # suffixes must be distinct - duplicates are not a chain
    return 0.35, (
        f'All {len(names)} names share the brand "{prefix.strip()}" and differ '
        f'only by a distinct suffix. A chain advertises that its outlets are one '
        f'business; accounts hiding a common owner do the opposite.')


def verify(ring, txns_by_merchant, portfolio, signals=None, merchants=None):
    """Return (discount in [0,1], exoneration reasons).

    Discounts compound multiplicatively: three weak-but-independent innocent
    explanations should matter more than one.

    Every check that reasons about the GRAPH - its shape, its parameters - now
    takes `signals` and stands down when conduct independently corroborates.
    Only `_rides_the_portfolio` does not, because it is itself a conduct
    argument and gates on its own evidence.
    """
    merchants = merchants or {}
    checks = [
        _single_attribute(ring, signals),
        _branded_chain(ring, merchants),
        _related_but_unremarkable(ring, signals),
        _hub_shaped(ring, signals),
        _rides_the_portfolio(ring, txns_by_merchant, portfolio),
        _threshold_artifact(ring, signals),
    ]
    discount, reasons = 1.0, []
    for hit in checks:
        if hit:
            factor, why = hit
            discount *= factor
            reasons.append(why)
    return discount, reasons
