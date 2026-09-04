"""Render the whole pitch video: frames, narration, and the final mux.

    python demo/build_frames.py     # first - writes frames + narration.json
    python demo/make_video.py       # this - renders, narrates, stitches

    python demo/make_video.py --skip-tts    # silent cut, ~1 minute
    python demo/make_video.py --only-mux    # re-stitch existing assets

Design decisions worth stating:

  * Frames are RENDERED, not screen-recorded. A desktop capture would include
    whatever else is on the desktop, cannot be re-cut without re-performing it,
    and cannot be reviewed frame by frame. These can.
  * Chrome is invoked ONE AT A TIME with its own profile directory. Running
    several headless instances concurrently silently produced no output - they
    collide over the default profile, and Chrome reports success anyway.
  * Every frame is verified to exist and be non-trivial in size before the mux
    runs. A missing frame becomes a hard failure here rather than a black gap
    in the finished video.
  * Cuts are hard cuts. No crossfades, no motion. Nothing that can render
    differently on someone else's player.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
FRAMES = DEMO / "frames"
AUDIO = DEMO / "audio"
OUT = DEMO / "ring-sentinel-pitch.mp4"

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
FFMPEG = shutil.which("ffmpeg") or str(
    Path.home() / "AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def file_url(p: Path) -> str:
    return "file:///" + str(p).replace("\\", "/").replace(" ", "%20")


def shoot(url: str, out: Path, idx: int, budget=2500) -> bool:
    """One headless screenshot. Serial, own profile, verified afterwards."""
    profile = Path(os.environ.get("TEMP", "/tmp")) / f"rs_chrome_{idx}"
    subprocess.run([str(CHROME), "--headless=new", "--disable-gpu",
                    "--hide-scrollbars", "--force-device-scale-factor=1",
                    "--window-size=1920,1080", f"--virtual-time-budget={budget}",
                    f"--user-data-dir={profile}", f"--screenshot={out}", url],
                   capture_output=True, timeout=120)
    ok = out.exists() and out.stat().st_size > 5000
    print(f"    {'ok  ' if ok else 'FAIL'} {out.name}"
          f"{'' if ok else '   <-- will abort before mux'}")
    return ok


def render_frames(narration) -> bool:
    print("  rendering frames (serial - concurrent Chrome silently fails)")
    good = True
    for i, n in enumerate(narration):
        png = FRAMES / f"{n['name']}.png"
        if (DEMO / "clips" / f"{n['name']}.mp4").exists():
            print(f"    clip {n['name']} (animated, nothing to screenshot)")
            continue
        if n.get("ring_step") is not None:
            url = file_url(DEMO / "ring.html") + f"?video=1&step={n['ring_step']}"
            good &= shoot(url, png, i, budget=3500)
        else:
            good &= shoot(file_url(FRAMES / f"{n['name']}.html"), png, i)
    return good


# Deliberately NOT retiming the narration.
#
# An earlier version stretched every clip to a fixed 148 words per minute with
# atempo. Measured against a reference video it looked right, and it sounded
# wrong: normalising per line means one clip gets stretched 32% and the next
# barely at all, so the delivery lurches between drawled and normal. Uneven
# pacing is more distracting than fast pacing. The model's natural rhythm is
# left alone; length is controlled by writing fewer words instead, which is
# what actually made this cut easier to follow.
#
# If the whole thing ever needs slowing, apply ONE factor to every clip - never
# a per-clip target.


def narrate(narration) -> bool:
    AUDIO.mkdir(exist_ok=True)
    print("  loading chatterbox (cached after first run) ...")
    import torch
    import torchaudio as ta
    from chatterbox.tts import ChatterboxTTS

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = ChatterboxTTS.from_pretrained(device=dev)
    print(f"  narrating {len(narration)} lines on {dev}")
    # A TTS model can truncate a line or loop a phrase, and neither is audible
    # from a file listing - it just ships as a glitch. Words-per-second catches
    # both: a truncation reads far too fast, a loop far too slow.
    LO, HI, TRIES = 1.9, 4.3, 3
    suspect = []
    for n in narration:
        wav_path = AUDIO / f"{n['name']}.wav"
        # Cache on the TEXT, not just the filename. Reusing a clip whose script
        # has since been edited is silent and invisible: the video builds fine
        # and the voice says the old sentence.
        said = AUDIO / f"{n['name']}.txt"
        if wav_path.exists() and said.exists() and \
                said.read_text(encoding="utf-8") == n["text"]:
            print(f"    skip {n['name']} (unchanged)")
            continue
        if wav_path.exists():
            print(f"    redo {n['name']} (script changed)")
        want = len(n["text"].split())
        best = None
        for attempt in range(TRIES):
            # cfg_weight low = slower, more deliberate delivery. The default
            # races, and this is a technical pitch, not an advert.
            wav = model.generate(n["text"], exaggeration=0.35, cfg_weight=0.3)
            dur = wav.shape[-1] / model.sr
            wps = want / dur if dur else 99
            if best is None or abs(wps - 2.9) < abs(best[1] - 2.9):
                best = (wav, wps, dur)
            if LO <= wps <= HI:
                break
            print(f"    retry {n['name']} - {wps:.2f} words/sec on attempt {attempt+1}")
        wav, raw_wps, dur = best
        ta.save(str(wav_path), wav, model.sr)
        said.write_text(n["text"], encoding="utf-8")
        # Judge the GENERATED pace, not the retimed one: retiming pulls every
        # clip into the sane band by construction, which would hide exactly the
        # truncation this check exists to catch.
        wps = raw_wps
        flag = "" if LO <= wps <= HI else "   <-- CHECK THIS LINE"
        if flag:
            suspect.append(n["name"])
        print(f"    ok  {n['name']}  {dur:5.1f}s  {wps:.2f} w/s{flag}")
    if suspect:
        print(f"  {len(suspect)} line(s) outside the sane pace band: {suspect}")
    return True


def wav_seconds(p: Path) -> float:
    """ffprobe, not the wave module: torchaudio writes IEEE-float WAV
    (format tag 3) and stdlib wave raises 'unknown format: 3' on it."""
    out = subprocess.run([FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
                          "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(p)], capture_output=True, text=True)
    return float(out.stdout.strip())


VIDEO_ARGS = ["-vf", "fps=30,format=yuv420p,scale=1920:1080", "-c:v", "libx264",
              "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]


def segment(beat, hold: float, out: Path):
    """One beat as a video of exactly `hold` seconds.

    Every beat becomes a segment with identical encoding parameters, so the
    final concat is a stream copy and cannot re-encode differently per beat.
    A still is looped; a manim clip plays and then freezes on its last frame,
    which is why each scene is authored SHORTER than its narration.
    """
    clip = DEMO / "clips" / f"{beat['name']}.mp4"
    if clip.exists():
        subprocess.run([FFMPEG, "-y", "-i", str(clip), "-vf",
                        f"tpad=stop_mode=clone:stop_duration={hold + 2},"
                        f"fps=30,format=yuv420p,scale=1920:1080",
                        "-t", f"{hold}", "-c:v", "libx264", "-preset", "medium",
                        "-crf", "20", "-pix_fmt", "yuv420p", str(out)],
                       capture_output=True, check=True)
        return "clip"
    png = FRAMES / f"{beat['name']}.png"
    subprocess.run([FFMPEG, "-y", "-loop", "1", "-i", str(png), "-t", f"{hold}"]
                   + VIDEO_ARGS + [str(out)], capture_output=True, check=True)
    return "still"


def mux(narration, silent: bool) -> bool:
    missing = [n["name"] for n in narration
               if not (FRAMES / f"{n['name']}.png").exists()
               and not (DEMO / "clips" / f"{n['name']}.mp4").exists()]
    if missing:
        print(f"  ABORT - {len(missing)} beat(s) have neither a frame nor a clip: "
              f"{missing[:4]}")
        return False

    holds = []
    for n in narration:
        w = AUDIO / f"{n['name']}.wav"
        d = (wav_seconds(w) + n.get("pad", 0.5)) if (w.exists() and not silent)             else (len(n["text"].split()) / 2.9 + n.get("pad", 0.5))
        holds.append(round(d, 3))

    work = DEMO / "_segments"
    work.mkdir(exist_ok=True)
    print("  building segments")
    parts = []
    for i, (n, d) in enumerate(zip(narration, holds)):
        out = work / f"{i:02d}_{n['name']}.mp4"
        kind = segment(n, d, out)
        parts.append(out)
        print(f"    {kind:5} {n['name']:13} {d:6.2f}s")

    concat = DEMO / "_frames.txt"
    concat.write_text(
        chr(10).join("file '" + str(p).replace(chr(92), "/") + "'" for p in parts),
        encoding="utf-8")
    silent_video = DEMO / "_video.mp4"
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-c", "copy", str(silent_video)], capture_output=True, check=True)

    if silent:
        shutil.move(str(silent_video), str(OUT))
    else:
        alist = DEMO / "_audio.txt"
        voice = DEMO / "_voice.wav"
        # Re-pad each clip to its hold time so audio and picture stay locked.
        pieces = []
        for n, d in zip(narration, holds):
            padded = AUDIO / f"_pad_{n['name']}.wav"
            subprocess.run([FFMPEG, "-y", "-i", str(AUDIO / f"{n['name']}.wav"),
                            "-af", f"apad=whole_dur={d}", "-ar", "44100", "-ac", "2",
                            str(padded)], capture_output=True, check=True)
            pieces.append(padded)
        alist.write_text(
            chr(10).join("file '" + str(p).replace(chr(92), "/") + "'" for p in pieces),
            encoding="utf-8")
        subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i",
                        str(alist), "-c", "copy", str(voice)],
                       capture_output=True, check=True)
        # Raw narration measured -17.9 LUFS, about 4 dB under what web players
        # expect, which plays back noticeably quiet. loudnorm brings it to the
        # -14 LUFS norm with true peak held below -1.5 dBTP so nothing clips.
        subprocess.run([FFMPEG, "-y", "-i", str(silent_video), "-i", str(voice),
                        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-shortest", str(OUT)], capture_output=True, check=True)
        for f in pieces + [silent_video, voice, alist]:
            f.unlink(missing_ok=True)
    concat.unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tts", action="store_true")
    ap.add_argument("--only-mux", action="store_true")
    ap.add_argument("--no-subs", action="store_true",
                    help="leave the picture clean; still writes the .srt")
    args = ap.parse_args()

    narration = json.loads((FRAMES / "narration.json").read_text(encoding="utf-8"))
    print(f"  {len(narration)} frames planned\n")

    if not args.only_mux:
        if not render_frames(narration):
            print("\n  Some frames failed to render. Fix before muxing.")
            return 1
        if not args.skip_tts:
            narrate(narration)

    if not mux(narration, silent=args.skip_tts):
        return 1

    # Captions ship as a SIDECAR .srt only - the video carries no subtitle
    # track at all.
    #
    # Burned-in captions could not be turned off, and they landed on top of the
    # animated slides' own on-screen labels. Embedding them as a soft track was
    # the obvious fix, but the MP4 muxer re-asserts the "default" disposition on
    # the subtitle track no matter how it is cleared (-disposition none, "0",
    # -default_mode infer_no_subs all leave default=1), so players still
    # auto-displayed it. A track that cannot be reliably switched off is worse
    # than no track: anyone who wants captions can load the .srt beside the file.
    if not args.skip_tts:
        import subtitles
        if subtitles.main() == 0:
            print(f"  subtitles written to {DEMO / 'ring-sentinel-pitch.srt'}")
            print("  the picture itself carries no captions")

    probe = subprocess.run([FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
                            "-show_entries", "format=duration,size",
                            "-show_entries", "stream=codec_type,width,height",
                            "-of", "default=nw=1", str(OUT)],
                           capture_output=True, text=True)
    print(f"\n  {OUT}")
    for line in probe.stdout.strip().split("\n"):
        print(f"    {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
