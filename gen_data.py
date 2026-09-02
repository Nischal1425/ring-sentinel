"""Synthetic merchant population with planted abuse rings — and planted innocents.

Written BEFORE any detector code exists. That ordering is the point: if the
scenarios were authored while looking at the features, the synthetic recall
number would only measure "did I implement my own spec".

Two of the seven ring types were planted to be MISSED by a shared-attribute
detector, on purpose:

  * slow_burn   shares one weak attribute and never bursts
  * timing_only shares NO attribute at all - only synchronised timing

Both are now found, and the history matters more than the score. `timing_only`
was invisible by construction: no attribute graph can represent a group that
shares no attribute. Rather than keep reporting 0/3 as a permanent blind spot,
it drove a second relation - temporal co-occurrence against a popularity-aware
null model, in sentinel/temporal.py. Planting a failure you cannot yet detect
is how you find out what to build next.

`slow_burn` turned out to be detectable after all, which is its own lesson:
the guess about which classes would be hard was only half right.

The four decoy classes are legitimate clusters that share attributes for
innocent reasons. They exist to generate false positives. A detector that
flags a franchise chain has not understood the problem.

Ground truth lives in data/truth.csv, a sidecar the pipeline never opens.
Money is integer paise everywhere; rupees appear at the CSV boundary only.

    python gen_data.py
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

RNG = random.Random(42)
START = date(2026, 3, 1)
DAYS = 184  # 1 Mar - 31 Aug 2026
OUT = Path(__file__).parent / "data"

N_LEGIT = 1100
MIN_RING = 3

# The class the detector still only partly catches. Kept as a constant because
# the banner has already drifted twice: it once said "2 expected to be missed"
# (slow_burn and timing_only, both now caught), then "1 not separable" - which
# stopped being true the moment onboarding data was added. recruiter_quiet now
# runs about 3 of 5, so it is PARTIAL, not missed and not solved.
# test_pipeline.py asserts a floor of 3 of 5 caught, and that no decoy is
# flagged. It currently runs 4 of 5; the floor is deliberately below that.
PARTIAL = ("recruiter_quiet",)

# Diwali lands early November 2026, so the festive build runs through August.
# Legitimate merchants burst together here. Any burst detector that does not
# compare against the portfolio baseline will flag the whole population.
SEASON = {3: 1.00, 4: 1.06, 5: 1.12, 6: 1.07, 7: 1.14, 8: 1.62}

# Amounts a real small merchant sees, in paise. Wide and lumpy on purpose:
# a narrow band would make Benford and structuring trivially separable.
TICKET = [(299_00, 1499_00), (1499_00, 8999_00), (8999_00, 74999_00)]

CITIES = ["BLR", "DEL", "MUM", "HYD", "PNQ", "CHN", "JAI", "AMD", "LKO", "KOL"]
TRADES = ["TEXTILES", "TRADERS", "ENTERPRISES", "STORES", "AGENCIES", "SUPPLY CO",
          "GARMENTS", "ELECTRONICS", "HARDWARE", "FOODS", "PHARMA", "MOTORS"]
FIRST = ["SHREE", "SRI", "NEW", "ROYAL", "STAR", "GOLDEN", "PRIME", "UNITY",
         "ANAND", "KRISHNA", "LAXMI", "VENKAT", "ARORA", "MEHTA", "IQBAL"]

FREEMAIL = ["gmail.com", "yahoo.co.in", "outlook.com", "rediffmail.com"]
COMMON_DEVICE = ["Windows", "iOS Device", "Android", "MacOS"]


def ref(n: int) -> str:
    return "".join(RNG.choice("0123456789") for _ in range(n))


def schedule(lo_start, hi_start, lo_period, hi_period, jitter=1):
    """A rhythm unique to ONE ring instance.

    Every ring type used to share a single hardcoded schedule, so all six
    refund mills traded on exactly the same days as each other. That is not
    how independent criminal groups behave, and it silently made distinct
    rings look temporally coordinated - which flattered the co-timing signal
    and merged separate rings into blobs. Each ring now gets its own start,
    its own period and per-day jitter.
    """
    start = RNG.randrange(lo_start, hi_start)
    period = RNG.randrange(lo_period, hi_period)
    days = []
    for d in range(start, DAYS, period):
        days.append(min(DAYS - 1, max(0, d + RNG.randint(-jitter, jitter))))
    return days


def merchant_name() -> str:
    return f"{RNG.choice(FIRST)} {RNG.choice(TRADES)}"


def confusable(name: str) -> str:
    """A lookalike of `name`: homoglyph swap or a single-character edit.

    Deliberately mixes Unicode confusables with plain ASCII typos, so a
    detector that only folds homoglyphs catches part of the class and not
    all of it.
    """
    swaps = [("I", "l"), ("O", "0"), ("S", "5"), ("A", "4"), ("E", "3")]
    src, dst = RNG.choice(swaps)
    if src in name:
        i = name.index(src)
        return name[:i] + dst + name[i + 1:]
    return name + RNG.choice(["S", " CO", "  "])


def signup(*, batch=None, spread=None, oldest=1500):
    """Day the account was opened, relative to the start of the window.

    Negative = opened before the book begins. Account-creation velocity is one
    of the standard mule signals in the industry, and it is the field that
    separates a recruiter from a group company: mules are onboarded together
    in days, outlets open over years.

    Added only after bench_elliptic2.py and the recruiter_quiet class showed
    the transaction-side data could not separate them at all. RiskPulse, a
    competing submission, independently trains on `account_age_days`, which is
    reassurance that this is a real feature and not one invented to make our
    own planted case detectable.
    """
    if batch is not None:
        return batch + RNG.randint(0, spread if spread is not None else 5)
    return -RNG.randrange(30, oldest)


def new_attrs(seed_pool=None, *, signup_day=None):
    """A merchant's linkage attributes. Most are unique; some are common."""
    return {
        "signup_day": str(signup_day if signup_day is not None else signup()),
        "bank_account": f"AC{ref(11)}",
        # Most merchants sit on a shared free-mail domain. That is exactly the
        # high-document-frequency value the df cap has to survive.
        "email_domain": RNG.choice(FREEMAIL) if RNG.random() < 0.82 else f"{ref(5)}.co.in",
        "device_fp": (RNG.choice(COMMON_DEVICE) if RNG.random() < 0.35
                      else f"FP{ref(10)}"),
        "ip_prefix": f"49.{RNG.randrange(1, 255)}.{RNG.randrange(1, 255)}",
        "phone": f"9{ref(9)}",
        "upi_vpa": f"{ref(6)}@okaxis",
    }


class World:
    def __init__(self):
        self.merchants = []   # dicts with id, name, attrs
        self.truth = {}       # merchant_id -> (ring_id, ring_type, is_fraud)
        self.txns = []
        self._n = 0

    def add(self, name, attrs, ring_id="", ring_type="legit", fraud=0):
        mid = f"acc_{self._n:05d}"
        self._n += 1
        self.merchants.append({"merchant_id": mid, "name": name, **attrs})
        self.truth[mid] = (ring_id, ring_type, fraud)
        return mid

    def trade(self, mid, day, amount, *, refund=0, chargeback=0):
        self.txns.append({
            "txn_id": f"pay_{len(self.txns):06d}",
            "merchant_id": mid,
            "date": (START + timedelta(days=day)).isoformat(),
            "amount_paise": amount,
            "payer_card": f"card{ref(6)}",
            "is_refund": refund,
            "is_chargeback": chargeback,
        })

    def organic(self, mid, *, rate=0.55, band=None, cb=0.004, rf=0.03):
        """Ordinary trading: seasonal volume, lumpy amounts, a little churn."""
        lo, hi = band or RNG.choice(TICKET)
        for day in range(DAYS):
            month = (START + timedelta(days=day)).month
            if RNG.random() > rate * SEASON.get(month, 1.0) / 1.6:
                continue
            for _ in range(RNG.randrange(1, 4)):
                self.trade(mid, day, RNG.randrange(lo, hi),
                           refund=int(RNG.random() < rf),
                           chargeback=int(RNG.random() < cb))


# --------------------------------------------------------------- ring classes
# Each returns the ring's member ids. Written before the detector existed.

def ring_mule_fanout(w, rid):
    """Many fresh accounts drain through one bank account. Classic mule net."""
    shared = f"AC{ref(11)}"
    device = f"FP{ref(10)}"
    members = []
    batch = -RNG.randrange(20, 90)          # opened together, weeks before use
    for _ in range(RNG.randrange(6, 13)):
        a = new_attrs(signup_day=signup(batch=batch, spread=6))
        a["bank_account"] = shared
        if RNG.random() < 0.7:
            a["device_fp"] = device
        members.append(w.add(merchant_name(), a, rid, "mule_fanout", 1))
    burst = RNG.randrange(120, DAYS - 10)
    for mid in members:
        for _ in range(RNG.randrange(14, 30)):
            w.trade(mid, burst + RNG.randrange(0, 4), RNG.randrange(18000_00, 49000_00))
    return members


def ring_device_farm(w, rid):
    """One device, one IP, many accounts, tightly synchronised activity."""
    device, ip = f"FP{ref(10)}", f"49.{RNG.randrange(1, 255)}.{RNG.randrange(1, 255)}"
    members = []
    batch = -RNG.randrange(20, 90)
    for _ in range(RNG.randrange(5, 10)):
        a = new_attrs(signup_day=signup(batch=batch, spread=5))
        a["device_fp"], a["ip_prefix"] = device, ip
        members.append(w.add(merchant_name(), a, rid, "device_farm", 1))
    for day in schedule(80, 150, 4, 11):
        for mid in members:
            for _ in range(RNG.randrange(3, 8)):
                w.trade(mid, day, RNG.randrange(2500_00, 9500_00))
    return members


def ring_refund_mill(w, rid):
    """Volume in, most of it refunded straight back out. Laundering shape."""
    shared_upi = f"{ref(6)}@okaxis"
    members = []
    batch = -RNG.randrange(15, 70)
    for _ in range(RNG.randrange(4, 8)):
        a = new_attrs(signup_day=signup(batch=batch, spread=7))
        a["upi_vpa"] = shared_upi
        members.append(w.add(merchant_name(), a, rid, "refund_mill", 1))
    days = schedule(40, 80, 2, 5)
    for mid in members:
        for day in days:
            amt = RNG.randrange(9000_00, 38000_00)
            w.trade(mid, day, amt)
            if RNG.random() < 0.72:
                w.trade(mid, day + 1, amt, refund=1)
    return members


def ring_structuring(w, rid):
    """Amounts parked just under a reporting threshold, over and over."""
    shared = f"AC{ref(11)}"
    members = []
    batch = -RNG.randrange(15, 70)
    for _ in range(RNG.randrange(4, 8)):
        a = new_attrs(signup_day=signup(batch=batch, spread=8))
        a["bank_account"] = shared
        members.append(w.add(merchant_name(), a, rid, "structuring", 1))
    for mid in members:
        for day in range(40, DAYS, 3):
            # just below ₹50,000
            w.trade(mid, day, RNG.randrange(48200_00, 49900_00))
    return members


def ring_name_twins(w, rid):
    """Lookalike names, one weak shared attribute. Tests name similarity."""
    base = merchant_name()
    ip = f"49.{RNG.randrange(1, 255)}.{RNG.randrange(1, 255)}"
    members = []
    for i in range(RNG.randrange(4, 8)):
        a = new_attrs()
        a["ip_prefix"] = ip
        nm = base if i == 0 else confusable(base)
        members.append(w.add(nm, a, rid, "name_twins", 1))
    for mid in members:
        w_ = RNG.randrange(80, 140)
        for day in range(w_, min(w_ + 40, DAYS)):
            if RNG.random() < 0.5:
                w.trade(mid, day, RNG.randrange(4000_00, 22000_00))
    return members


def ring_slow_burn(w, rid):
    """EXPECTED PARTIAL MISS.

    One weak shared attribute, no burst, low volume, spread over months.
    Deliberately sits near the noise floor of every structural signal.
    """
    ip = f"49.{RNG.randrange(1, 255)}.{RNG.randrange(1, 255)}"
    members = []
    for _ in range(RNG.randrange(3, 6)):
        a = new_attrs()
        a["ip_prefix"] = ip
        members.append(w.add(merchant_name(), a, rid, "slow_burn", 1))
    days = schedule(0, 25, 9, 18, jitter=3)
    for mid in members:
        for day in days:
            w.trade(mid, day, RNG.randrange(6000_00, 21000_00))
    return members


def ring_timing_only(w, rid):
    """EXPECTED MISS, BY CONSTRUCTION.

    Shares no attribute with anyone. Coordinated purely in time. A graph built
    on shared attributes cannot represent this ring, let alone score it. It is
    planted so the scorecard has to report a structural blind spot rather than
    a clean sweep - and so the README can say what would fix it (a temporal
    co-occurrence edge, which is not built here).
    """
    members = [w.add(merchant_name(), new_attrs(), rid, "timing_only", 1)
               for _ in range(RNG.randrange(4, 8))]
    for day in schedule(60, 130, 7, 15):
        for mid in members:
            for _ in range(RNG.randrange(4, 9)):
                w.trade(mid, day, RNG.randrange(11000_00, 31000_00))
    return members


def ring_layered_chain(w, rid):
    """A LAYERED mule network: no globally shared attribute at all.

    Added after bench_elliptic2.py showed 94% of real labelled fraud subgraphs
    are TREES while every ring here was a clique. A shared value linking all
    members produces a clique for free, which is not how a layered network
    launders: a recruiter knows two mules, each mule knows the next, and no
    single identifier touches everyone.

    Each ADJACENT PAIR shares one attribute; non-adjacent members share
    nothing. The result is a path, not a clique - exactly the shape our 2-core
    is designed to strip.
    """
    members = []
    for _ in range(RNG.randrange(5, 9)):
        members.append(w.add(merchant_name(), new_attrs(), rid, "layered_chain", 1))
    links = ["bank_account", "device_fp", "upi_vpa", "phone"]
    for i in range(len(members) - 1):
        attr = links[i % len(links)]
        value = {"bank_account": f"AC{ref(11)}", "device_fp": f"FP{ref(10)}",
                 "upi_vpa": f"{ref(6)}@okaxis", "phone": f"9{ref(9)}"}[attr]
        for m in (members[i], members[i + 1]):
            w.merchants[[x["merchant_id"] for x in w.merchants].index(m)][attr] = value
    # Money still behaves badly even though the shape is innocent.
    days = schedule(50, 110, 2, 6)
    for mid in members:
        for day in days:
            w.trade(mid, day, RNG.randrange(48200_00, 49900_00))
    return members


def ring_recruiter_star(w, rid):
    """A recruiter and the accounts they onboarded. A TREE, and worse: a star.

    Structurally this is the decoy_aggregator: one hub, many spokes, no
    spoke-to-spoke link. It is planted precisely because it is structurally
    innocent - the only thing separating it from a legitimate marketplace is
    BEHAVIOUR. If the detector can only tell them apart by shape, it cannot
    tell them apart at all.
    """
    batch = -RNG.randrange(25, 100)
    hub = w.add(merchant_name(), new_attrs(signup_day=batch - RNG.randrange(20, 90)),
                rid, "recruiter_star", 1)
    hub_row = w.merchants[[x["merchant_id"] for x in w.merchants].index(hub)]
    members = [hub]
    for i in range(RNG.randrange(5, 10)):
        a = new_attrs(signup_day=signup(batch=batch, spread=9))
        # Each spoke shares ONE attribute with the hub and nothing with peers.
        attr = ["device_fp", "phone", "upi_vpa", "bank_account"][i % 4]
        a[attr] = hub_row[attr]
        members.append(w.add(merchant_name(), a, rid, "recruiter_star", 1))
    onboarding = RNG.randrange(60, 150)
    for mid in members:
        for _ in range(RNG.randrange(10, 22)):
            day = min(DAYS - 1, onboarding + RNG.randrange(0, 6))
            w.trade(mid, day, RNG.randrange(21000_00, 46000_00),
                    refund=int(RNG.random() < 0.45))
    return members


def ring_recruiter_quiet(w, rid):
    """The hardest case in this file: a criminal star that BEHAVES NORMALLY.

    ring_recruiter_star is already structurally identical to decoy_aggregator.
    This one goes further and removes the behavioural tell too: no burst, no
    structuring, no refund spike, ordinary seasonal trading. Shape cannot
    separate it from a marketplace, and neither can conduct.

    One difference remains, and it is the real-world one. A marketplace's
    sellers keep their OWN settlement accounts and share only the platform's
    IP - a weak, infrastructural link. A recruiter's mules sit on the
    RECRUITER'S bank account and device. So the distinguishing evidence is not
    the shape of the star or how it trades, but WHAT KIND of thing is shared.

    If the detector cannot make that distinction it should flag the aggregator
    too, and the scorecard will say so.
    """
    batch = -RNG.randrange(25, 100)
    hub = w.add(merchant_name(), new_attrs(signup_day=batch - RNG.randrange(20, 90)),
                rid, "recruiter_quiet", 1)
    hub_row = w.merchants[[x["merchant_id"] for x in w.merchants].index(hub)]
    members = [hub]
    for i in range(RNG.randrange(5, 9)):
        a = new_attrs(signup_day=signup(batch=batch, spread=10))
        a[["bank_account", "device_fp"][i % 2]] = hub_row[
            ["bank_account", "device_fp"][i % 2]]
        members.append(w.add(merchant_name(), a, rid, "recruiter_quiet", 1))
    for mid in members:
        w.organic(mid, rate=0.5)          # indistinguishable from honest trade
    return members


RING_TYPES = [ring_mule_fanout, ring_device_farm, ring_refund_mill,
              ring_structuring, ring_name_twins, ring_slow_burn,
              ring_timing_only, ring_layered_chain, ring_recruiter_star,
              ring_recruiter_quiet]


# -------------------------------------------------------------- decoy classes
# Legitimate. Share attributes for honest reasons. Must NOT be flagged.

def decoy_franchise(w, did):
    """One owner, several outlets. Same bank account and phone, legitimately."""
    bank, phone = f"AC{ref(11)}", f"9{ref(9)}"
    brand = merchant_name()
    members = []
    for i in range(RNG.randrange(4, 9)):
        # Outlets open over years, one at a time. This is what makes a real
        # chain look different from a batch of mules.
        a = new_attrs(signup_day=-RNG.randrange(200, 1400))
        a["bank_account"], a["phone"] = bank, phone
        members.append(w.add(f"{brand} {CITIES[i % len(CITIES)]}", a, did, "decoy_franchise", 0))
    for mid in members:
        w.organic(mid, rate=0.7)
    return members


def decoy_aggregator(w, did):
    """A reseller platform: a hub plus its sellers. Star, not clique."""
    hub_ip = f"49.{RNG.randrange(1, 255)}.{RNG.randrange(1, 255)}"
    hub = w.add(merchant_name(), {**new_attrs(), "ip_prefix": hub_ip}, did, "decoy_aggregator", 0)
    members = [hub]
    for _ in range(RNG.randrange(8, 16)):
        a = new_attrs()
        a["ip_prefix"] = hub_ip  # everyone talks to the hub, nobody to each other
        members.append(w.add(merchant_name(), a, did, "decoy_aggregator", 0))
    for mid in members:
        w.organic(mid, rate=0.5)
    return members


def decoy_family(w, did):
    """Small family business: two or three merchants, one shared device."""
    device = f"FP{ref(10)}"
    members = []
    for _ in range(RNG.randrange(2, 4)):
        a = new_attrs()
        a["device_fp"] = device
        members.append(w.add(merchant_name(), a, did, "decoy_family", 0))
    for mid in members:
        w.organic(mid, rate=0.45)
    return members


def decoy_festive(w, did):
    """Legitimate merchants who all burst for Diwali stocking, together.

    The portfolio bursts with them - which is the whole point. Burst measured
    against an absolute baseline flags these; burst measured against the
    same-day portfolio baseline does not.
    """
    members = [w.add(merchant_name(), new_attrs(), did, "decoy_festive", 0)
               for _ in range(RNG.randrange(5, 10))]
    for mid in members:
        w.organic(mid, rate=0.4)
        for day in range(150, DAYS):
            for _ in range(RNG.randrange(2, 6)):
                w.trade(mid, day, RNG.randrange(15000_00, 45000_00))
    return members


def decoy_simultaneous_launch(w, did):
    """A real chain opening several outlets in the SAME WEEK. Legitimate.

    This exists to stop the new tenure signal being a free win. Batch signup is
    the discriminator between a recruiter and a group company - but a brand
    entering a new city opens five stores at once, shares one settlement
    account, and looks exactly like a mule batch on that axis alone.

    If the detector flags this, the tenure signal has not solved the recruiter
    problem, it has only moved it onto honest merchants. The scorecard reports
    it separately for that reason.
    """
    bank, phone = f"AC{ref(11)}", f"9{ref(9)}"
    brand = merchant_name()
    launch = -RNG.randrange(60, 400)
    members = []
    for i in range(RNG.randrange(5, 8)):
        a = new_attrs(signup_day=launch + RNG.randint(0, 6))
        a["bank_account"], a["phone"] = bank, phone
        members.append(w.add(f"{brand} {CITIES[i % len(CITIES)]}", a, did,
                             "decoy_simultaneous_launch", 0))
    for mid in members:
        w.organic(mid, rate=0.6)
    return members


DECOY_TYPES = [decoy_franchise, decoy_aggregator, decoy_family, decoy_festive,
               decoy_simultaneous_launch]


def main():
    w = World()

    # The legitimate mass. This is the giant component, and discarding it is
    # correct - rings are small disjoint clusters, not the bulk of the graph.
    for _ in range(N_LEGIT):
        w.organic(w.add(merchant_name(), new_attrs()))

    for i in range(50):
        RING_TYPES[i % len(RING_TYPES)](w, f"ring_{i:03d}")
    for i in range(20):
        DECOY_TYPES[i % len(DECOY_TYPES)](w, f"decoy_{i:03d}")

    OUT.mkdir(exist_ok=True)
    RNG.shuffle(w.txns)

    with open(OUT / "merchants.csv", "w", newline="", encoding="utf-8") as f:
        cols = ["merchant_id", "name", "signup_day", "bank_account",
                "email_domain", "device_fp", "ip_prefix", "phone", "upi_vpa"]
        wr = csv.DictWriter(f, cols)
        wr.writeheader()
        wr.writerows(w.merchants)

    with open(OUT / "transactions.csv", "w", newline="", encoding="utf-8") as f:
        cols = ["txn_id", "merchant_id", "date", "amount_paise", "payer_card",
                "is_refund", "is_chargeback"]
        wr = csv.DictWriter(f, cols)
        wr.writeheader()
        wr.writerows(w.txns)

    # The sidecar. Nothing in sentinel/ may open this file.
    with open(OUT / "truth.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["merchant_id", "gt_ring_id", "gt_ring_type", "gt_is_fraud"])
        for mid, (rid, rtype, fraud) in w.truth.items():
            wr.writerow([mid, rid, rtype, fraud])

    rings = {r for r, t, _ in w.truth.values()
             if r and not t.startswith(("decoy", "legit"))}
    decoys = {r for r, t, _ in w.truth.values() if t.startswith("decoy")}
    fraud_n = sum(1 for _, _, fr in w.truth.values() if fr)
    print(f"  merchants     {len(w.merchants):>7,}   {fraud_n} in rings, "
          f"{len(w.merchants) - fraud_n} legitimate")
    print(f"  transactions  {len(w.txns):>7,}")
    print(f"  rings planted {len(rings):>7}   across {len(RING_TYPES)} types "
          f"({len(PARTIAL)} only partly separable: {PARTIAL[0]})")
    print(f"  decoys        {len(decoys):>7}   legitimate clusters that share attributes")
    print(f"  truth sidecar {'data/truth.csv':>7}   nothing in sentinel/ may read it")


if __name__ == "__main__":
    main()
