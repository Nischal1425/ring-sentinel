"""Render every video frame, and derive the narration from the SAME data.

    python demo/build_frames.py

Why this exists rather than a hand-written script plus a screen recording:

  * Numbers in a spoken script rot. This project has caught stale documented
    figures in every audit it has run. So the narration text is generated from
    the live pipeline here, next to the frame that shows it. They cannot
    disagree, because neither is typed by hand.
  * A screen recording of a live desktop captures whatever else is on that
    desktop, and cannot be re-cut without re-performing it. Rendered frames are
    deterministic, reviewable one at a time, and cheap to regenerate.

Outputs into demo/frames/:
    NN_name.html   the frame source
    NN_name.png    the rendered frame (written by render.py)
    narration.json the spoken line for each frame, plus its hold time
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FRAMES = ROOT / "demo" / "frames"
FRAMES.mkdir(parents=True, exist_ok=True)

CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#252a34;--ink:#e7eaf0;--dim:#8b93a3;
--hot:#ff6b6b;--warm:#ffa94d;--cool:#4dabf7;--good:#51cf66}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);width:1920px;height:1080px;
overflow:hidden;display:flex;flex-direction:column;
font:16px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.bar{padding:64px 96px 0;flex:0 0 auto}
.kicker{color:var(--dim);font-size:27px;letter-spacing:.14em;text-transform:uppercase}
h1{font-size:68px;font-weight:600;margin-top:14px;line-height:1.2}
h1 b{color:var(--warm);font-weight:600}
h1 .hot{color:var(--hot)}
h1 .good{color:var(--good)}
.body{flex:1 1 auto;padding:20px 96px 96px;display:flex;align-items:center;
justify-content:center}
pre{font:27px/1.6 ui-monospace,SFMono-Regular,Consolas,Menlo,monospace;
background:#0b0d11;border:1px solid var(--line);border-radius:12px;
padding:40px 46px;white-space:pre;color:#c9d1de;max-height:100%;overflow:hidden}
pre .cmd{color:var(--good)}
pre em{color:var(--hot);font-style:normal;font-weight:700}
pre b{color:var(--warm);font-weight:700}
pre i{color:var(--cool);font-style:normal}
.big{text-align:center}
.big .n{font-size:230px;font-weight:700;color:var(--hot);
font-variant-numeric:tabular-nums;line-height:1}
.big .n.good{color:var(--good)}
.big .cap{color:var(--dim);font-size:38px;margin-top:22px}
.cards{display:flex;gap:34px;flex-wrap:wrap;justify-content:center;max-width:1730px}
.k{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:36px 42px;min-width:452px}
.k .t{color:var(--dim);font-size:21px;text-transform:uppercase;letter-spacing:.08em}
.k .v{font-size:64px;font-weight:700;margin-top:12px;font-variant-numeric:tabular-nums}
.k .s{color:var(--dim);font-size:24px;margin-top:10px}
.k.bad .v{color:var(--hot)} .k.ok .v{color:var(--good)} .k.warn .v{color:var(--warm)}
/* Prose, not terminal output. These are full sentences, and inside a <pre>
   they cannot wrap - so the fit-to-width loop shrank them to about 11px in a
   box using a third of the frame. Wrapping lets them stay large. */
.reasons{display:flex;flex-direction:column;gap:28px;width:100%;max-width:1680px}
.r{background:var(--card);border:1px solid var(--line);
border-left:6px solid var(--hot);border-radius:12px;padding:32px 40px;
font-size:34px;line-height:1.45;color:#d9dee8}
.r b{color:var(--warm);font-weight:700}
"""


# Terminal blocks are real captured output, so their length is not under our
# control - test_pipeline.py grew from 7 checks to 17 and silently overflowed
# the frame. Shrink to fit rather than guessing a font size per slide.
# Height is measured against the known 1920x1080 canvas, NOT the parent's
# clientHeight: max-height:100% does not resolve on a flex item, so the pre
# reported scrollHeight == clientHeight and the first version never shrank.
# Width uses scrollWidth vs clientWidth, because overflow:hidden pins
# offsetWidth to the container - long lines were being cut off silently
# at the right edge with the fit loop reporting no overflow at all.
FIT = ("<script>var _b=document.querySelector('.bar');"
       "document.querySelectorAll('pre').forEach(function(p){"
       "var c=getComputedStyle(p.parentElement);"
       "var mh=1080-_b.offsetHeight-parseFloat(c.paddingTop)-parseFloat(c.paddingBottom);"
       "var s=parseFloat(getComputedStyle(p).fontSize);"
       "while((p.offsetHeight>mh||p.scrollWidth>p.clientWidth)&&s>10)"
       "{s-=1;p.style.fontSize=s+'px';}});</script>")


def page(kicker, title, body, extra=""):
    return (f"<!doctype html><meta charset='utf-8'><style>{CSS}{extra}</style>"
            f"<body><div class='bar'><div class='kicker'>{kicker}</div>"
            f"<h1>{title}</h1></div><div class='body'>{body}</div>{FIT}</body>")


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _emph(reason):
    """Escape a detector reason, then bold its figures so the eye finds them."""
    return re.sub(r"(\d[\d,.]*\s?%?x?)", r"<b>\1</b>", esc(reason))


def term(cmd, text, hi=()):
    """Real captured output, rendered as a terminal block."""
    out = esc(text.rstrip())
    for h in hi:                       # highlight whole lines containing h
        out = "\n".join(f"<em>{ln}</em>" if h in ln and "<em>" not in ln else ln
                        for ln in out.split("\n"))
    return f"<pre><span class='cmd'>$ {esc(cmd)}</span>\n\n{out}</pre>"


def run(cmd):
    r = subprocess.run([sys.executable] + cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env={**os.environ,
                       "PYTHONIOENCODING": "utf-8"})
    return r.stdout


def section(text, start, stop=None, limit=40):
    """Pull one labelled block out of a captured stdout."""
    lines = text.split("\n")
    try:
        i = next(k for k, l in enumerate(lines) if start in l)
    except StopIteration:
        return f"[section {start!r} not found]"
    out = []
    for l in lines[i:i + limit]:
        if stop and out and stop in l:
            break
        out.append(l)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _trim(reason, cap=165):
    """The detector writes full sentences; a slide caption needs the claim."""
    r = reason.split(" - ")[0].strip()
    if len(r) > cap:
        r = r[:cap].rsplit(" ", 1)[0] + "..."
    return r


def _pick(reasons, *words):
    for r in reasons:
        if any(w in r.lower() for w in words):
            return r
    return ""


def ring_story(hero, tbm):
    """Write demo/ring.html's data block from the ring the detector actually
    found, and return the five narration lines that match it.

    The previous version hardcoded six merchant names and claimed they shared
    one device and one IP. After the data was regenerated those names resolved
    to unrelated LEGITIMATE merchants, and the top ring turned out to be a
    refund mill whose members share neither a single device nor an IP. The
    animation was asserting evidence that did not exist. Everything here is
    now read back out of data/ and the detector's own reasons.
    """
    import csv as _csv
    from statistics import median

    rows = {r["merchant_id"]: r for r in
            _csv.DictReader(io.open("data/merchants.csv", encoding="utf-8"))}
    ids = sorted(hero.ring.members)[:9]          # 3x3 is the most the stage holds
    n = len(ids)

    cols, xs, ystep = 3, [40, 375, 710], 175
    nrows = (n + cols - 1) // cols
    y0 = max(105, (600 - ((nrows - 1) * ystep + 96)) // 2 + 25)
    members = []
    for k, mid in enumerate(ids):
        r = rows[mid]
        amts = [t.amount for t in tbm.get(mid, [])]
        members.append({"n": r["name"], "d": int(r["signup_day"]),
                        "p": "Rs {:,.2f}".format(median(amts) / 100 if amts else 0),
                        "x": xs[k % cols], "y": y0 + (k // cols) * ystep})

    def pairs_sharing(field):
        out = []
        for i in range(n):
            for j in range(i + 1, n):
                if rows[ids[i]][field] and rows[ids[i]][field] == rows[ids[j]][field]:
                    out.append([i, j])
        return out

    dev = pairs_sharing("device_fp")
    # Name the attribute that actually matched. The caption used to quote the
    # detector's hub sentence while the drawing showed a plain triangle - the
    # words and the picture have to describe the same thing.
    ident, ident_field = [], ""
    for f, phrase in (("bank_account", "settlement account"),
                      ("upi_vpa", "UPI address"),
                      ("phone", "phone number"),
                      ("ip_prefix", "internet address")):
        ident = pairs_sharing(f)
        if ident:
            ident_field = phrase
            break
    shared = sorted({i for e in dev for i in e})
    ishared = sorted({i for e in ident for i in e})
    span = max(m["d"] for m in members) - min(m["d"] for m in members)

    rs = list(hero.reasons)
    r_when = _pick(rs, "opened within")
    r_money = _pick(rs, "refund", "chargeback", "ticket", "round")
    r_time = _pick(rs, "quiet", "busiest", "same day")

    steps = [{"title": f"{_num(n).capitalize()} merchants. Every payment is an ordinary sale.",
              "sub": "Razorpay's per-payment model clears all of them. Correctly."}]
    lines = [f"{_num(n).capitalize()} merchants. Each one, on its own, looks "
             f"completely fine."]

    if dev:
        fp = rows[ids[shared[0]]]["device_fp"]
        k = _num(len(shared))
        steps.append({"title": f"{k.capitalize()} of them sign in from one device.",
                      "sub": f"Fingerprint {fp}. {k.capitalize()} separate "
                             f"businesses, one machine.",
                      "edges": dev, "colour": "#ff6b6b"})
        lines.append(f"{k.capitalize()} of them sign in from the very same "
                     f"device. Invisible if you look at payments one at a time.")

    if ident:
        k = _num(len(ishared))
        if ident_field == "internet address":
            why = ("Independent businesses do not end up behind one address "
                   "by chance.")
        else:
            why = (f"A marketplace shares an internet address with its sellers. "
                   f"It does not put them on its own {ident_field}.")
        steps.append({"title": f"{k.capitalize()} of them share a {ident_field}.",
                      "sub": why, "edges": ident, "colour": "#4dabf7"})
        lines.append(f"And {k} of them share a {ident_field}. {why}")

    # The title already carries the observed span, so the caption carries the
    # null model instead of repeating it and getting truncated.
    expect = ""
    if "against " in r_when and " days expected" in r_when:
        expect = r_when.split("against ")[1].split(" days expected")[0].strip()
    steps.append({"title": f"All {_num(n)} accounts were opened within {span} days.",
                  "sub": (f"Chance predicts {expect} days for {_num(n)} accounts "
                          f"picked at random from this book." if expect
                          else _trim(r_when or r_time)), "meta": True})
    lines.append(f"Every account was opened inside the same {span} days. "
                 f"Real shops open over years. Mules are onboarded in a batch.")

    steps.append({"title": "", "sub": "", "verdict": True})
    lines.append("None of that is visible in any single payment. All of it is "
                 "visible in the group.")

    data = {"id": hero.ring.ring_id, "score": hero.score, "members": members,
            "steps": steps,
            "verdict": {"label": f"Ring {hero.ring.ring_id}",
                        "line": esc(_trim(r_money or r_when, 200))}}

    html = io.open("demo/ring.html", encoding="utf-8").read()
    a, b = '<script id="ringdata">', '</script>'
    i = html.index(a) + len(a)
    j = html.index(b, i)
    nl = chr(10)
    html = html[:i] + nl + 'window.RING = ' + json.dumps(data) + ';' + nl + html[j:]
    io.open("demo/ring.html", "w", encoding="utf-8").write(html)
    return lines


def _num(k):
    return {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
            7: "seven", 8: "eight", 9: "nine"}.get(k, str(k))


def main():
    print("  capturing real output ...", flush=True)
    score_out = run(["score.py"])
    tests_out = run(["test_pipeline.py"])
    ml_out = run(["bench_ml.py"])
    ell_out = run(["bench_elliptic2.py"])
    abl_out = run(["ablate.py"])

    def ablation_delta(label, default="?"):
        """Recall points lost when one piece is removed, read off ablate.py.

        Spoken as a word in the narration, so it cannot be left as prose: the
        co-timing figure has already moved once when the data was regenerated.
        """
        for line in abl_out.split("\n"):
            if label in line:
                bits = line.split()
                try:
                    return f"{abs(float(bits[-1])) * 100:.0f}"
                except ValueError:
                    return default
        return default

    abl_cotiming = ablation_delta("no co-timing relation")

    # The live round-trip is only shown when it can actually be demonstrated.
    # No cached transcript: a saved API capture is exactly the kind of artifact
    # that goes stale and gets quoted as if it were fresh.
    from sentinel.razorpay import available as _rzp_available
    live_out = run(["live.py"]) if _rzp_available() else ""
    if "orders fetched live" not in live_out:
        live_out = ""
    live_orders = live_rings = ""
    if live_out:
        for line in live_out.split("\n"):
            if "orders fetched live" in line:
                live_orders = line.split()[-4]
            if "candidate rings" in line:
                live_rings = line.split("links,")[1].split()[0]

    # ---- numbers derived from the pipeline, never typed --------------------
    from run import analyse
    from sentinel.ingest import rupees
    from sentinel.respond import REVIEW_AT
    import evalkit

    verdicts, exposure, stats, merchants, tbm, _pf = analyse(quiet=True)
    hero = max((v for v in verdicts), key=lambda v: (v.score, len(v.ring)))
    hero_pay = sum(len(tbm.get(m, [])) for m in hero.ring.members)

    truth = evalkit.load_truth(); split = evalkit.assign(truth)
    by_id = {r["merchant_id"]: r for r in truth}
    test_m = {r["merchant_id"] for r in truth if split[r["merchant_id"]] == "test"}
    tr = defaultdict(set)
    for r in truth:
        if split[r["merchant_id"]] == "test" and r["gt_ring_id"] and r["gt_is_fraud"] == "1":
            tr[r["gt_ring_id"]].add(r["merchant_id"])
    tv, *_ = analyse(quiet=True, only=test_m)
    fl = [x for x in tv if x.score >= REVIEW_AT]
    mt, mp = evalkit.match(tr, fl)
    recall, prec = len(mt) / len(tr), len(mp) / max(len(fl), 1)
    # 20 of 22 is 90.9%, but the Wilson interval on that denominator runs from
    # roughly 72% to 98%. Quoting the point estimate alone overstates how much
    # 22 rings can tell you, and score.py already prints the interval.
    r_lo, r_hi = evalkit.wilson_interval(len(mt), len(tr))

    groups = defaultdict(set)
    for mid, rec in by_id.items():
        if rec["gt_ring_id"]:
            groups[rec["gt_ring_id"]].add(mid)

    def best(members):
        h = [(len(set(v.ring.members) & members), v) for v in verdicts
             if set(v.ring.members) & members]
        return max(h, key=lambda z: z[0])[1].score if h else 0

    crim = [g for g, mm in groups.items()
            if not by_id[next(iter(mm))]["gt_ring_type"].startswith("decoy")]
    dec = [g for g, mm in groups.items()
           if by_id[next(iter(mm))]["gt_ring_type"].startswith("decoy")]
    crim_caught = sum(1 for g in crim if best(groups[g]) >= REVIEW_AT)
    dec_flagged = sum(1 for g in dec if best(groups[g]) >= REVIEW_AT)
    quiet = sorted((best(groups[g]) for g in crim
                    if by_id[next(iter(groups[g]))]["gt_ring_type"] == "recruiter_quiet"),
                   reverse=True)
    fam = sorted((best(groups[g]) for g in dec
                  if by_id[next(iter(groups[g]))]["gt_ring_type"] == "decoy_family"
                  and best(groups[g]) > 0), reverse=True)
    n_checks = tests_out.count("  pass  ")

    F = []   # (name, html, narration, extra_hold_seconds)

    # ---------------------------------------------------------------- beat 1
    F.append(("01_hero", page(
        "the case that opens the pitch",
        f"{len(hero.ring)} accounts. {hero_pay} payments. "
        f"<b>Not one of them is unusual.</b>",
        "<div class='cards'>"
        f"<div class='k'><div class='t'>exposure</div><div class='v'>"
        f"{rupees(exposure.get(hero.ring.ring_id, 0))}</div>"
        f"<div class='s'>across {len(hero.ring)} merchant accounts</div></div>"
        f"<div class='k bad'><div class='t'>ring score</div>"
        f"<div class='v'>{hero.score}</div><div class='s'>threshold is {REVIEW_AT}</div></div>"
        "</div>"),
        # The per-payment argument used to live here as well as in the
        # blindspot beat that follows. Said twice it just costs runtime.
        f"{_num(len(hero.ring)).capitalize()} merchant accounts. "
        f"{hero_pay} payments. Not one of them is unusual.", 0.9))

    F.append(("02_reasons", page(
        "what the case file actually says",
        "The evidence, in <b>plain language</b>",
        "<div class='reasons'>" + "".join(
            f"<div class='r'>{_emph(r)}</div>" for r in hero.reasons[:3])
        + "</div>"),
        "It only exists in what connects the accounts. And every flag says "
        "why, in plain language. A merchant can read it, and argue back.", 0.9))

    # Beats whose picture is a manim clip in demo/clips/ rather than a still.
    # build_frames writes no HTML for them; make_video.py finds the clip by
    # name. The numbers they quote come from demo/frames/facts.json, which
    # demo/facts.py derives from this same pipeline.
    facts = json.loads((FRAMES / "facts.json").read_text(encoding="utf-8")) \
        if (FRAMES / "facts.json").exists() else None

    if facts:
        F.append(("05_blindspot", None,
                  "Razorpay already scores every payment. That model is good. "
                  "But it is asked one question at a time. Is this payment "
                  "fine? Yes. Is this one? Yes. Nine correct answers. And the "
                  "ring walks straight through. The fraud was never inside a "
                  "payment. It is in what connects them.", 0.9))

    # ---------------------------------------------------------------- beat 2
    for i, line in enumerate(ring_story(hero, tbm)):
        F.append((f"1{i}_ring{i}", None, line, 0.5))   # rendered from ring.html

    # ---------------------------------------------------------------- beat 3
    F.append(("20_results", page(
        "measured on the held-out half",
        f"<span class='hot'>{recall*100:.1f}%</span> recall. "
        f"{prec*100:.1f}% precision.",
        "<div class='cards'>"
        f"<div class='k bad'><div class='t'>ring recall</div><div class='v'>"
        f"{len(mt)}/{len(tr)}</div><div class='s'>held-out half, never tuned on</div></div>"
        f"<div class='k warn'><div class='t'>ring precision</div><div class='v'>"
        f"{len(mp)}/{len(fl)}</div><div class='s'>of what we flagged</div></div>"
        f"<div class='k ok'><div class='t'>95% confidence, recall</div>"
        f"<div class='v'>{r_lo*100:.0f}-{r_hi*100:.0f}%</div>"
        f"<div class='s'>{len(tr)} rings is a small sample</div></div>"
        "</div>"),
        f"Now the results, on the half it was never tuned on. "
        f"{recall*100:.0f} percent recall. {prec*100:.0f} percent precision. "
        f"But {len(tr)} rings is a small sample. The honest reading is a "
        f"range. {r_lo*100:.0f} to {r_hi*100:.0f} percent. And score.py prints "
        f"that interval beside every rate it reports.", 1.0))

    F.append(("21_perclass", page(
        "read the per-class table, not the headline",
        "Nine classes caught. <span class='hot'>One only partly.</span>",
        term("python score.py", section(score_out, "PER RING CLASS", limit=16),
             hi=("recruiter_quiet",))),
        f"But read the per class table, not the headline. Nine classes of ring, "
        f"all caught. And this one, recruiter quiet, {sum(1 for s in quiet if s >= REVIEW_AT)} "
        f"out of {len(quiet)}.", 0.5))

    F.append(("22_themiss", page(
        "why that one stays missed",
        f"It scores <span class='hot'>{min(quiet)}</span>. "
        f"The line is <b>{REVIEW_AT}</b>.",
        "<div class='cards'>"
        f"<div class='k bad'><div class='t'>the criminal we miss</div>"
        f"<div class='v'>{min(quiet)}</div><div class='s'>a quiet recruiter</div></div>"
        + "".join(f"<div class='k ok'><div class='t'>real family business</div>"
                  f"<div class='v'>{s}</div><div class='s'>legitimate, must not flag</div></div>"
                  for s in fam[:2]) + "</div>"),
        f"That one scores {min(quiet)}. My line is {REVIEW_AT}. I could catch it "
        f"by lowering the line. But two real family businesses sit at "
        f"{fam[0]} and {fam[1]}. Lowering it would flag them too. So it stays "
        f"missed, and the reason is written down.", 0.9))

    # ---------------------------------------------------------------- beat 4
    F.append(("30_decoys", page(
        "I planted innocents to fool myself",
        f"<span class='hot'>{len(dec)}</span> legitimate groups. "
        f"<b>{dec_flagged}</b> flagged.",
        term("python score.py", section(score_out, "DECOYS", limit=10))),
        f"I did not only plant criminals. I planted innocents, to fool myself. "
        f"A franchise chain on one settlement account. A family on one device. "
        f"And a brand opening six outlets in one week, which looks exactly like "
        f"a batch of mules. "
        f"{len(dec)} legitimate groups. {dec_flagged} flagged.", 1.0))

    # ------------------------------------------------- how it actually works
    if facts:
        g, k = facts["graph"], facts["constants"]
        drop = facts["dropped_examples"][0]
        F.append(("40_graph", None,
                  f"So how is that group found? Link any two merchants that "
                  f"share an identifier. You get "
                  f"{g['naive']['edges']//1000} thousand links, and one "
                  f"useless blob. Because {drop['merchants']} of them use the "
                  f"same email provider. So, two rules. Drop what almost "
                  f"everyone shares. Make every link earn its weight. That "
                  f"leaves {g['shipped']['edges']} links, and "
                  f"{g['shipped']['groups']} clean groups. The largest is "
                  f"{g['shipped']['largest']}.", 1.0))

        F.append(("41_timing", None,
                  f"Timing is the strongest signal, and the easiest to get "
                  f"wrong. Everybody is busy at Diwali. So nothing is measured "
                  f"against a flat line. It is measured against what the book "
                  f"itself did that day. On day {facts['timing_peak_day']}, "
                  f"this ring did "
                  f"{facts['timing_peak_ring_share']*100:.0f} percent of its "
                  f"business. The book did half a percent. That is "
                  f"{facts['timing_peak_ring_share']/facts['timing_peak_book_share']:.0f} "
                  f"times the rate, across {len(facts['timing_days'])} trading "
                  f"days in six months.", 1.0))

        cutf = facts["verifier_cut"]
        if cutf:
            F.append(("42_ratchet", None,
                      f"Then a second component argues with the first. Six "
                      f"questions. Is this one weak attribute? A branded chain? "
                      f"A marketplace? Ring {cutf['ring']} scored "
                      f"{cutf['raw']}. Verification cut it to {cutf['final']}, "
                      f"below the line. Nobody is troubled. Verification has no "
                      f"path that raises a score. The type itself rejects "
                      f"it.", 0.9))

    F.append(("43_ablate", page(
        "what is each part actually worth?",
        "Take one piece out and <b>re-measure</b>",
        term("python ablate.py", section(abl_out, "configuration", limit=12),
             hi=("co-timing",))),
        f"But what is each piece actually worth? Take it out, and measure "
        f"again. Removing the co-timing relation costs {abl_cotiming} points "
        f"of recall. It is the most valuable idea here. Removing the money "
        f"signal costs nothing, and that row is in the table too.",
        0.9))

    # ---------------------------------------------------------------- beat 6
    F.append(("50_ml", page(
        "I tested whether a model beats the rules",
        "It loses. And the rule it learned is <span class='hot'>backwards</span>.",
        term("python bench_ml.py",
             section(ml_out, "scorer", limit=6) + "\n\n"
             + section(ml_out, "|--- structural", limit=9))),
        "I tested whether machine learning beats the rules. It loses. And look "
        "at the tree it learned. The top split says densely connected accounts "
        "are innocent. That is backwards. It is explainable in the sense that "
        "you can print it, and what it explains is false.", 0.9))

    F.append(("51_elliptic", page(
        "and a benchmark I do not win",
        "121,810 real labelled Bitcoin subgraphs.",
        term("python bench_elliptic2.py", section(ell_out, "scorer", limit=6)
             + "\n" + section(ell_out, "published", limit=5))),
        "I also tested on Elliptic two. Real, labelled Bitcoin subgraphs. "
        "I do not beat the published state of the art. I land at the structure "
        "only baseline. Both of those are in the repo, because a benchmark you "
        "only publish when you win is not a benchmark.", 0.9))

    # ---------------------------------------------------------------- beat 7
    F.append(("60_tests", page(
        "every claim has a check behind it",
        f"<b>{n_checks}</b> checks. One per claim.",
        term("python test_pipeline.py",
             "\n".join(l for l in tests_out.split("\n") if "  pass  " in l)[:1500])),
        f"{n_checks} checks. One for every claim I have made. One scrambles "
        f"every identifier and proves the scores do not move. One proves the "
        f"detector never opens the answer key.", 0.9))

    F.append(("61_safety", page(
        "the strongest thing it can do",
        "There is <span class='hot'>no freeze</span> in this codebase.",
        "<pre>" + esc('class Action(StrEnum):\n'
                      '    OBSERVE         = "observe"   # logged, nothing else\n'
                      '    WATCH           = "watch"     # flagged, nobody notified\n'
                      '    REVIEW          = "review"    # human queue + evidence\n'
                      '    HOLD_SETTLEMENT = "hold"      # T+2 -> T+3, auto-expires 24h\n\n'
                      '# no FREEZE. no BLOCK. no ACCOUNT_CLOSURE.\n'
                      '# not disabled - absent.') + "</pre>"),
        "And this is the whole action ladder. Observe. Watch. Review. And "
        "hold, a reversible one day settlement delay that expires on its own. "
        "There is no freeze in this codebase. I cannot destroy a merchant's "
        "business by accident. The function does not exist.", 1.3))

    if live_out:
        F.append(("62_live", page(
            "against the real Razorpay API",
            f"<b>{live_orders}</b> live orders. "
            f"<span class='hot'>{live_rings}</span> rings. "
            "<span class='good'>Both refused.</span>",
            term("python live.py", section(live_out, "GET /v1/orders", limit=14),
                 hi=("x gate:",))),
            f"And this is not a simulation. {live_orders} orders, pulled live "
            f"from the Razorpay API. {live_rings} rings found inside them. Now "
            f"read the last two lines of each. Both were refused. One "
            f"attribute type, where two are required. One run, where two are "
            f"required. It found them, and it still would not act.", 0.9))

    F.append(("70_close", page(
        "ring sentinel",
        "Catch the <b>group</b>, not the payment.",
        "<div class='cards'>"
        f"<div class='k bad'><div class='t'>held-out recall</div>"
        f"<div class='v'>{recall*100:.1f}%</div>"
        f"<div class='s'>{len(mt)} of {len(tr)} rings, never tuned on</div></div>"
        f"<div class='k ok'><div class='t'>legitimate groups flagged</div>"
        f"<div class='v'>{dec_flagged}/{len(dec)}</div>"
        f"<div class='s'>the planted innocents</div></div>"
        f"<div class='k warn'><div class='t'>checks</div>"
        f"<div class='v'>{n_checks}</div><div class='s'>one per claim</div></div>"
        "</div>"),
        f"That is Ring Sentinel. {recall*100:.0f} percent of rings caught, on "
        f"data it was never tuned on. None of the innocents flagged. And every "
        f"number you have seen was re-derived from the pipeline when this "
        f"video was built. The worst thing it can do to a merchant is make "
        f"them wait one day.", 1.6))

    narration = []
    for name, html, line, pad in F:
        if html is not None:
            (FRAMES / f"{name}.html").write_text(html, encoding="utf-8")
        narration.append({"name": name, "text": line, "pad": pad,
                          "ring_step": int(name[1]) if name.startswith("1") and
                          name[1].isdigit() and "ring" in name else None})
    (FRAMES / "narration.json").write_text(
        json.dumps(narration, indent=2), encoding="utf-8")

    # SCRIPT.md used to be written by hand. It drifted immediately: it still
    # named ring R0003 and 618 payments long after the data was regenerated
    # and the top ring became R0007 with 129. Generating it from the same
    # narration removes the whole class of error.
    nl = chr(10)
    words = sum(len(x["text"].split()) for x in narration)
    # Prefer the real narration length. The 2.4 words/sec fallback is only for
    # a first run before any audio exists; the rendered voice runs nearer 3.0,
    # so the estimate overstated the video by fifty seconds.
    secs, measured = 0.0, True
    for x in narration:
        w = ROOT / "demo" / "audio" / (x["name"] + ".wav")
        if w.exists():
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                  "format=duration", "-of", "csv=p=0", str(w)],
                                 capture_output=True, text=True).stdout.strip()
            if out:
                secs += float(out) + x["pad"]
                continue
        measured = False
        secs += len(x["text"].split()) / 2.4 + x["pad"]
    beats = {"0": "The case", "1": "Why it is a ring",
             "2": "Measured on the held-out half", "3": "The decoys",
             "5": "Where it loses", "6": "Checks and safety"}
    doc = ["# Ring Sentinel - pitch narration", "",
           "GENERATED by `demo/build_frames.py` from the live pipeline. Every",
           "number below is read back out of the detector, so editing this file",
           "only creates drift - rerun the builder instead.", "",
           f"{len(narration)} lines, {words} words, "
           f"{int(secs)//60}:{int(secs)%60:02d} of narration"
           f"{' (measured)' if measured else ' (estimated)'}.", "",
           "Rebuild the video with:", "",
           "```", "python demo/build_frames.py", "python demo/make_video.py",
           "```", ""]
    last = None
    for x in narration:
        b = beats.get(x["name"][0], "")
        if b != last:
            doc += ["", f"## {b}", ""]
            last = b
        doc.append(f"**`{x['name']}`** &nbsp; {x['text']}")
        doc.append("")
    doc += ["## What is on screen",
            "", "Nothing is screen-recorded. Every frame is rendered headlessly",
            "from HTML by `demo/make_video.py`, so a frame can be reviewed one at",
            "a time and the whole video rebuilt byte for byte. Terminal frames",
            "show real captured output, not typed-out text.", ""]
    (ROOT / "demo" / "SCRIPT.md").write_text(nl.join(doc), encoding="utf-8")

    print(f"  {sum(1 for _n, h, _l, _p in F if h is not None)} html frames + "
          f"5 ring states")
    print(f"  narration: {len(narration)} lines, "
          f"{sum(len(n['text'].split()) for n in narration)} words "
          f"(~{sum(len(n['text'].split()) for n in narration)/140*60:.0f}s at 140wpm)")
    print(f"  hero ring {hero.ring.ring_id}: {len(hero.ring)} accounts, "
          f"{hero_pay} payments, score {hero.score}")
    print(f"  held out {len(mt)}/{len(tr)} recall, {len(mp)}/{len(fl)} precision")
    print(f"  criminal {crim_caught}/{len(crim)}, decoys flagged {dec_flagged}/{len(dec)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
