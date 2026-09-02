"""Seventeen checks. Each one guards a claim made on stage.

    python test_pipeline.py

No framework, no fixtures. If a claim in the README cannot be checked by a
line here, it should not be in the README.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path

import evalkit
from run import analyse
from sentinel import respond
from sentinel.graph import ATTR_WEIGHTS, SIZE_CAP, build, load_merchants
from sentinel.ingest import by_merchant, load_txns
from sentinel.respond import REVIEW_AT, decide
from sentinel.types import Action
from sentinel.types import Verdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
OK = []


def check(name):
    def wrap(fn):
        OK.append((name, fn))
        return fn
    return wrap


# --------------------------------------------------------------------------
@check("money is integer paise, and the ledger conserves it")
def test_paise():
    txns = load_txns()
    for t in txns[:2000]:
        assert isinstance(t.amount, int), f"{t.txn_id} amount is {type(t.amount)}"
    gross = sum(t.amount for t in txns if not t.is_refund)
    refunded = sum(t.amount for t in txns if t.is_refund)
    net = sum(t.amount if not t.is_refund else -t.amount for t in txns)
    # No tolerance. If this ever needs an epsilon, a float got into the ledger.
    assert gross - refunded == net, f"{gross} - {refunded} != {net}"
    return f"{len(txns):,} txns, net {net} paise, exact"


# --------------------------------------------------------------------------
@check("the detector package cannot see ground truth")
def test_gt_isolation():
    """The wall is around sentinel/, not around one file.

    evalkit/score/tune are the evaluation side and are allowed to read truth.
    Nothing the detector imports may mention it.
    """
    offenders = []
    for path in (ROOT / "sentinel").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"gt_|truth\.csv", text):
            offenders.append(path.name)
    assert not offenders, f"detector package reads ground truth: {offenders}"

    # The grep alone is NOT proof, and an adversarial probe showed why: a
    # module reading `'tr' + 'uth.csv'` sails straight past it. So watch the
    # filesystem instead of the source. Every open() during a full pipeline
    # run is recorded, and touching the label file fails the check no matter
    # how the path was spelled.
    import builtins
    real_open, touched = builtins.open, []

    def watched(file, *a, **kw):
        try:
            if "truth" in str(file).lower():
                touched.append(str(file))
        except Exception:
            pass
        return real_open(file, *a, **kw)

    builtins.open = watched
    try:
        analyse(quiet=True)
    finally:
        builtins.open = real_open
    assert not touched, (
        f"the pipeline opened the label file at runtime: {touched[:3]}")

    n = len(list((ROOT / "sentinel").rglob("*.py")))
    return (f"{n} modules pass the grep AND a full pipeline run opened the "
            f"label file zero times")


# --------------------------------------------------------------------------
@check("scores survive salting every identifier (nothing is memorised)")
def test_hash_invariance():
    """Salt-hash every linkage identifier and re-run.

    If any rule keyed off a literal value - a bank prefix, a domain, a device
    string - the ring set would change. Merchant NAMES are deliberately left
    alone: the identity signal compares them to each other, never to a list,
    so there is nothing there to memorise either.
    """
    base = load_merchants()
    salted = {}
    for mid, row in base.items():
        r = dict(row)
        for attr in ATTR_WEIGHTS:
            if r.get(attr):
                r[attr] = hashlib.md5(("salt" + r[attr]).encode()).hexdigest()[:14]
        salted[mid] = r

    before = {frozenset(r.members) for r in build(base)[0]}
    after = {frozenset(r.members) for r in build(salted)[0]}
    assert before == after, (
        f"{len(before ^ after)} ring(s) changed when identifiers were salted - "
        f"something is keyed off a literal value")
    return f"{len(before)} rings identical under salting"


# --------------------------------------------------------------------------
@check("no giant component survives the document-frequency cap")
def test_no_giant_component():
    rings, stats = build()
    largest = max((len(r) for r in rings), default=0)
    assert largest <= SIZE_CAP, f"largest ring is {largest} > {SIZE_CAP}"
    assert stats["unresolved_dense"] == 0, (
        f"{stats['unresolved_dense']} dense blobs could not be decomposed")
    return f"largest ring {largest} <= cap {SIZE_CAP}, {stats['edges']} edges"


# --------------------------------------------------------------------------
@check("the split is group-disjoint by ring, with bridges reported")
def test_group_disjoint():
    truth = evalkit.load_truth()
    split = evalkit.assign(truth)
    evalkit.assert_disjoint(truth, split)          # raises on a straddling ring

    merchants = load_merchants()
    bridges = evalkit.attribute_bridges(merchants, split, ATTR_WEIGHTS)
    surviving = [b for b in bridges if b[4]]

    # A surviving bridge only leaks if it joins RING members across the
    # boundary. Two unrelated merchants colliding on an IP prefix is birthday
    # noise: with 1,451 merchants drawn from ~64k prefixes, collisions are
    # expected and carry no ring information.
    #
    # It cannot propagate in any case - each half rebuilds its own graph from
    # its own merchants, so a value shared across the split never becomes an
    # edge. The check stays because that would silently stop being true the
    # day someone builds one global graph and filters at scoring time.
    ring_side = {r["merchant_id"]: split[r["merchant_id"]]
                 for r in truth if r["gt_ring_id"] and r["gt_is_fraud"] == "1"}
    dangerous = []
    for attr, value, _a, _b, alive in surviving:
        if not alive:
            continue
        holders = [m for m, row in merchants.items() if row.get(attr) == value]
        sides_seen = {ring_side[m] for m in holders if m in ring_side}
        if len(sides_seen) > 1:
            dangerous.append((attr, value))
    assert not dangerous, (
        f"{len(dangerous)} value(s) join ring members across the split: "
        f"{dangerous[:3]}")

    sides = defaultdict(set)
    for row in truth:
        if row["gt_ring_id"]:
            sides[row["gt_ring_id"]].add(split[row["merchant_id"]])
    return (f"{len(sides)} ring groups, none straddling; {len(bridges)} bridges "
            f"({len(surviving)} survive the cap), none joining rings")


# --------------------------------------------------------------------------
@check("a ring fires while every one of its payments looks ordinary")
def test_ring_fires_below_transaction_threshold():
    """The differentiation claim, as an assert rather than a slide.

    A transaction-level model - which is what Razorpay's Vulcan already is -
    scores one payment at a time. This finds a ring where NO individual payment
    is unusual enough to flag, yet the group is. If this test cannot find such
    a ring, the whole premise of working at ring level is unsupported and the
    submission should say so.
    """
    verdicts, _exposure, _stats, _m, tbm, _pf = analyse(quiet=True)
    txns = load_txns()

    # A deliberately generous stand-in for a per-transaction model: flag a
    # payment if it is a refund, a chargeback, or far out on the amount
    # distribution. Generous because a weak baseline would make this trivial.
    amounts = sorted(t.amount for t in txns)
    p99 = amounts[int(len(amounts) * 0.99)]

    def txn_is_suspicious(t):
        return t.is_chargeback or t.is_refund or t.amount >= p99

    for v in sorted(verdicts, key=lambda x: -x.score):
        if v.score < REVIEW_AT:
            break
        ring_txns = [t for m in v.ring.members for t in tbm.get(m, [])]
        if len(ring_txns) < 20:
            continue
        hot = sum(txn_is_suspicious(t) for t in ring_txns)
        if hot == 0:
            return (f"ring {v.ring.ring_id} scores {v.score} on "
                    f"{len(v.ring)} accounts and {len(ring_txns)} payments - "
                    f"not one payment is individually suspicious")
    raise AssertionError(
        "no ring fires while all its payments look ordinary - the case for "
        "ring-level detection is not demonstrated on this data")


# --------------------------------------------------------------------------
@check("no action escalates past a gate, and the responder cannot move money")
def test_action_gates():
    # 1. The enum itself contains nothing that moves money.
    forbidden = {"freeze", "block", "close", "seize", "debit", "capture",
                 "refund", "transfer", "payout"}
    for member in Action:
        assert member.value.lower() not in forbidden, f"Action.{member.name} moves money"

    # 2. A ring with a huge exposure and a cold history cannot be held.
    verdicts, exposure, _s, _m, _t, _p = analyse(quiet=True)
    top = max(verdicts, key=lambda v: v.score)
    action, gates = decide(top, respond.PER_RING_CAP + 1, {}, respond.DAILY_CAP)
    assert action is not Action.HOLD_SETTLEMENT, "over-cap exposure was held"
    assert gates, "a blocked escalation must say which gate stopped it"

    # 3. The circuit breaker: an exhausted daily budget stops everything.
    action, gates = decide(top, 1_00_00_000_00, {"x": 9}, 0)
    assert action is not Action.HOLD_SETTLEMENT, "held with no budget left"
    return f"{len(list(Action))} actions, top of ladder = {Action.HOLD_SETTLEMENT.value}"


# --------------------------------------------------------------------------
@check("the Razorpay client has no method that can move money")
def test_client_surface():
    """The defense-only claim, checked against the class instead of trusted.

    Absent, not disabled. If someone adds a capture() in six months this fails
    on the next run rather than in production.
    """
    from sentinel import razorpay

    surface = {n for n in dir(razorpay.NotesOnlyClient) if not n.startswith("_")}
    assert surface == {"fetch_payments", "fetch_payment", "fetch_orders",
                       "fetch_customers", "annotate", "annotate_order"}, (
        f"client surface changed: {sorted(surface)}")
    # Four reads and two note-patches. Nothing that moves money.

    banned = ("capture", "refund", "transfer", "payout", "settle", "freeze",
              "block", "void", "reverse_payment")
    source = (ROOT / "sentinel" / "razorpay.py").read_text(encoding="utf-8")
    for verb in banned:
        assert f"def {verb}" not in source, f"client defines {verb}()"
    # And it will not point at a live key even if one is exported.
    assert "rzp_test_" in source, "client does not enforce test mode"
    return f"surface = {sorted(surface)}; live keys refused"


# --------------------------------------------------------------------------
@check("co-timing does not link merchants who merely share the calendar")
def test_cotiming_survives_the_festival():
    """The adversarial test for the second relation.

    decoy_festive merchants all burst together through the Diwali build. Any
    co-occurrence measure without a popularity-aware null model links them
    instantly, and the whole book with them. This asserts the null model earns
    its place: NO festive decoy may be linked to another member of its own
    decoy group by a co-timing edge.
    """
    from sentinel.temporal import cooccurrence_edges

    truth = {r["merchant_id"]: r for r in evalkit.load_truth()}
    merchants = load_merchants()
    tbm = by_merchant(load_txns())
    edges = cooccurrence_edges(list(merchants), tbm)

    groups = defaultdict(set)
    for m, r in truth.items():
        if r["gt_ring_type"].startswith("decoy"):
            groups[r["gt_ring_id"]].add(m)

    bad = []
    for a, b, *_ in edges:
        ga = truth.get(a, {}).get("gt_ring_id")
        if ga and ga == truth.get(b, {}).get("gt_ring_id")                 and truth[a]["gt_ring_type"] == "decoy_festive":
            bad.append((a, b))
    assert not bad, (
        f"{len(bad)} co-timing edge(s) inside a festive decoy group - the null "
        f"model is not absorbing portfolio-wide seasonality")

    linked_any_decoy = sum(
        1 for a, b, *_ in edges
        if truth.get(a, {}).get("gt_ring_id")
        and truth[a]["gt_ring_id"] == truth.get(b, {}).get("gt_ring_id")
        and truth[a]["gt_ring_type"].startswith("decoy"))
    return (f"{len(edges)} co-timing edges, 0 inside a festive decoy, "
            f"{linked_any_decoy} inside any decoy group")


# --------------------------------------------------------------------------
@check("the second relation finds rings that share no attribute at all")
def test_cotiming_closes_the_blind_spot():
    """Attribute-only must MISS timing_only rings; co-timing must FIND them.

    Both halves matter. If the attribute graph already found them the second
    relation is unnecessary, and if co-timing does not find them it is useless.
    """
    truth = evalkit.load_truth()
    by_id = {r["merchant_id"]: r for r in truth}
    rings = defaultdict(set)
    for r in truth:
        if r["gt_ring_id"] and r["gt_ring_type"] == "timing_only":
            rings[r["gt_ring_id"]].add(r["merchant_id"])
    assert rings, "no timing_only rings planted"

    scores = {}
    for co in (False, True):
        verdicts, *_ = analyse(quiet=True, co_timing=co)
        flagged = [v for v in verdicts if v.score >= REVIEW_AT]
        matched, _ = evalkit.match(rings, flagged)
        scores[co] = len(matched)

    assert scores[False] == 0, (
        f"attribute graph already found {scores[False]} timing_only rings - "
        f"they are not attribute-free and the blind-spot claim is wrong")
    assert scores[True] == len(rings), (
        f"co-timing found only {scores[True]}/{len(rings)} timing_only rings")
    return (f"attribute-only {scores[False]}/{len(rings)}, "
            f"with co-timing {scores[True]}/{len(rings)}")


# --------------------------------------------------------------------------
@check("annotating an order never destroys the merchant's own notes")
def test_annotate_merges():
    """Razorpay's PATCH replaces the whole notes object.

    We shipped the naive version and it deleted the merchant's metadata - the
    very attributes the detector reads. Offline check with a stubbed transport,
    including the case that matters most: when the 15-key cap forces a choice,
    OUR keys are dropped and theirs are kept.
    """
    from sentinel import razorpay as rz

    sent = {}

    class Stub(rz.NotesOnlyClient):
        def __init__(self):
            self._auth, self._timeout, self.calls = "x", 1, 0

        def _request(self, method, path, payload=None, **kw):
            sent["payload"] = payload
            return {"notes": (payload or {}).get("notes", {})}

    theirs = {"buyer_id": "b1", "device_fp": "FP9", "invoice": "INV-77"}
    Stub().annotate_order("order_1", {"sentinel_score": "91"}, existing=theirs)
    out = sent["payload"]["notes"]
    for k, v in theirs.items():
        assert out.get(k) == v, f"annotate destroyed merchant key {k!r}"
    assert out.get("sentinel_score") == "91", "verdict was not written"

    # A full notes object: ours must yield, not theirs. Clear the capture
    # first, or this asserts against the previous call's payload.
    sent.clear()
    full = {f"their_{i}": str(i) for i in range(rz.MAX_NOTE_KEYS)}
    Stub().annotate_order("order_2", {"sentinel_score": "91"}, existing=full)
    wrote = sent.get("payload")
    assert wrote is None, (
        "annotate wrote to a full notes object - merchant data would be lost")
    return f"{len(theirs)} merchant keys preserved; ours yield when notes are full"


# --------------------------------------------------------------------------
@check("a marketplace is cleared, a recruiter is not - by link type, not shape")
def test_identity_star_vs_marketplace():
    """The distinction the verifier was blind to.

    decoy_aggregator and recruiter_star are the SAME SHAPE - a hub with spokes,
    no spoke-to-spoke link. Only what they share differs: a platform shares an
    IP with its sellers, a recruiter shares a settlement account or a device.
    Clearing a star on shape alone cleared the criminals too.
    """
    import csv as _csv
    from collections import defaultdict as _dd

    with open(ROOT / "data" / "truth.csv", encoding="utf-8") as f:
        truth = {r["merchant_id"]: r for r in _csv.DictReader(f)}
    groups = _dd(set)
    for m, r in truth.items():
        if r["gt_ring_id"]:
            groups[r["gt_ring_id"]].add(m)

    verdicts, *_ = analyse(quiet=True)

    def best(mem):
        hits = [(len(set(v.ring.members) & mem), v) for v in verdicts
                if set(v.ring.members) & mem]
        return max(hits, key=lambda h: h[0])[1].score if hits else 0

    stars = [best(m) for g, m in groups.items()
             if truth[next(iter(m))]["gt_ring_type"] == "recruiter_star"]
    aggs = [best(m) for g, m in groups.items()
            if truth[next(iter(m))]["gt_ring_type"] == "decoy_aggregator"]
    assert stars and aggs, "recruiter_star / decoy_aggregator not planted"
    assert min(stars) >= REVIEW_AT, (
        f"criminal recruiter stars not flagged: {sorted(stars)}")
    assert max(aggs) < REVIEW_AT, (
        f"legitimate aggregator flagged: {sorted(aggs)}")
    return (f"recruiters {min(stars)}-{max(stars)} flagged, "
            f"aggregators {max(aggs)} cleared - same shape, different link")


# --------------------------------------------------------------------------
@check("onboarding velocity separates a recruiter from a chain - both ways")
def test_tenure_separates_recruiter_from_chain():
    """The signal that cracked recruiter_quiet, and the trap that guards it.

    Batch signup is the discriminator: mules are onboarded in days, outlets
    open over years. But a real brand opening several stores in one week is a
    batch too, which is why decoy_simultaneous_launch exists. BOTH halves are
    asserted here - catching recruiters is worthless if it costs honest chains.

    recruiter_quiet is NOT fully solved: two of five still slip under the line,
    and that is stated rather than rounded away.
    """
    import csv as _csv
    from collections import defaultdict as _dd

    with open(ROOT / "data" / "truth.csv", encoding="utf-8") as f:
        truth = {r["merchant_id"]: r for r in _csv.DictReader(f)}
    groups = _dd(set)
    for m, r in truth.items():
        if r["gt_ring_id"]:
            groups[r["gt_ring_id"]].add(m)
    verdicts, *_ = analyse(quiet=True)

    def best(mem):
        hits = [(len(set(v.ring.members) & mem), v) for v in verdicts
                if set(v.ring.members) & mem]
        return max(hits, key=lambda h: h[0])[1].score if hits else 0

    def scores(kind):
        return [best(m) for g, m in groups.items()
                if truth[next(iter(m))]["gt_ring_type"] == kind]

    quiet = scores("recruiter_quiet")
    launch = scores("decoy_simultaneous_launch")
    franchise = scores("decoy_franchise")
    assert quiet and launch and franchise, "classes not planted"

    caught = sum(1 for x in quiet if x >= REVIEW_AT)
    assert caught >= 3, (
        f"tenure no longer catches quiet recruiters: {sorted(quiet)}")
    assert all(x < REVIEW_AT for x in launch), (
        f"a legitimate simultaneous launch was flagged: {sorted(launch)} - the "
        f"tenure signal has moved the problem onto honest merchants")
    assert all(x < REVIEW_AT for x in franchise), (
        f"a franchise chain was flagged: {sorted(franchise)}")
    return (f"quiet recruiters {caught}/{len(quiet)} caught "
            f"(was 0/5 before onboarding data); simultaneous launches "
            f"{max(launch)} and franchises {max(franchise)} all cleared")


# --------------------------------------------------------------------------
@check("a hostile merchant name cannot inject markup into the case file")
def test_report_escapes_everything():
    """report.html is opened by a human reviewer. Merchant names come from
    merchants, who choose them.

    An audit found one unescaped field - the auto-expiry timestamp - which was
    missed precisely because WE generate it. This drives a payload through every
    string the renderer touches and asserts no executable tag survives.
    """
    import re as _re
    from sentinel.report import _case
    from sentinel.types import Ring as _Ring, Verdict as _Verdict

    payload = ('<img src=x onerror=alert(1)><script>steal()</script>'
               '"onmouseover="evil()')
    ring = _Ring(payload, (payload, "m2", "m3"),
                 ((payload, "m2", 0.9, ("bank_account",)),))
    v = _Verdict(ring=ring, score=90, raw_score=90, reasons=[payload],
                 exonerations=[payload], signals={"money": 0.9})
    out = _case(v, {"action": payload, "expires_at": payload,
                    "reversal": payload, "gates_that_stopped_escalation": [payload]},
                {payload: {"name": payload}, "m2": {"name": payload},
                 "m3": {"name": "x"}}, 100)

    ours = r"div|span|ul|li|h3|table|tr|td|th|i"
    live = _re.findall(rf"<(?!/?(?:{ours})[ >])[a-zA-Z][^>]*>", out)
    assert not live, f"unescaped markup reached the page: {live[:3]}"
    assert "&lt;" in out, "nothing was escaped at all - check the test itself"
    return "payload driven through name, reasons, exonerations, gates, expiry"


# --------------------------------------------------------------------------
@check("the generator's volume leak is still what the README says it is")
def test_volume_leak_is_documented_accurately():
    """A guard on an ADMISSION, not a capability.

    Our synthetic criminals transact far less than our synthetic honest
    merchants, which flatters nine of ten ring classes. The README states the
    medians and the balanced accuracy a single threshold achieves. If the
    generator ever changes, those figures go stale like every other number in
    this repo has, so they are asserted here.

    Found by trying a merchant-level model, which scored a suspicious PR-AUC of
    1.000 and turned out to have learned transaction count alone.
    """
    import statistics as _st

    truth = evalkit.load_truth()
    tbm = by_merchant(load_txns())
    ring = [len(tbm.get(r["merchant_id"], [])) for r in truth
            if r["gt_is_fraud"] == "1"]
    legit = [len(tbm.get(r["merchant_id"], [])) for r in truth
             if r["gt_is_fraud"] != "1"]
    assert ring and legit, "no labelled merchants"
    rm, lm = _st.median(ring), _st.median(legit)

    # The leak is real and the README quantifies it. Assert the shape, with
    # enough tolerance that a reseed does not fail this, but little enough that
    # a genuine fix does.
    assert rm < lm, (
        f"criminals no longer transact less than legitimate merchants "
        f"({rm} vs {lm}) - the README's volume-leak section is out of date")
    best = max(((sum(1 for x in ring if x < t) / len(ring)
                 + sum(1 for x in legit if x >= t) / len(legit)) / 2
                for t in range(1, 400)))
    assert best > 0.75, (
        f"a single transaction-count threshold now only reaches {best:.3f} "
        f"balanced accuracy - the leak has shrunk and the README overstates it")
    return (f"criminals median {rm} vs legitimate {lm}; one threshold reaches "
            f"{best:.3f} balanced accuracy - README's figures still hold")


# --------------------------------------------------------------------------
@check("Jaccard matching is correct on hand-built sets")
def test_jaccard():
    assert evalkit.jaccard([], []) == 0.0
    assert evalkit.jaccard("abc", "abc") == 1.0
    assert evalkit.jaccard("ab", "cd") == 0.0
    assert abs(evalkit.jaccard("abc", "abcd") - 0.75) < 1e-9
    assert abs(evalkit.jaccard("abcd", "cdef") - (2 / 6)) < 1e-9

    # The one-to-one property the 0.5 cutoff buys: two disjoint true rings
    # cannot both match one prediction.
    pred = set("abcd")
    a, b = set("abcx"), set("defg")
    assert evalkit.jaccard(pred, a) >= 0.5 and evalkit.jaccard(pred, b) < 0.5
    return "including the one-to-one property of the 0.5 cutoff"


# --------------------------------------------------------------------------
@check("verification can only ever lower a score")
def test_verifier_only_subtracts():
    from sentinel.types import Ring
    ring = Ring("R0", ("a", "b", "c"), ())
    Verdict(ring=ring, score=40, raw_score=80)          # fine
    try:
        Verdict(ring=ring, score=90, raw_score=80)
    except ValueError:
        return "Verdict rejects score > raw_score at construction"
    raise AssertionError("Verdict allowed verification to RAISE a score")


def main():
    print(f"\n  {'RING SENTINEL - CHECKS':<62}\n  {'-' * 62}")
    failed = 0
    for name, fn in OK:
        try:
            detail = fn()
            print(f"  pass  {name}")
            if detail:
                print(f"        {detail}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}\n        {exc}")
    print(f"  {'-' * 62}\n  {len(OK) - failed}/{len(OK)} passed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
