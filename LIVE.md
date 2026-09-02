# The live Razorpay leg

Everything below was measured against a real test-mode account on 2026-09-02,
not read from documentation.

## What test mode actually allows

| Endpoint | Result |
|---|---|
| `GET /v1/payments` | 200 |
| `GET /v1/orders` | 200 |
| `GET /v1/customers` | 200 |
| `GET /v1/settlements` | 200 |
| `GET /v1/disputes` | 200 |
| `POST /v1/orders` | 200 |
| `POST /v1/customers` | 200 |
| `POST /v1/payment_links` | 200 |
| `PATCH /v1/orders/:id` (notes) | 200 |
| `POST /v1/payments` (S2S) | **401** |
| `POST /v1/payments/create/ajax` | **401** |
| `POST /v1/payments/create/upi` | **404** |

**Payment objects cannot be minted headlessly.** Server-to-server payment
creation needs Razorpay to enable S2S on the account; every other route needs a
browser checkout. So the live leg runs on **orders**, which are real objects
created over the real network and which carry `notes`.

Razorpay also de-duplicates customers on contact and email, so two distinct
customers cannot share a phone number - which is precisely the linkage a ring
exhibits. The buyer's attributes therefore travel in the order's `notes`. In
production these arrive on the payment object (card fingerprint, device, VPA,
email). This is a real constraint of test mode, stated rather than hidden.

## Running it

```bash
export RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
export RAZORPAY_KEY_SECRET=xxxxxxxx

python seed_testmode.py     # 18 buyers, 25 orders: 2 rings, 1 decoy, 8 ordinary
python live.py              # fetch from the API, detect, decide
python live.py --annotate   # also write the verdict back via PATCH /orders/:id
```

`seed_testmode.py` lives outside `sentinel/` on purpose: the detector package
holds no ability to create a Razorpay object of any kind.

## Measured result

Re-run on 2026-09-02 against the current code. The account has accumulated
orders across several seedings, so the counts differ from a fresh run:

```
GET /v1/orders    49 orders fetched live
entities          17 buyers, 31 orders
graph             10 links, 2 candidate rings

R0000  score 92   4 buyers  structuring ring       -> review
R0001  score 89   3 buyers  lookalike-name ring    -> review
                            everyone else          -> untouched
```

Neither escalates past `review` on a cold start: both are stopped by the
two-attribute-types gate and by hysteresis, which needs a ring to persist across
two runs before a settlement moves.

The live output also carries the marketplace-versus-recruiter reasoning added
later:

> One account sits at the centre of 100% of the links, and what it shares with
> the others is identity, not infrastructure. A marketplace shares an IP with
> its sellers; it does not put them on its own settlement account or device.

Numbers in this file are a snapshot and go stale the moment anything is
re-seeded. `python live.py` is the source of truth.

## What the live run found that the synthetic book hid

The structuring ring first scored **21 and was missed**. `money.detect` required
12 transactions before reporting anything, and the live ring has 8 - so the
signal abstained and the verifier correctly wrote the group off as "related but
unremarkable".

Every synthetic ring has hundreds of payments, so the offline scorecard could
never surface this. The fix splits the floor in two: a **rate ratio** compares
against a portfolio base rate and still needs 12 rows, but a **share** describes
the ring's own payments and is readable at 6. The ring now scores 92. Offline
numbers were unchanged by the fix at the time. The current held-out figures
are whatever score.py prints; they have moved since, and this file no longer
restates them - duplicated numbers are how a README goes stale.

## What this does NOT show

A test account is one merchant, so these are rings of **buyers**, not rings of
colluding **merchants**. The merchant-collusion claim is measured on the
synthetic book in `gen_data.py`, whose labels are ours. Eighteen buyers is a
demonstration, not an evaluation - the numbers that count come from `score.py`
on the held-out half.

## The second bug the live run found: we were destroying merchant data

`PATCH /v1/orders/:id` **replaces** the whole `notes` object. Our first
responder sent only its own keys, which deleted whatever the merchant had put
there - including `buyer_id` and `device_fp`, the very attributes the detector
reads.

It was invisible for one run and obvious on the second: 18 buyers collapsed to
8, because every order we had annotated had lost the field that identified its
buyer. A risk system that damages a merchant's own records while labelling them
is exactly the failure this project exists to avoid.

`annotate_order` now merges. When Razorpay's 15-key cap forces a choice, **our
keys are dropped and the merchant's are kept** - never the other way round.
`test_annotate_merges` checks both halves offline.

Current state of the account, read back live:

```
total orders                      49
  verdict + merchant data intact  15   <- after the merge fix
  merchant data destroyed         18   <- before the fix, left as evidence
  untouched (not flagged)         16
```

## A note on read-after-write

Razorpay's list endpoints lag. Twice we annotated orders, immediately re-read
them, and saw stale notes; both times the write had landed and showed up
seconds later. Anything built on this API should not treat a list read as
confirmation of a write it just made.
