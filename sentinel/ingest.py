"""CSV to integer-paise records, grouped by merchant.

Money is integer paise everywhere. Rupees exist only at the CSV and HTML
boundaries. test_pipeline.py asserts conservation with NO tolerance - if that
ever needs an epsilon, a float got into the ledger.

Discipline carried over from D:/razorpay/fincontroller/ledger.py: labels live
in a sidecar file that nothing in this package may open, or even name. The
evaluation harness owns it; test_pipeline.py greps this whole package to prove
no module here so much as mentions it.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True, slots=True)
class Txn:
    txn_id: str
    merchant_id: str
    day: int          # days since the window start; the graph never needs a calendar
    amount: int       # paise
    payer_card: str
    is_refund: bool
    is_chargeback: bool


def rupees(paise: int) -> str:
    """Indian digit grouping: 12,34,567.89 rather than 1,234,567.89."""
    neg, paise = paise < 0, abs(paise)
    whole, frac = divmod(paise, 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{'-' if neg else ''}Rs {s}.{frac:02d}"


def load_txns(path: Path | None = None) -> list[Txn]:
    with open(path or DATA / "transactions.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    origin = min(date.fromisoformat(r["date"]) for r in rows)
    return [
        Txn(
            r["txn_id"],
            r["merchant_id"],
            (date.fromisoformat(r["date"]) - origin).days,
            int(r["amount_paise"]),
            r["payer_card"],
            r["is_refund"] == "1",
            r["is_chargeback"] == "1",
        )
        for r in rows
    ]


def by_merchant(txns: list[Txn]) -> dict[str, list[Txn]]:
    out: dict[str, list[Txn]] = defaultdict(list)
    for t in txns:
        out[t.merchant_id].append(t)
    return out


@dataclass
class Portfolio:
    """Population-level baselines.

    Every rate a signal reports is compared against these, never against an
    absolute constant. A burst that the whole portfolio shares is a festival,
    not a conspiracy - and getting that wrong is the single largest source of
    false positives in Indian payments.
    """

    txns_by_day: dict[int, int]
    refund_rate: float
    chargeback_rate: float
    n_txns: int
    n_days: int

    @classmethod
    def build(cls, txns: list[Txn]) -> "Portfolio":
        per_day: dict[int, int] = defaultdict(int)
        refunds = chargebacks = 0
        for t in txns:
            per_day[t.day] += 1
            refunds += t.is_refund
            chargebacks += t.is_chargeback
        n = max(len(txns), 1)
        return cls(dict(per_day), refunds / n, chargebacks / n, len(txns),
                   max(per_day, default=0) + 1)

    def day_share(self, day: int) -> float:
        """Fraction of all portfolio activity that fell on this day."""
        return self.txns_by_day.get(day, 0) / max(self.n_txns, 1)
