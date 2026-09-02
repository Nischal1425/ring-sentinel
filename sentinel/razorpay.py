"""Razorpay test-mode client that CANNOT move money.

The defense-only claim is not a policy sentence in a document. It is the
capability surface of this class. NotesOnlyClient has exactly six methods:
four reads (payments, one payment, orders, customers) and two note patches.

There is no capture. No refund. No transfer. No payout. No settlement API. Not
disabled, not gated behind a flag - absent. You cannot call what does not
exist, and test_pipeline.py asserts the class surface stays that way.

Note that this class also cannot CREATE anything. Seeding a test account with
orders lives in seed_testmode.py, outside this package, precisely so the
detector keeps no ability to originate an object of any kind.

Notes are the right channel for this and not a workaround: they are metadata,
they are reversible by overwriting, they are visible to the merchant in the
dashboard, and Razorpay caps them at 15 keys of 256 characters, which is a
hard bound on how much this system can assert about anybody.

Credentials come from the environment (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)
and must be test-mode: a key that does not start with rzp_test_ is refused
outright. With no credentials the pipeline says so and continues offline. It
never silently degrades into pretending.

    stdlib urllib, no requests dependency.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.razorpay.com/v1"
MAX_NOTE_KEYS = 15
MAX_NOTE_CHARS = 256


def credentials() -> tuple[str, str] | None:
    """Test-mode credentials from the environment, or None."""
    key = os.environ.get("RAZORPAY_KEY_ID", "")
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key or not secret:
        return None
    if not key.startswith("rzp_test_"):
        raise RuntimeError(
            f"refusing a non-test key ({key[:12]}...). This tool is only ever "
            f"pointed at test mode, where no real payment can occur.")
    return key, secret


def available() -> bool:
    try:
        return credentials() is not None
    except RuntimeError:
        return False


class NotesOnlyClient:
    """Read. Annotate. That is the entire surface."""

    def __init__(self, creds=None, *, timeout=20):
        creds = creds or credentials()
        if creds is None:
            raise RuntimeError("no test-mode credentials in the environment")
        token = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
        self._auth = f"Basic {token}"
        self._timeout = timeout
        self.calls = 0

    # -- transport ---------------------------------------------------------
    def _request(self, method, path, payload=None, *, attempts=4):
        """One HTTP call, with backoff on 429. Razorpay rate-limits per account."""
        url = f"{API}{path}"
        body = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Authorization", self._auth)
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    self.calls += 1
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < attempts - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

    # -- the whole API -----------------------------------------------------
    def fetch_payments(self, count=100, skip=0):
        """GET /payments - read only."""
        q = urllib.parse.urlencode({"count": min(count, 100), "skip": skip})
        return self._request("GET", f"/payments?{q}").get("items", [])

    def fetch_orders(self, count=100, skip=0):
        """GET /orders - read only.

        Orders rather than payments because test mode cannot mint payment
        objects headlessly: server-to-server payment creation returns 401
        unless Razorpay enables S2S on the account, and everything else needs
        a browser checkout. Orders are real API objects, created over the real
        network, and they carry the `notes` we need. Measured, not assumed -
        see LIVE.md for the probe results.
        """
        q = urllib.parse.urlencode({"count": min(count, 100), "skip": skip})
        return self._request("GET", f"/orders?{q}").get("items", [])

    def fetch_customers(self, count=100, skip=0):
        """GET /customers - read only."""
        q = urllib.parse.urlencode({"count": min(count, 100), "skip": skip})
        return self._request("GET", f"/customers?{q}").get("items", [])

    def fetch_payment(self, payment_id):
        """GET /payments/:id - read only."""
        return self._request("GET", f"/payments/{payment_id}")

    def annotate_order(self, order_id, notes: dict, *, existing=None):
        """PATCH /orders/:id - MERGE a verdict into an order's `notes`.

        Razorpay's PATCH replaces the whole notes object. Sending only our keys
        therefore DELETES whatever the merchant put there. We shipped that bug
        and it destroyed the very attributes the detector reads - found by
        running live.py twice and watching 18 buyers collapse to 8.

        A risk system that damages a merchant's own data while labelling them
        is precisely the failure this project exists to avoid, so the merge is
        not a convenience: when the 15-key cap forces a choice, OUR keys are
        dropped and theirs are kept.
        """
        current = existing if existing is not None else             self._request("GET", f"/orders/{order_id}").get("notes") or {}
        theirs = {k: v for k, v in current.items() if not k.startswith("sentinel_")}
        room = MAX_NOTE_KEYS - len(theirs)
        if room <= 0:
            return {"skipped": "no room in notes without discarding merchant data"}
        ours = dict(list(self._clean(notes).items())[:room])
        return self._request("PATCH", f"/orders/{order_id}",
                             {"notes": {**theirs, **ours}})

    def annotate(self, payment_id, notes: dict):
        """PATCH /payments/:id - write a verdict into `notes`. Nothing else.

        The only writes this system can perform are these two note patches. It
        cannot capture, refund, transfer or hold funds, because those methods
        are not on this class - absent, not disabled.
        """
        return self._request("PATCH", f"/payments/{payment_id}",
                             {"notes": self._clean(notes)})

    @staticmethod
    def _clean(notes: dict) -> dict:
        """Razorpay caps notes at 15 keys x 256 chars. That cap is a feature:
        it is a hard bound on how much this system can assert about anybody."""
        if len(notes) > MAX_NOTE_KEYS:
            raise ValueError(f"Razorpay allows {MAX_NOTE_KEYS} note keys, got {len(notes)}")
        clean = {}
        for k, v in notes.items():
            text = str(v)
            if len(text) > MAX_NOTE_CHARS:
                text = text[:MAX_NOTE_CHARS - 1] + "..."
            clean[str(k)[:MAX_NOTE_CHARS]] = text
        return clean


def verdict_notes(verdict, entry) -> dict:
    """The verdict, compressed into what `notes` can legally carry.

    Deliberately includes the reversal handle and the expiry: an annotation a
    merchant cannot see the end of is the thing this project exists to avoid.
    """
    notes = {
        "sentinel_ring": verdict.ring.ring_id,
        "sentinel_score": str(verdict.score),
        "sentinel_action": entry["action"],
        "sentinel_members": str(len(verdict.ring)),
        "sentinel_reason": (verdict.reasons or ["no single decisive signal"])[0],
    }
    if entry.get("expires_at"):
        notes["sentinel_expires"] = entry["expires_at"]
    if entry.get("reversal"):
        notes["sentinel_reversal"] = entry["reversal"]
    return notes
