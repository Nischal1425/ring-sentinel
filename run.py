"""Ring Sentinel - one pass over the book.

    python run.py

Reads data/merchants.csv and data/transactions.csv, builds the shared-attribute
graph, scores every candidate ring, tries to exonerate each one, decides a
bounded action, and writes data/audit.jsonl plus report.html.

Never reads data/truth.csv. That file belongs to score.py alone.
"""

from __future__ import annotations

import sys

from sentinel import detect, verify
from sentinel.graph import DATA, build, load_merchants
from sentinel.ingest import Portfolio, by_merchant, load_txns, rupees
from sentinel.report import write as write_report
from sentinel.respond import run as respond
from sentinel.types import Verdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def analyse(*, quiet=False, only=None, co_timing=True, df_cap=None):
    """Run one pass. `only` restricts the book to a subset of merchant ids.

    The subset is applied to the merchants AND their transactions before the
    graph is built, so a split half is a genuinely separate book - not the
    same graph with some rows hidden at scoring time.
    """
    merchants = load_merchants()
    if only is not None:
        merchants = {m: r for m, r in merchants.items() if m in only}
    txns = [t for t in load_txns() if t.merchant_id in merchants]
    tbm = by_merchant(txns)
    portfolio = Portfolio.build(txns)
    from sentinel.graph import DF_CAP
    rings, stats = build(merchants, df_cap=DF_CAP if df_cap is None else df_cap,
                         txns_by_merchant=tbm if co_timing else None)

    verdicts, exposure = [], {}
    for ring in rings:
        raw, signals, reasons = detect.score(ring, merchants, tbm, portfolio)
        discount, exonerations = verify.verify(ring, tbm, portfolio, signals, merchants)
        verdicts.append(Verdict(
            ring=ring,
            score=round(raw * discount),
            raw_score=raw,
            reasons=reasons,
            exonerations=exonerations,
            signals=signals,
        ))
        exposure[ring.ring_id] = sum(
            t.amount for m in ring.members for t in tbm.get(m, []) if not t.is_refund)

    if not quiet:
        print(f"  merchants            {stats['merchants']:>8,}")
        print(f"  attribute values     {stats['attr_values_kept']:>8,} kept, "
              f"{stats['dropped_too_common']} dropped as too common, "
              f"{stats['dropped_unique']:,} as unique")
        print(f"  edges                {stats['edges']:>8,}")
        print(f"  candidate rings      {stats['rings']:>8,}   "
              f"{stats.get('rings_from_co_timing', 0)} found by co-timing alone, "
              f"{stats['unresolved_dense']} dense blobs unresolved")
    return verdicts, exposure, stats, merchants, tbm, portfolio


def main():
    if not (DATA / "merchants.csv").exists():
        print("  No data yet. Build it first:")
        print()
        print("      python gen_data.py")
        print()
        return 1
    verdicts, exposure, stats, merchants, _tbm, _pf = analyse()
    entries, held = respond(verdicts, exposure)
    shown = write_report(verdicts, entries, merchants, exposure, stats=stats)

    from collections import Counter
    tally = Counter(e["action"] for e in entries)
    print(f"\n  actions              " + "  ".join(
        f"{k}={v}" for k, v in tally.most_common()))
    print(f"  value delayed        {rupees(held):>16}   "
          f"reversible, auto-expires in 24h")
    print(f"  audit trail          data/audit.jsonl  ({len(entries)} records)")
    print(f"  case files           report.html       ({shown} worth a review)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
