"""Derive every number the animated scenes quote, and cache it as JSON.

    python demo/facts.py          # writes demo/frames/facts.json

The manim scenes in demo/scenes.py read this file. They never contain a typed
figure, for the same reason the HTML frames do not: a number written by hand
goes stale the next time the data is regenerated, and nobody notices until it
is on screen. The naive-graph measurement below is slow enough (221k edges)
that it is worth caching rather than recomputing inside every render.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FRAMES = ROOT / "demo" / "frames"
FRAMES.mkdir(parents=True, exist_ok=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def graph_shape(merchants, cap, edge_min):
    """Edge count, largest connected component and group count at one setting."""
    from sentinel import graph
    kept, dropped, _ = graph.invert(merchants, df_cap=cap)
    edges = graph.build_edges(merchants, kept, df_cap=cap, edge_min=edge_min)

    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, *_ in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comp = collections.Counter(find(x) for x in parent)
    return {"edges": len(edges),
            "largest": max(comp.values()) if comp else 0,
            "groups": len(comp),
            "dropped_values": len(dropped)}


def main():
    from sentinel import graph, respond, temporal, verify
    from run import analyse

    merchants = graph.load_merchants()
    n = len(merchants)
    print(f"  {n} merchants - measuring the graph at four settings")

    shapes = {
        "naive": graph_shape(merchants, n, 0.0),
        "cap_only": graph_shape(merchants, graph.DF_CAP, 0.0),
        "floor_only": graph_shape(merchants, n, graph.EDGE_MIN),
        "shipped": graph_shape(merchants, graph.DF_CAP, graph.EDGE_MIN),
    }
    for k, v in shapes.items():
        print(f"    {k:11} edges={v['edges']:7} largest={v['largest']:5} "
              f"groups={v['groups']:4}")

    # The commonest attribute values the cap removes, which is the whole reason
    # the naive graph collapses into one component.
    _, dropped, _ = graph.invert(merchants, df_cap=graph.DF_CAP)
    biggest = sorted(dropped, key=lambda d: -d[1])[:4]

    verdicts, exposure, _stats, _m, tbm, _p = analyse(quiet=True)
    hero = max(verdicts, key=lambda v: (v.score, len(v.ring)))

    # A real case where the verifier lowered a score. Prefer one it pulled from
    # ABOVE the review line to below it - that is the case that actually spares
    # a merchant. The largest raw drop is a weaker example: the first candidate
    # found scored 68 against a threshold of 70, so it was never going to be
    # actioned and the ratchet changed nothing. Fall back to it only if no ring
    # crossed the line this run, and say so rather than inventing one.
    lowered = sorted((v for v in verdicts if v.score < v.raw_score),
                     key=lambda v: v.raw_score - v.score, reverse=True)
    crossed = [v for v in lowered
               if v.raw_score >= respond.REVIEW_AT > v.score]
    pick = (crossed or lowered or [None])[0]
    cut = ({"ring": pick.ring.ring_id, "raw": pick.raw_score,
            "final": pick.score, "members": len(pick.ring),
            "crossed_threshold": bool(crossed)}
           if pick else None)

    # The timing signal compares SHARES, not volumes. velocity.py measures a
    # one-sided total-variation distance: how much of the ring's own activity
    # falls above what the portfolio's day profile predicts. Day 94 is not a
    # quiet day in absolute terms - it is slightly above the book's median -
    # but the ring puts a fifth of its life into it while the book puts half a
    # percent. Plotting raw counts would have shown the wrong thing entirely.
    from sentinel.signals import velocity as _vel

    ring_day = collections.Counter()
    for m in hero.ring.members:
        for t in tbm.get(m, []):
            ring_day[t.day] += 1
    ring_total = sum(ring_day.values())
    days = sorted(ring_day)
    ring_share = [ring_day[d] / ring_total for d in days]
    book_share = [_p.day_share(d) for d in days]
    peak = max(days, key=lambda d: ring_day[d]) if days else 0
    vel = _vel.detect(hero.ring, tbm, _p)

    facts = {
        "merchants": n,
        "timing_days": days,
        "timing_ring_share": ring_share,
        "timing_book_share": book_share,
        "timing_peak_day": peak,
        "timing_peak_ring_share": ring_day[peak] / ring_total if days else 0,
        "timing_peak_book_share": _p.day_share(peak) if days else 0,
        "timing_score": round(vel.score, 4) if vel else None,
        "timing_reasons": list(vel.reasons) if vel else [],
        "payments": sum(len(v) for v in tbm.values()),
        "graph": shapes,
        "dropped_examples": [{"attr": k[0], "value": k[1], "merchants": c}
                             for k, c in biggest],
        "constants": {
            "DF_CAP": graph.DF_CAP, "EDGE_MIN": graph.EDGE_MIN,
            "MIN_RING": graph.MIN_RING, "SIZE_CAP": graph.SIZE_CAP,
            "Z_MIN": temporal.Z_MIN, "MIN_ACTIVE_DAYS": temporal.MIN_ACTIVE_DAYS,
            "WATCH_AT": respond.WATCH_AT, "REVIEW_AT": respond.REVIEW_AT,
            "HOLD_AT": respond.HOLD_AT,
            "MIN_ATTR_TYPES": respond.MIN_ATTR_TYPES,
            "HYSTERESIS_RUNS": respond.HYSTERESIS_RUNS,
            "CORROBORATION": verify.CORROBORATION,
        },
        "hero": {"ring": hero.ring.ring_id, "score": hero.score,
                 "members": len(hero.ring),
                 "payments": sum(len(tbm.get(m, [])) for m in hero.ring.members),
                 "exposure_paise": exposure.get(hero.ring.ring_id, 0)},
        "verifier_cut": cut,
        "rings_found": len(verdicts),
        "rings_at_review": sum(1 for v in verdicts if v.score >= respond.REVIEW_AT),
    }
    (FRAMES / "facts.json").write_text(json.dumps(facts, indent=2),
                                       encoding="utf-8")
    print(f"  wrote {FRAMES / 'facts.json'}")
    if cut:
        print(f"    verifier example: {cut['ring']} {cut['raw']} -> {cut['final']}")
    else:
        print("    NOTE: the verifier lowered nothing this run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
