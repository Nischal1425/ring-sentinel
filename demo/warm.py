"""Run this ONCE before demonstrating the system live. It removes every way a
walkthrough can glitch.

    python demo/warm.py

The pitch video itself is not recorded live - demo/build_frames.py and
demo/make_video.py render it headlessly. This script is for the other case:
driving the system in front of someone, in a terminal.

What goes wrong when you do that, and what this does about it:

  * A cold import pauses for 2-3 seconds mid-sentence. This imports everything
    first, so every command starts instantly.
  * `run.py` on a fresh checkout dies with "No data yet". This regenerates it.
  * Hysteresis means NOTHING is held on the first run, so the action ladder
    shows an empty column. This primes the state so the ladder is populated.
  * The live Razorpay account may be empty or stale. This checks it and tells
    you whether to re-seed BEFORE you start, not during.

It then prints a running order, so you are never typing from memory.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BAR = "-" * 72


def step(label, fn):
    print(f"  {label:44}", end="", flush=True)
    t0 = time.time()
    try:
        detail = fn() or ""
        print(f"ok  {time.time() - t0:5.1f}s  {detail}")
        return True
    except Exception as exc:                      # noqa: BLE001 - report, never crash
        print(f"FAILED  {type(exc).__name__}: {exc}")
        return False


def main():
    print(BAR)
    print("  WARMING THE DEMO - run this once, then start recording")
    print(BAR)

    def gen():
        if not (ROOT / "data" / "merchants.csv").exists():
            subprocess.run([sys.executable, "gen_data.py"], check=True,
                           capture_output=True)
            return "generated"
        return "already present"

    def imports():
        import run, score, evalkit                                   # noqa: F401
        from sentinel import detect, graph, respond, verify          # noqa: F401
        from sentinel.signals import identity, money, structural     # noqa: F401
        from sentinel.signals import tenure, velocity                # noqa: F401
        return "all modules loaded"

    def prime():
        # Two passes: hysteresis needs a ring above threshold on two
        # consecutive runs before a hold appears. One run shows an empty
        # hold column, which looks broken on camera.
        for _ in range(2):
            subprocess.run([sys.executable, "run.py"], check=True,
                           capture_output=True)
        return "report.html + audit trail ready, holds primed"

    def live():
        from sentinel.razorpay import available
        if not available():
            return "SKIPPED - no RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET set"
        from sentinel.razorpay import NotesOnlyClient
        orders = NotesOnlyClient().fetch_orders(count=100)
        tagged = sum(1 for o in orders if (o.get("notes") or {}).get("buyer_id"))
        if tagged < 10:
            return f"only {tagged} usable orders - RUN seed_testmode.py FIRST"
        return f"{len(orders)} orders, {tagged} usable"

    ok = all([
        step("regenerate the book", gen),
        step("warm every import", imports),
        step("prime run.py twice (hysteresis)", prime),
        step("check the live Razorpay account", live),
    ])

    print(f"\n{BAR}")
    print("  RUNNING ORDER. One command per step.")
    print(BAR)
    for step, what, cmd in [
        ("1", "open the case file",        "start report.html"),
        ("2", "the 30-second explainer",   "start demo\\ring.html   (space to advance)"),
        ("3", "held-out results",          "python score.py"),
        ("4", "the decoys",                "  ...same output, scroll to DECOYS"),
        ("5", "what each signal is worth", "python ablate.py"),
        ("6", "live Razorpay API",         "python live.py            (add --annotate to write)"),
        ("7", "the two things that lost",  "python bench_ml.py   /   python bench_elliptic2.py"),
        ("8", "the checks and the ladder", "python test_pipeline.py"),
    ]:
        print(f"  step {step}  {what:26} {cmd}")

    print(f"\n{BAR}")
    if ok:
        print("  Ready. Set your terminal to 18pt or larger before you share the screen.")
    else:
        print("  Something above FAILED. Fix it before you start, not during.")
    print(BAR)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
