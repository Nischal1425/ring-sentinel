"""Run the detector against the LIVE Razorpay test-mode API.

    RAZORPAY_KEY_ID=rzp_test_... RAZORPAY_KEY_SECRET=... python live.py
    python live.py --annotate      also write verdicts back to the orders

Nothing here is simulated. Orders are fetched over the network from Razorpay,
the graph is built from their real `notes`, and with --annotate the verdict is
written back through PATCH /orders/:id - the only write this system owns.

What this demonstrates, precisely:
  * the detector consumes real Razorpay API objects, not just our CSV
  * the responder's bounded action works end to end against real infrastructure

What it does NOT demonstrate, and nobody should claim it does:
  * a test account is ONE merchant, so these are rings of BUYERS, not rings of
    colluding merchants. The synthetic book in gen_data.py is where the
    merchant-collusion claim is measured, and its labels are ours.
  * 18 buyers is a demo, not an evaluation. The numbers that count come from
    score.py on the held-out half.

Run seed_testmode.py first if the account is empty.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from sentinel import detect, verify
from sentinel.graph import build
from sentinel.ingest import Portfolio, Txn, by_merchant, rupees
from sentinel.razorpay import NotesOnlyClient, available, verdict_notes
from sentinel.respond import DAILY_CAP, decide, record
from sentinel.types import Action, Verdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

LINKAGE = ("device_fp", "upi_vpa", "bank_account", "ip_prefix", "phone",
           "email_domain")


def from_orders(orders):
    """Real Razorpay orders -> the same shapes the offline pipeline uses.

    One entity per buyer. In production these attributes arrive on the payment
    object; in test mode they travel in the order's notes, which is where a
    real integration would put them anyway.
    """
    merchants, txns, order_ids = {}, [], {}
    for o in orders:
        notes = o.get("notes") or {}
        buyer = notes.get("buyer_id")
        if not buyer:
            continue                     # not ours - leave other people's data alone
        row = merchants.setdefault(buyer, {"merchant_id": buyer,
                                           "name": notes.get("buyer_name", "")})
        for attr in LINKAGE:
            if notes.get(attr):
                row[attr] = notes[attr]
        order_ids.setdefault(buyer, []).append(o["id"])
        txns.append(Txn(
            txn_id=o["id"], merchant_id=buyer,
            day=int(notes.get("day", 0)),
            amount=int(o["amount"]),
            payer_card="", is_refund=False, is_chargeback=False))
    return merchants, txns, order_ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--annotate", action="store_true",
                    help="write verdicts back to the orders")
    args = ap.parse_args()

    if not available():
        print("  No test-mode credentials. Set RAZORPAY_KEY_ID and "
              "RAZORPAY_KEY_SECRET.\n  Running offline is what run.py is for - "
              "this script will not pretend.")
        return 1

    client = NotesOnlyClient()
    orders = client.fetch_orders(count=100)
    print(f"  GET /v1/orders           {len(orders):>4} orders fetched live")

    merchants, txns, order_ids = from_orders(orders)
    notes_by_order = {o["id"]: (o.get("notes") or {}) for o in orders}
    if len(merchants) < 3:
        print("  Not enough tagged orders to form a ring. "
              "Run seed_testmode.py first.")
        return 1

    tbm = by_merchant(txns)
    portfolio = Portfolio.build(txns)
    rings, stats = build(merchants, txns_by_merchant=tbm)
    print(f"  entities                 {len(merchants):>4} buyers, "
          f"{len(txns)} orders")
    print(f"  graph                    {stats['edges']:>4} links, "
          f"{stats['rings']} candidate rings\n")

    fresh_state, budget, written = {}, DAILY_CAP, 0
    for ring in sorted(rings, key=lambda r: -len(r)):
        raw, signals, reasons = detect.score(ring, merchants, tbm, portfolio)
        discount, exonerations = verify.verify(ring, tbm, portfolio, signals, merchants)
        v = Verdict(ring=ring, score=round(raw * discount), raw_score=raw,
                    reasons=reasons, exonerations=exonerations, signals=signals)
        exposure = sum(t.amount for m in ring.members for t in tbm.get(m, []))
        action, gates = decide(v, exposure, fresh_state, budget)
        entry = record(v, action, exposure, gates)

        names = ", ".join(merchants[m].get("name", m) for m in ring.members[:3])
        print(f"  {ring.ring_id}  score {v.score:>3}  {len(ring)} buyers  "
              f"{rupees(exposure)}  [{action.value}]")
        print(f"      {names}")
        for r in v.reasons[:2]:
            print(f"      - {r}")
        for e in v.exonerations[:1]:
            print(f"      ~ {e}")
        for g in gates[:2]:
            print(f"      x gate: {g}")

        if args.annotate:
            notes = verdict_notes(v, entry)
            notes["sentinel_checked"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds")
            patched = 0
            for buyer in ring.members:
                for oid in order_ids.get(buyer, []):
                    client.annotate_order(oid, notes,
                                          existing=notes_by_order.get(oid))
                    patched += 1
            written += patched
            print(f"      -> PATCH /orders/:id on {patched} orders "
                  f"(notes only; no money verb exists on this client)")
        print()

    print(f"  API calls made           {client.calls:>4}"
          + (f",  {written} orders annotated" if args.annotate else ""))
    if not args.annotate:
        print("  Re-run with --annotate to write the verdicts back to Razorpay.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
