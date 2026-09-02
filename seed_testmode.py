"""Populate a Razorpay TEST-MODE account with a small buyer population.

    RAZORPAY_KEY_ID=rzp_test_... RAZORPAY_KEY_SECRET=... python seed_testmode.py

This lives OUTSIDE sentinel/ on purpose. The detector package cannot create a
Razorpay object of any kind; only this development script can. Keeping the
creating code and the deciding code in separate packages is what lets
test_pipeline.py assert a capability surface that means something.

WHY ORDERS, NOT PAYMENTS. Probed against a real test account on 2026-09-02:

    GET  /payments        200      POST /orders                200
    GET  /orders          200      POST /customers             200
    GET  /customers       200      POST /payment_links         200
    GET  /settlements     200      POST /payments/create/ajax  401
    GET  /disputes        200      POST /payments  (S2S)       401
                                   POST /payments/create/upi   404

Payment objects cannot be minted headlessly - server-to-server payment
creation needs Razorpay to enable S2S on the account, and every other route
requires a browser checkout. Orders are real objects created over the real
network, and they carry `notes`, which is where a real integration would pass
device and instrument metadata anyway.

WHY THE ATTRIBUTES TRAVEL IN NOTES. Razorpay de-duplicates customers on
contact and email, so two distinct customers cannot share a phone number -
which is exactly the linkage a ring would exhibit. Notes are the honest
substitute: in production these fields arrive on the payment object (card
fingerprint, device, VPA, email). Stated plainly rather than glossed over.
"""

from __future__ import annotations

import random
import sys

from sentinel.razorpay import API, MAX_NOTE_KEYS, credentials  # noqa: F401
from sentinel.razorpay import NotesOnlyClient  # noqa: F401  (surface check)

import base64
import json
import urllib.error
import urllib.request

RNG = random.Random(7)

FIRST = ["SHREE", "SRI", "NEW", "ROYAL", "STAR", "GOLDEN", "PRIME", "ANAND"]
TRADE = ["TEXTILES", "TRADERS", "STORES", "AGENCIES", "ELECTRONICS", "FOODS"]


def _name():
    return f"{RNG.choice(FIRST)} {RNG.choice(TRADE)}"


def _confusable(name):
    for src, dst in [("I", "l"), ("O", "0"), ("S", "5"), ("A", "4"), ("E", "3")]:
        if src in name:
            i = name.index(src)
            return name[:i] + dst + name[i + 1:]
    return name + " CO"


def _ref(n):
    return "".join(RNG.choice("0123456789") for _ in range(n))


class Seeder:
    """Creates orders. Deliberately a separate class from the detector's."""

    def __init__(self):
        creds = credentials()
        if creds is None:
            raise SystemExit(
                "No test-mode credentials. Set RAZORPAY_KEY_ID and "
                "RAZORPAY_KEY_SECRET (the key id must start with rzp_test_).")
        token = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
        self._auth = f"Basic {token}"
        self.created = 0

    def create_order(self, amount_paise, notes, receipt):
        body = json.dumps({"amount": amount_paise, "currency": "INR",
                           "receipt": receipt, "notes": notes}).encode()
        req = urllib.request.Request(f"{API}/orders", data=body, method="POST")
        req.add_header("Authorization", self._auth)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                self.created += 1
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:180]
            print(f"    order failed ({exc.code}): {detail}")
            return None


def buyer_notes(buyer, attrs, day):
    """One buyer's linkage attributes, as an order would carry them."""
    notes = {"buyer_id": buyer, "buyer_name": attrs["name"], "day": str(day)}
    for k in ("device_fp", "upi_vpa", "bank_account", "ip_prefix"):
        if attrs.get(k):
            notes[k] = attrs[k]
    return notes


def main():
    seeder = Seeder()
    print(f"  Seeding test-mode account (key {credentials()[0]})\n")

    plan = []          # (buyer_id, attrs, [(day, amount_paise), ...])

    # --- ring: shared device, amounts parked just under Rs 50,000 ----------
    device = f"FP{_ref(10)}"
    for i in range(4):
        bid = f"ring1_b{i}"
        plan.append((bid, {"name": _name(), "device_fp": device,
                           "ip_prefix": f"49.{_ref(2)}.{_ref(2)}"},
                     [(30 + i, RNG.randrange(48_200_00, 49_900_00)) for _ in range(2)]))

    # --- ring: shared UPI handle, lookalike names -------------------------
    vpa, base = f"{_ref(6)}@okaxis", _name()
    for i in range(3):
        bid = f"ring2_b{i}"
        plan.append((bid, {"name": base if i == 0 else _confusable(base),
                           "upi_vpa": vpa},
                     [(60, RNG.randrange(9_000_00, 30_000_00)) for _ in range(2)]))

    # --- decoy: a family sharing one device, trading normally -------------
    fam = f"FP{_ref(10)}"
    for i in range(3):
        plan.append((f"decoy_b{i}", {"name": _name(), "device_fp": fam},
                     [(10 + i * 20, RNG.randrange(1_200_00, 8_000_00))]))

    # --- ordinary buyers, all unique --------------------------------------
    for i in range(8):
        plan.append((f"solo_b{i}",
                     {"name": _name(), "device_fp": f"FP{_ref(10)}",
                      "ip_prefix": f"49.{_ref(2)}.{_ref(2)}"},
                     [(RNG.randrange(1, 90), RNG.randrange(500_00, 40_000_00))]))

    total = sum(len(o) for _b, _a, o in plan)
    print(f"  {len(plan)} buyers, {total} orders to create "
          f"(2 rings, 1 decoy, 8 ordinary)\n")

    n = 0
    for bid, attrs, orders in plan:
        for day, amount in orders:
            n += 1
            res = seeder.create_order(amount, buyer_notes(bid, attrs, day),
                                      f"sentinel_{bid}_{n}")
            if res:
                print(f"    {res['id']}  {bid:12} Rs {amount / 100:>10,.2f}")

    print(f"\n  created {seeder.created}/{total} orders")
    print(f"  now run:  python live.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
