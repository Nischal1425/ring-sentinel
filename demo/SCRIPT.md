# Ring Sentinel — 5 minute pitch, word for word

Read this aloud. It is timed at a calm 140 words per minute, which is slower
than you think you are speaking. Total 4:45, leaving 15 seconds of slack.

**Record each of the seven beats separately.** Never attempt one take. If a beat
goes wrong you re-record 40 seconds, not five minutes.

Run `python demo/warm.py` once before you start recording. It regenerates the
data, warms every import, and prints the exact command for each beat.

---

## BEAT 1 — 0:00 to 0:35
**Screen:** `report.html`, scrolled to ring R0003.

> This is nine merchant accounts and six hundred and eighteen payments.
>
> Every payment is an ordinary sale. Six thousand rupees, five thousand rupees.
> Razorpay already has a model that scores payments one at a time, and it would
> clear every single one of these. Correctly.
>
> Because a fraud ring is invisible one payment at a time. It only exists in
> what connects the accounts.

*Pause. Let the case file sit on screen for two seconds.*

---

## BEAT 2 — 0:35 to 1:05
**Screen:** `demo/ring.html`. Press space between each line.

> Six merchants. Each one, on its own, looks fine. *(space)*
>
> They share one device fingerprint. *(space)*
>
> And one IP prefix. *(space)*
>
> And every account was opened in the same week — when chance predicts a
> spread of eleven hundred days. *(space)*
>
> None of that is visible in any single payment. All of it is visible in the
> group.

---

## BEAT 3 — 1:05 to 2:05
**Screen:** terminal, `python score.py`. Scroll to PER RING CLASS.

> These are the results on the held-out half. Ninety point nine percent recall,
> twenty rings of twenty-two. Eighty-seven percent precision.
>
> But read the per-class table, not the headline.
>
> Nine classes of ring, all caught. And this one — recruiter_quiet — four out
> of five.
>
> That fifth one scores sixty-seven. My threshold is seventy. I could catch it
> by lowering the line, but there are two legitimate family businesses sitting
> at sixty-three and sixty-seven. Lowering the line to catch one criminal would
> flag both of them.
>
> So it stays missed, and it is written down in the README with the reason.

---

## BEAT 4 — 2:05 to 2:50
**Screen:** terminal, scroll to the DECOYS section of the same output.

> I did not only plant criminals in the test data. I planted innocents,
> specifically to fool myself.
>
> A franchise chain sharing one settlement account across five branches. A
> marketplace with a hub and sellers. A family business on one shared device.
> Merchants all busy together for Diwali. And a brand opening six outlets in
> the same week, which on the account-age signal looks exactly like a batch of
> mules.
>
> Twenty legitimate groups. Zero flagged.
>
> If a fraud detector cannot tell a Domino's franchise from a mule network, it
> is not ready to touch anyone's money.

---

## BEAT 5 — 2:50 to 3:35
**Screen:** terminal, `python live.py --annotate`. Then switch to your Razorpay
test dashboard and open a tagged order.

> This is not a mock. These are real orders in a real Razorpay test-mode
> account, fetched over the network.
>
> It finds two rings, scores them, and writes its verdict back onto the real
> order through PATCH slash orders.
>
> And that is the only write it can perform. There is no capture method, no
> refund, no transfer, no payout on that client. Not disabled — absent.

---

## BEAT 6 — 3:35 to 4:20
**Screen:** terminal, `python bench_ml.py`, then `python bench_elliptic2.py`.

> Two things I want to show you that did not work.
>
> First: I tested whether machine learning beats my rules. It loses. And look
> at the tree it learned — the top split says densely connected accounts are
> innocent. That is backwards. It is explainable in the sense that you can
> print it, and what it explains is false.
>
> Second: I tested against Elliptic2, a hundred and twenty-one thousand real
> labelled Bitcoin subgraphs. I do not beat the published state of the art. I
> land at the structure-only baseline.
>
> Both of those are in the repo, because a benchmark you only publish when you
> win is not a benchmark.

---

## BEAT 7 — 4:20 to 4:45
**Screen:** terminal, `python test_pipeline.py`, then open `sentinel/types.py`
at the Action enum.

> Seventeen checks. One per claim I have just made.
>
> One of them scrambles every identifier and proves the scores do not change.
> One wraps the file system and proves the detector never opens the answer key.
>
> And this is the action ladder. Observe. Watch. Review. Hold — a reversible
> twenty-four hour settlement delay that expires on its own.
>
> There is no freeze in this codebase. I cannot destroy a merchant's business
> by accident, because the function does not exist.

*End on the Action enum on screen. Do not add an outro.*

---

## Notes for recording

- **No face, no slides, no music.** Terminal and browser only.
- **Terminal font 18pt minimum.** Judges may watch on a laptop.
- **Say the number, then show it.** "Ninety point nine percent" while it is on screen.
- **Say "held out" twice.** It is the track's literal judging criterion.
- If you stumble, stop and re-record that beat. Do not push through.

## If you narrate with TTS instead of your own voice

Your own voice is better here, accent and all — this is an engineering pitch,
not an advert, and a real voice signals a real person did the work.

If you would rather not record voice, clone your own voice with OmniVoice
rather than using a stock preset. A stock TTS voice on a fraud pitch reads as
low effort. Your own cloned voice does not.

Do **not** put AI-generated b-roll over this script. Every claim above is
verified by something on screen. Decorative footage over verified claims makes
them look decorative too.
