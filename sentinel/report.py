"""Case files as one self-contained HTML page. No framework, no CDN.

Ported from D:/razorpay/fincontroller/report.py. Inline SVG, inline CSS, one
file that opens from disk and cannot break on stage.

This page IS the product. A score is not an answer a merchant can argue with;
a case file is. Every panel exists to let a reviewer disagree: which accounts,
which attribute linked each pair, what every signal said, what innocent
explanations were considered and rejected, which gate stopped the action, and
how to reverse it.
"""

from __future__ import annotations

import html
from collections import Counter
from pathlib import Path

from .ingest import rupees
from .respond import HOLD_AT, REVIEW_AT

CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#252a34;--ink:#e7eaf0;--dim:#8b93a3;
--hot:#ff6b6b;--warm:#ffa94d;--cool:#4dabf7;--good:#51cf66}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--dim);margin-bottom:28px}
.case{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:20px;margin-bottom:18px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
flex-wrap:wrap;margin-bottom:14px}
.rid{font-weight:600;font-size:16px}
.score{font-variant-numeric:tabular-nums;font-weight:700;font-size:26px}
.tag{display:inline-block;padding:2px 9px;border-radius:99px;font-size:11px;
text-transform:uppercase;letter-spacing:.06em;border:1px solid currentColor}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
h3{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
margin:0 0 8px;font-weight:600}
ul{margin:0;padding-left:18px}li{margin-bottom:5px}
.ex li{color:var(--warm)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:12px}
td,th{text-align:left;padding:3px 8px 3px 0;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500}
.bar{height:7px;border-radius:4px;background:#22262f;overflow:hidden;
margin-top:3px}
.bar>i{display:block;height:100%;border-radius:4px}
.sig{margin-bottom:9px}
.sig .lbl{display:flex;justify-content:space-between;font-size:12px}
.gate{color:var(--cool);font-size:12px;margin-top:6px}
.foot{color:var(--dim);font-size:12px;margin-top:14px;border-top:1px solid
var(--line);padding-top:10px}
"""


def _colour(score):
    return "var(--hot)" if score >= HOLD_AT else (
        "var(--warm)" if score >= REVIEW_AT else "var(--cool)")


def _signal_bars(signals):
    # Derived from the fusion weights, never hand-listed. A hardcoded order
    # silently dropped the tenure signal from every case file the day it
    # shipped - the evidence was computed, scored, and then not shown.
    from .detect import WEIGHTS
    order = sorted(WEIGHTS, key=lambda k: -WEIGHTS[k])
    out = []
    for name in order:
        if name not in signals:
            # An absent signal is not a zero. Say so rather than draw an empty
            # bar a reader will misread as "checked, clean".
            out.append(f'<div class="sig"><div class="lbl"><span>{name}</span>'
                       f'<span style="color:var(--dim)">abstained</span></div></div>')
            continue
        v = signals[name]
        out.append(
            f'<div class="sig"><div class="lbl"><span>{name}</span>'
            f'<span class="mono">{v:.2f}</span></div>'
            f'<div class="bar"><i style="width:{v * 100:.0f}%;'
            f'background:{_colour(v * 100)}"></i></div></div>')
    return "".join(out)


def _edges(ring, limit=8):
    rows = []
    for a, b, w, kinds in sorted(ring.edges, key=lambda e: -e[2])[:limit]:
        why = ", ".join(k.replace("_", " ") for k in kinds)
        rows.append(f"<tr><td class='mono'>{html.escape(a)}</td>"
                    f"<td class='mono'>{html.escape(b)}</td>"
                    f"<td class='mono'>{w:.2f}</td><td>{html.escape(why)}</td></tr>")
    more = (f"<tr><td colspan=4 style='color:var(--dim)'>"
            f"+{len(ring.edges) - limit} more</td></tr>"
            if len(ring.edges) > limit else "")
    return ("<table><tr><th>account</th><th>linked to</th><th>weight</th>"
            f"<th>because they share</th></tr>{''.join(rows)}{more}</table>")


def _case(v, entry, merchants, exposure):
    names = Counter(merchants.get(m, {}).get("name", "?") for m in v.ring.members)
    action = entry["action"]
    gates = entry.get("gates_that_stopped_escalation") or []

    reasons = "".join(f"<li>{html.escape(r)}</li>" for r in v.reasons) or \
        "<li style='color:var(--dim)'>No individual signal was decisive.</li>"
    exons = "".join(f"<li>{html.escape(e)}</li>" for e in v.exonerations) or \
        "<li style='color:var(--dim)'>None of the innocent explanations fitted.</li>"

    gate_html = ""
    if gates:
        gate_html = ("<div class='gate'>Escalation stopped by: "
                     + "; ".join(html.escape(g) for g in gates) + "</div>")

    reversal = ""
    if entry.get("reversal"):
        # Escaped like everything else. This one is generated by respond.py and
        # is therefore not attacker-controlled today - which is exactly why it
        # was the field that got missed. Merchant names, which ARE chosen by
        # merchants, were escaped from the start.
        reversal = (f"<div class='foot'>Auto-expires "
                    f"{html.escape(str(entry['expires_at']))} unless "
                    f"a human confirms. Reverse with "
                    f"<span class='mono'>{html.escape(entry['reversal'])}</span>."
                    f"</div>")

    return f"""
<div class="case">
  <div class="top">
    <div>
      <div class="rid">{html.escape(v.ring.ring_id)} &middot;
        {len(v.ring)} accounts &middot; {rupees(exposure)} exposure</div>
      <div class="mono" style="color:var(--dim)">
        {html.escape(', '.join(list(names)[:3]))}{' &hellip;' if len(names) > 3 else ''}
      </div>
    </div>
    <div style="text-align:right">
      <div class="score" style="color:{_colour(v.score)}">{v.score}</div>
      <span class="tag" style="color:{_colour(v.score)}">{html.escape(action)}</span>
    </div>
  </div>
  <div class="grid">
    <div>
      <h3>Why we flagged it</h3><ul>{reasons}</ul>
      <h3 style="margin-top:16px">Innocent explanations considered</h3>
      <ul class="ex">{exons}</ul>
      {gate_html}
    </div>
    <div>
      <h3>Signals &mdash; verified {v.score} of raw {v.raw_score}</h3>
      {_signal_bars(v.signals)}
      <h3 style="margin-top:16px">What links these accounts</h3>
      {_edges(v.ring)}
    </div>
  </div>
  {reversal}
</div>"""


def write(verdicts, entries, merchants, exposure, path="report.html", stats=None):
    """One page, every case, worst first."""
    by_ring = {e["ring_id"]: e for e in entries}
    shown = [v for v in sorted(verdicts, key=lambda x: -x.score)
             if v.score >= REVIEW_AT]

    cases = "".join(_case(v, by_ring.get(v.ring.ring_id, {"action": "observe"}),
                          merchants, exposure.get(v.ring.ring_id, 0))
                    for v in shown)
    s = stats or {}
    sub = (f"{s.get('merchants', 0):,} merchants &rarr; {s.get('edges', 0):,} links "
           f"&rarr; {s.get('rings', 0)} candidate groups &rarr; {len(shown)} worth "
           f"a reviewer's time. Nothing here freezes an account; the strongest "
           f"available action is a reversible 24-hour settlement delay.")

    page = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Ring Sentinel - case files</title><style>{CSS}</style></head>"
            f"<body><div class='wrap'><h1>Ring Sentinel &mdash; case files</h1>"
            f"<div class='sub'>{sub}</div>{cases}</div></body></html>")

    Path(path).write_text(page, encoding="utf-8")
    return len(shown)
