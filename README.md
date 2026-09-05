# Ring Sentinel

**Razorpay AI Buildathon — AI Risk Manager**

A fraud ring is a group of merchant accounts run by one operator. Every payment
looks normal. Every account looks normal. Only the *group* looks wrong.

Ring Sentinel finds the group.

## The problem

One operator. Nine merchant accounts. Six months. ₹25.9 lakh moved through them,
and 53 of their 129 payments are refunds — money in, money straight back out.

A payment-by-payment model sees nine ordinary businesses making ordinary ₹33,000
sales. It clears every one of them, and it is right every time. The fraud is not
inside a payment. It is in what links the accounts:

- three settle into the **same bank account**
- three sign in from the **same device**
- all nine **opened within 69 days** — accounts picked at random here spread over 1,188
- refunds at **ten times** the rest of the portfolio

Each of those has an innocent explanation on its own. All four, on the same nine
accounts, does not.

## Watch it work

https://github.com/user-attachments/assets/6be1bb91-cd8e-4235-81f2-d09ac16d2856

Four minutes. Every figure spoken in it is read out of this repository at build
time, so the video and the code cannot disagree.

## What it does

Links accounts that share something, then scores the **group** — not the payment —
on identity, money movement, connection shape, account age and timing.

A second pass then argues the score *down*: is this just a franchise chain? a
marketplace? a family business? It can only subtract, never add.

What comes out is a case file in plain English rather than a number, so a
merchant can read it and push back.

## Results

Thresholds were chosen on one half of the data. This is the other half, read once.

| | |
|---|---|
| Rings caught | **20 of 22** (90.9%) |
| Flagged groups that really were rings | **20 of 23** (87.0%) |
| Innocent lookalike groups flagged | **0 of 10** |
| Innocent merchants inside a flagged group | 12 of 147 |

## The hard part

Not finding rings. *Not* flagging honest businesses that look identical to one.

So the data contains twenty planted decoys: a franchise chain settling into one
head-office account, a family running three shops off one laptop, a marketplace
whose sellers all sit behind its IP, merchants all busy at once for Diwali, and a
brand opening six outlets in a single week — which looks exactly like a batch of
mules being onboarded.

None of the twenty were flagged.

## What it can do to a merchant

`OBSERVE` → `WATCH` → `REVIEW` → `HOLD_SETTLEMENT`, which moves settlement from
T+2 to T+3 and expires by itself after 24 hours.

There is no freeze, no block and no account closure. Not disabled behind a flag —
the functions do not exist, and a test asserts they never appear. The worst this
can do to an honest merchant is make them wait a day.

## What it gets wrong

- One ring scores 67 against a threshold of 70, and is missed. Lowering the line
  to catch it would flag two real family businesses sitting at 67 and 63.
- On Elliptic2 — 121,810 labelled Bitcoin subgraphs — it does not beat the
  published state of the art. It lands at the structure-only baseline.
- 22 rings is a small sample. That 90.9% carries a 95% interval of 72–98%.

## Run it

```bash
pip install -r requirements.txt
python gen_data.py && python run.py && python score.py && python test_pipeline.py
```

numpy, scipy, scikit-learn. No torch, no GNN framework, no LLM, no API key,
nothing to train. About 20 seconds. `run.py` writes `report.html` — the case
files are the product.

Against the real Razorpay test-mode API:

```bash
export RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
export RAZORPAY_KEY_SECRET=xxxxxxxx
python seed_testmode.py && python live.py --annotate
```

---

**[ENGINEERING.md](ENGINEERING.md)** — how the graph is built, what each signal
is worth, the benchmark it loses, and the bugs found along the way.
