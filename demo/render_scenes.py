"""Render the manim scenes into demo/clips/, named for the beat they belong to.

    python demo/render_scenes.py              # all scenes
    python demo/render_scenes.py Ratchet      # just one, while iterating

make_video.py picks a clip up automatically when demo/clips/<beat>.mp4 exists,
and falls back to demo/frames/<beat>.png otherwise, so a scene that fails to
render leaves the rest of the video buildable.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
CLIPS = DEMO / "clips"
MEDIA = DEMO / "_manim"

# scene class -> the narration beat it plays under
SCENES = {
    "Blindspot": "05_blindspot",
    "GiantComponent": "40_graph",
    "NullModel": "41_timing",
    "Ratchet": "42_ratchet",
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def render(scene: str, beat: str) -> bool:
    out = subprocess.run(
        [sys.executable, "-m", "manim", "render", "-r", "1920,1080", "--fps", "30",
         "--format=mp4", "--media_dir", str(MEDIA), "-v", "WARNING",
         str(DEMO / "scenes.py"), scene],
        capture_output=True, text=True)
    made = MEDIA / "videos" / "scenes" / "1080p30" / f"{scene}.mp4"
    if out.returncode != 0 or not made.exists():
        print(f"  FAIL  {scene}")
        tail = (out.stderr or out.stdout).strip().splitlines()[-12:]
        for line in tail:
            print(f"        {line}")
        return False
    CLIPS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(made, CLIPS / f"{beat}.mp4")
    probe = subprocess.run(
        [shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of", "csv=p=0", str(made)],
        capture_output=True, text=True).stdout.strip()
    print(f"  ok    {scene:15} -> clips/{beat}.mp4  {float(probe):5.1f}s")
    return True


def main():
    wanted = sys.argv[1:] or list(SCENES)
    unknown = [w for w in wanted if w not in SCENES]
    if unknown:
        print(f"  unknown scene(s): {unknown}. Known: {list(SCENES)}")
        return 1
    if not (DEMO / "frames" / "facts.json").exists():
        print("  no facts.json - run python demo/facts.py first")
        return 1
    ok = all(render(s, SCENES[s]) for s in wanted)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
