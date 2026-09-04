"""Build subtitles for the pitch video from the narration and the rendered audio.

    python demo/subtitles.py          # writes demo/ring-sentinel-pitch.srt + .ass

Timing comes from the audio that was actually rendered, not from a words-per-minute
guess, so the cues cannot drift away from the voice.

Within one narration line the split is by sentence, then each boundary is SNAPPED to
a real pause found by ffmpeg's silencedetect. Proportional splitting alone drifts by a
second or so mid-line, which is exactly where a caption looks broken; snapping to the
breath the speaker actually took fixes it. Where no pause is found the proportional
estimate stands, so a line with no detectable pause still gets sane cues.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
AUDIO = DEMO / "audio"
FRAMES = DEMO / "frames"

FFMPEG = shutil.which("ffmpeg") or str(
    Path.home() / "AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe")

MAX_CHARS = 42        # per rendered line, the usual readability limit
MAX_LINES = 2
SNAP_WINDOW = 0.55    # seconds either side of an estimated boundary
MIN_CUE = 1.0         # no caption flashes by faster than this

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def duration(p: Path) -> float:
    out = subprocess.run([FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
                          "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(p)], capture_output=True, text=True)
    return float(out.stdout.strip())


def pauses(p: Path, floor="-34dB", least=0.16) -> list[float]:
    """Midpoints of every detectable pause inside one narration clip."""
    out = subprocess.run(
        [FFMPEG, "-i", str(p), "-af", f"silencedetect=n={floor}:d={least}",
         "-f", "null", "-"], capture_output=True, text=True).stderr
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", out)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", out)]
    return [(s + e) / 2 for s, e in zip(starts, ends)]


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.?!])\s+", text.strip())
    return [p for p in parts if p]


def chunk(sent: str) -> list[str]:
    """One sentence into cue-sized pieces of at most MAX_LINES rendered lines."""
    budget = MAX_CHARS * MAX_LINES
    if len(sent) <= budget:
        return [sent]
    words, out, cur = sent.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > budget:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def wrap(s: str) -> str:
    """Balance a cue over at most MAX_LINES lines."""
    if len(s) <= MAX_CHARS:
        return s
    words, lines, cur = s.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > MAX_CHARS:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    while len(lines) > MAX_LINES:                 # last resort, never drop words
        lines[-2] = lines[-2] + " " + lines[-1]
        lines.pop()
    return "\n".join(lines)


def pack(text: str) -> list[str]:
    """Sentences greedily packed into cue-sized pieces.

    Splitting strictly per sentence gave "Correctly." its own 0.5s cue and left
    "of these." on screen for 0.35s - unreadable. Short sentences ride along
    with their neighbour instead, which is how subtitles are normally cut.
    """
    budget = MAX_CHARS * MAX_LINES
    out: list[str] = []
    for sent in sentences(text):
        for piece in chunk(sent):
            if out and len(out[-1]) + 1 + len(piece) <= budget:
                out[-1] = out[-1] + " " + piece
            else:
                out.append(piece)
    return out


def cues_for(text: str, t0: float, span: float, breaks: list[float]):
    """Cue boundaries proportional to length, then snapped to real pauses."""
    pieces = pack(text)
    if len(pieces) == 1:
        return [(t0, t0 + span, wrap(pieces[0]))]

    total = sum(len(p) for p in pieces)
    edges, run = [], 0.0
    for p in pieces[:-1]:
        run += len(p) / total * span
        edges.append(run)

    snapped = []
    for e in edges:
        near = [b for b in breaks if abs(b - e) <= SNAP_WINDOW]
        snapped.append(min(near, key=lambda b: abs(b - e)) if near else e)

    # No cue may be shorter than MIN_CUE. Push boundaries later where they are
    # too close to the previous one, then pull them earlier where that would
    # crowd the end. Both passes are needed: snapping can bunch boundaries at
    # either end of the clip.
    n = len(snapped)
    for i in range(n):
        snapped[i] = max(snapped[i], (snapped[i - 1] if i else 0.0) + MIN_CUE)
    for i in range(n - 1, -1, -1):
        ceiling = (snapped[i + 1] if i + 1 < n else span) - MIN_CUE
        snapped[i] = min(snapped[i], ceiling)
    if any(b <= 0 for b in snapped) or snapped != sorted(snapped):
        snapped = edges              # clip too short to honour the minimum

    bounds = [0.0] + snapped + [span]
    return [(t0 + bounds[i], t0 + bounds[i + 1], wrap(pieces[i]))
            for i in range(len(pieces))]


def ts_srt(t: float) -> str:
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02}:{int(m):02}:{int(s):02},{int(round(s % 1 * 1000)):03}"


def ts_ass(t: float) -> str:
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02}:{int(s):02}.{int(s % 1 * 100):02}"


# BorderStyle 1 outlines the glyphs. The opaque-box style (3) drew one box per
# rendered line, so a two-line cue came out as two ragged rectangles of
# different widths - scrappy against otherwise clean slides. A heavy outline
# plus shadow stays readable over the terminal frames, which are the only ones
# with content down near the caption.
# Colours are ASS &HAABBGGRR, i.e. byte-reversed from the CSS in the frames.
ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Segoe UI,46,&H00F0EAE7,&H00F0EAE7,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,3.4,1.4,2,160,160,56,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def main():
    narration = json.loads((FRAMES / "narration.json").read_text(encoding="utf-8"))
    all_cues, t = [], 0.0
    for n in narration:
        wav = AUDIO / f"{n['name']}.wav"
        if not wav.exists():
            print(f"  missing audio for {n['name']} - run demo/make_video.py first")
            return 1
        spoken = duration(wav)
        all_cues += cues_for(n["text"], t, spoken, pauses(wav))
        t += spoken + n.get("pad", 0.5)          # pad is silence, never captioned

    srt = []
    for i, (a, b, txt) in enumerate(all_cues, 1):
        srt.append(f"{i}\n{ts_srt(a)} --> {ts_srt(b)}\n{txt}\n")
    (DEMO / "ring-sentinel-pitch.srt").write_text("\n".join(srt), encoding="utf-8")

    ass = [ASS_HEAD]
    for a, b, txt in all_cues:
        ass.append(f"Dialogue: 0,{ts_ass(a)},{ts_ass(b)},Sub,,0,0,0,,"
                   + txt.replace("\n", "\\N"))
    (DEMO / "ring-sentinel-pitch.ass").write_text("\n".join(ass), encoding="utf-8")

    longest = max(all_cues, key=lambda c: c[1] - c[0])
    fastest = min(all_cues, key=lambda c: (c[1] - c[0]) / max(len(c[2]), 1))
    print(f"  {len(all_cues)} cues over {t:.0f}s")
    print(f"  longest  {longest[1]-longest[0]:.1f}s  {longest[2][:46]!r}")
    print(f"  densest  {len(fastest[2])/(fastest[1]-fastest[0]):.1f} chars/sec  "
          f"{fastest[2][:46]!r}")
    over = [c for c in all_cues if len(c[2]) / (c[1] - c[0]) > 22]
    if over:
        print(f"  {len(over)} cue(s) above 22 chars/sec - too fast to read:")
        for c in over[:4]:
            print(f"    {c[2][:60]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
