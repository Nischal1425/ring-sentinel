"""Animated segments for the pitch, rendered with manim.

    python demo/facts.py            # first - derives every number used here
    python demo/render_scenes.py    # renders these into demo/clips/

Each scene is deliberately authored SHORTER than the narration that plays over
it. make_video.py freezes the last frame to fill the remainder, so a scene can
never run past its voice-over and desync the rest of the video.

No figure in this file is typed by hand. Everything comes out of facts.json,
which demo/facts.py derives from the live pipeline, for the same reason the
HTML frames derive theirs: hand-written numbers go stale the next time the
data is regenerated and nobody notices until it is on screen.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from manim import *

ROOT = Path(__file__).resolve().parent.parent
FACTS = json.loads((ROOT / "demo" / "frames" / "facts.json").read_text(encoding="utf-8"))

config.background_color = "#0f1115"

INK, DIM = "#e7eaf0", "#8b93a3"
HOT, WARM, COOL, GOOD = "#ff6b6b", "#ffa94d", "#4dabf7", "#51cf66"
CARD, LINE = "#171a21", "#252a34"
FONT = "Segoe UI"


def header(kicker: str, title: str):
    """Top-left kicker + headline, matching the HTML frames' typography."""
    k = Text(kicker.upper(), font=FONT, font_size=21, color=DIM).set_opacity(0.9)
    t = Text(title, font=FONT, font_size=40, color=INK, weight=SEMIBOLD)
    g = VGroup(k, t).arrange(DOWN, aligned_edge=LEFT, buff=0.18)
    return g.to_corner(UL, buff=0.7)


def stat(label: str, value, colour=INK, size=54):
    v = value if isinstance(value, Mobject) else Text(
        str(value), font=FONT, font_size=size, color=colour, weight=BOLD)
    l = Text(label.upper(), font=FONT, font_size=17, color=DIM)
    return VGroup(v, l).arrange(DOWN, buff=0.16)


class Blindspot(Scene):
    """The thesis: every payment is fine, the group is not."""

    def construct(self):
        hero = FACTS["hero"]
        head = header("the blind spot", "Score payments one at a time")
        self.play(FadeIn(head, shift=DOWN * 0.2), run_time=0.7)

        model = RoundedRectangle(width=2.6, height=1.25, corner_radius=0.14,
                                 stroke_color=LINE, fill_color=CARD,
                                 fill_opacity=1).shift(DOWN * 0.3)
        label = Text("per-payment\nmodel", font=FONT, font_size=20, color=DIM,
                     line_spacing=0.7).move_to(model)
        self.play(Create(model), FadeIn(label), run_time=0.6)

        rng = random.Random(7)
        chips = VGroup(*[
            RoundedRectangle(width=1.15, height=0.5, corner_radius=0.1,
                             stroke_color=LINE, fill_color=CARD, fill_opacity=1)
            for _ in range(6)])
        for i, c in enumerate(chips):
            c.move_to(LEFT * (6.6 + i * 1.45) + DOWN * 0.3)
            amt = Text(f"Rs {rng.randint(28, 40)},{rng.randint(100, 999)}",
                       font=FONT, font_size=15, color=GOOD).move_to(c)
            c.add(amt)

        self.add(chips)
        ticks = VGroup()
        for i, c in enumerate(chips):
            self.play(c.animate.move_to(RIGHT * (2.6 + i * 0.05) + DOWN * 0.3),
                      run_time=0.34, rate_func=linear)
            tick = Text("approved", font=FONT, font_size=15, color=GOOD)
            tick.next_to(c, RIGHT, buff=0.2)
            ticks.add(tick)
            self.add(tick)
            c.set_opacity(0.35)
            tick.set_opacity(0.35)

        verdict = Text("Every one of them: correct.", font=FONT, font_size=26,
                       color=GOOD).next_to(model, DOWN, buff=1.15)
        self.play(FadeIn(verdict), run_time=0.5)
        self.wait(0.5)

        self.play(FadeOut(chips), FadeOut(ticks), FadeOut(model), FadeOut(label),
                  FadeOut(verdict), run_time=0.5)

        # Same payments, regrouped by who they belong to.
        n = hero["members"]
        nodes = VGroup(*[Dot(radius=0.14, color=INK).set_opacity(0.85)
                         for _ in range(n)])
        for i, d in enumerate(nodes):
            ang = TAU * i / n - PI / 2
            d.move_to(np.array([np.cos(ang) * 2.5, np.sin(ang) * 1.85 - 0.3, 0]))
        self.play(LaggedStart(*[GrowFromCenter(d) for d in nodes],
                              lag_ratio=0.06), run_time=0.9)

        links = VGroup()
        for i in range(n):
            for j in range(i + 1, n):
                links.add(Line(nodes[i].get_center(), nodes[j].get_center(),
                               stroke_width=1.6, color=HOT).set_opacity(0.42))
        self.play(Create(links), run_time=1.2)
        self.play(nodes.animate.set_color(HOT), run_time=0.4)

        punch = Text("The fraud is in what connects them.", font=FONT,
                     font_size=28, color=INK).to_edge(DOWN, buff=0.85)
        self.play(FadeIn(punch, shift=UP * 0.15), run_time=0.6)
        self.wait(0.8)


class GiantComponent(Scene):
    """Why a naive shared-attribute graph is useless, and the two guards."""

    def construct(self):
        g = FACTS["graph"]
        c = FACTS["constants"]
        naive, shipped = g["naive"], g["shipped"]
        drop = FACTS["dropped_examples"][0]

        head = header("building the graph",
                      "Link merchants that share an identifier")
        self.play(FadeIn(head, shift=DOWN * 0.2), run_time=0.7)

        rng = random.Random(11)
        pts = []
        while len(pts) < 130:                       # a disc of merchants
            x, y = rng.uniform(-1, 1), rng.uniform(-1, 1)
            if x * x + y * y <= 1:
                pts.append(np.array([x * 3.5 - 1.4, y * 2.35 - 0.55, 0]))
        dots = VGroup(*[Dot(point=p, radius=0.055, color=INK).set_opacity(0.75)
                        for p in pts])
        self.play(LaggedStart(*[GrowFromCenter(d) for d in dots],
                              lag_ratio=0.004), run_time=0.9)

        panel = VGroup(
            stat("links", f"{naive['edges']:,}", HOT, 46),
            stat("biggest group", f"{naive['largest']:,}", HOT, 46),
        ).arrange(DOWN, buff=0.5).to_edge(RIGHT, buff=1.0).shift(DOWN * 0.2)

        hair = VGroup(*[
            Line(pts[rng.randrange(130)], pts[rng.randrange(130)],
                 stroke_width=0.9, color=HOT).set_opacity(0.30)
            for _ in range(430)])
        note = Text("links drawn are a sample", font=FONT, font_size=15,
                    color=DIM).set_opacity(0.75).to_edge(DOWN, buff=0.55)

        self.play(Create(hair), FadeIn(panel), FadeIn(note), run_time=1.5)
        blob = Text(f"one component, {naive['largest']:,} of "
                    f"{FACTS['merchants']:,} merchants",
                    font=FONT, font_size=24, color=HOT)
        blob.next_to(head, DOWN, aligned_edge=LEFT, buff=0.45)
        self.play(FadeIn(blob), run_time=0.5)
        self.wait(0.7)

        # Guard one: the document-frequency cap.
        rule1 = Text(f"drop any value shared by more than {c['DF_CAP']} merchants",
                     font=FONT, font_size=23, color=WARM)
        rule1.move_to(blob, aligned_edge=LEFT)
        why = Text(f"{drop['value']} - {drop['merchants']} merchants",
                   font=FONT, font_size=19, color=DIM)
        why.next_to(rule1, DOWN, aligned_edge=LEFT, buff=0.2)
        self.play(FadeOut(blob), FadeIn(rule1), FadeIn(why), run_time=0.6)
        self.play(hair[130:].animate.set_opacity(0.0), run_time=1.0)

        # Guard two: the edge weight floor.
        rule2 = Text(f"and require an edge weight of at least {c['EDGE_MIN']}",
                     font=FONT, font_size=23, color=WARM)
        rule2.move_to(rule1, aligned_edge=LEFT)
        self.play(FadeOut(why), Transform(rule1, rule2), run_time=0.6)
        self.play(hair[26:130].animate.set_opacity(0.0), run_time=0.9)

        # What is left: small, tight groups.
        clusters = [np.array([rng.uniform(-4.4, 1.4), rng.uniform(-2.6, 1.4), 0])
                    for _ in range(14)]
        moves = []
        for i, d in enumerate(dots):
            home = clusters[i % 14] + np.array(
                [rng.uniform(-0.28, 0.28), rng.uniform(-0.28, 0.28), 0])
            moves.append(d.animate.move_to(home))
        self.play(*moves, FadeOut(hair[:26]), run_time=1.3)

        new_panel = VGroup(
            stat("links", f"{shipped['edges']:,}", GOOD, 46),
            stat("biggest group", f"{shipped['largest']:,}", GOOD, 46),
        ).arrange(DOWN, buff=0.5).move_to(panel)
        done = Text(f"{shipped['groups']} separate groups, "
                    f"largest {shipped['largest']}",
                    font=FONT, font_size=24, color=GOOD).move_to(rule1,
                                                                 aligned_edge=LEFT)
        self.play(Transform(panel, new_panel), Transform(rule1, done),
                  FadeOut(note), run_time=0.8)      # no links left to disclaim
        self.wait(0.9)


class NullModel(Scene):
    """Timing evidence, measured against the book's own calendar."""

    def construct(self):
        days = FACTS["timing_days"]
        rs = FACTS["timing_ring_share"]
        bs = FACTS["timing_book_share"]
        peak = FACTS["timing_peak_day"]
        pr, pb = FACTS["timing_peak_ring_share"], FACTS["timing_peak_book_share"]

        head = header("timing", "Busy together, or just busy?")
        self.play(FadeIn(head, shift=DOWN * 0.2), run_time=0.7)

        ax = Axes(x_range=[0, len(days) + 1, 1], y_range=[0, max(rs) * 1.18, 0.05],
                  x_length=8.4, y_length=3.9,
                  axis_config={"stroke_color": LINE, "include_ticks": False,
                               "stroke_width": 2})
        ax.shift(DOWN * 0.5 + LEFT * 1.0)
        ylab = Text("share of its own payments", font=FONT, font_size=17,
                    color=DIM).rotate(PI / 2).next_to(ax, LEFT, buff=0.22)
        self.play(Create(ax), FadeIn(ylab), run_time=0.8)

        # The book's own profile first - this is the null, not a flat line.
        book_bars = VGroup(*[
            Rectangle(width=0.44, height=max(ax.c2p(0, v)[1] - ax.c2p(0, 0)[1],
                                             0.012),
                      stroke_width=0, fill_color=COOL, fill_opacity=0.75)
            .move_to(ax.c2p(i + 1, 0), aligned_edge=DOWN)
            for i, v in enumerate(bs)])
        blab = Text("what the whole book did that day", font=FONT, font_size=19,
                    color=COOL)
        blab.next_to(ax, UP, buff=0.28).align_to(ax, LEFT)
        self.play(LaggedStart(*[GrowFromEdge(b, DOWN) for b in book_bars],
                              lag_ratio=0.08), FadeIn(blab), run_time=1.1)
        self.wait(0.4)

        ring_bars = VGroup(*[
            Rectangle(width=0.44, height=ax.c2p(0, v)[1] - ax.c2p(0, 0)[1],
                      stroke_width=0, fill_color=HOT, fill_opacity=0.85)
            .move_to(ax.c2p(i + 1, 0) + RIGHT * 0.46, aligned_edge=DOWN)
            for i, v in enumerate(rs)])
        rlab = Text("what this ring did", font=FONT, font_size=19, color=HOT)
        rlab.next_to(blab, DOWN, aligned_edge=LEFT, buff=0.14)
        self.play(LaggedStart(*[GrowFromEdge(b, DOWN) for b in ring_bars],
                              lag_ratio=0.08), FadeIn(rlab), run_time=1.2)

        i_peak = days.index(peak)
        ring = SurroundingRectangle(ring_bars[i_peak], color=WARM, buff=0.06,
                                    stroke_width=2.5)
        call = Text(f"day {peak}:  {pr*100:.0f}%  vs  {pb*100:.1f}%",
                    font=FONT, font_size=25, color=WARM)
        call.next_to(ax, DOWN, buff=0.3)
        self.play(Create(ring), FadeIn(call), run_time=0.7)

        # Stacked under the call-out, not both anchored to the frame edge -
        # the first version drew them on top of each other.
        ratio = Text(f"{pr/pb:.0f} times the book's rate, on {len(days)} "
                     f"trading days in six months",
                     font=FONT, font_size=23, color=INK)
        ratio.next_to(call, DOWN, buff=0.22)
        self.play(FadeIn(ratio), run_time=0.6)
        self.wait(1.0)


class Ratchet(Scene):
    """Verification is a ratchet: it can only ever lower a score."""

    def construct(self):
        cut = FACTS["verifier_cut"]
        c = FACTS["constants"]
        head = header("the second opinion", "Verification can only subtract")
        self.play(FadeIn(head, shift=DOWN * 0.2), run_time=0.7)

        # A 5-unit track pushed its own readout up into the headline. Shorter
        # and lower, so the number above it has clear air.
        track = RoundedRectangle(width=1.4, height=4.0, corner_radius=0.16,
                                 stroke_color=LINE, fill_color=CARD,
                                 fill_opacity=1).shift(LEFT * 4.9 + DOWN * 1.0)
        self.play(Create(track), run_time=0.5)

        span = 3.72
        base = track.get_bottom() + UP * 0.14
        val = ValueTracker(cut["raw"])

        bar = always_redraw(lambda: Rectangle(
            width=1.14, height=max(span * val.get_value() / 100, 0.04),
            stroke_width=0,
            fill_color=HOT if val.get_value() >= c["REVIEW_AT"] else GOOD,
            fill_opacity=0.9).move_to(base, aligned_edge=DOWN))
        # Inside the head of the track. Above it, the readout collided with the
        # caption under the headline. Clear of the bar for any score below
        # about 90; this scene starts at the verifier's real raw score.
        num = always_redraw(lambda: Text(
            f"{val.get_value():.0f}", font=FONT, font_size=40, weight=BOLD,
            color=HOT if val.get_value() >= c["REVIEW_AT"] else GOOD)
            .move_to(track.get_top() + DOWN * 0.42))
        self.add(bar, num)

        line_y = base + UP * span * c["REVIEW_AT"] / 100
        thresh = DashedLine(line_y + LEFT * 1.0, line_y + RIGHT * 1.0,
                            color=WARM, stroke_width=2)
        # Above the line rather than beside it: to the right it ran straight
        # into the list of checks.
        tlab = Text(f"review at {c['REVIEW_AT']}", font=FONT, font_size=16,
                    color=WARM).next_to(thresh, UP, buff=0.1)
        self.play(Create(thresh), FadeIn(tlab), run_time=0.5)

        raw_lab = Text(f"ring {cut['ring']} scored {cut['raw']} on the evidence",
                       font=FONT, font_size=24, color=INK)
        raw_lab.next_to(head, DOWN, aligned_edge=LEFT, buff=0.5)
        self.play(FadeIn(raw_lab), run_time=0.5)

        checks = ["is it one weak attribute?", "is it a branded chain?",
                  "related but unremarkable?", "hub shaped, like a marketplace?",
                  "does it just ride the portfolio?",
                  "an artifact of our own threshold?"]
        rows = VGroup(*[Text(x, font=FONT, font_size=21, color=DIM)
                        for x in checks]).arrange(DOWN, aligned_edge=LEFT,
                                                  buff=0.26)
        rows.move_to(RIGHT * 2.0 + DOWN * 0.95)
        self.play(LaggedStart(*[FadeIn(r, shift=RIGHT * 0.1) for r in rows],
                              lag_ratio=0.12), run_time=1.3)

        fired = rows[2]
        self.play(fired.animate.set_color(WARM), run_time=0.35)
        self.play(val.animate.set_value(cut["final"]), run_time=1.3)

        out = Text(f"{cut['raw']}  to  {cut['final']}.  Below the line, so nobody "
                   f"is troubled.", font=FONT, font_size=24, color=GOOD)
        out.move_to(raw_lab, aligned_edge=LEFT)
        self.play(FadeOut(raw_lab), FadeIn(out), run_time=0.6)

        never = Text("It has no path that raises a score. The type rejects it.",
                     font=FONT, font_size=22, color=DIM).to_edge(DOWN, buff=0.55)
        self.play(FadeIn(never), run_time=0.6)
        self.wait(0.9)
