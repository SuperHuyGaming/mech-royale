"""
Check runner for GMUNVIS Mech Royale Bot.
Executes the official conformance test suite against bot.py,
validating 300/300 observations answered in time with <10ms latency.
"""
from __future__ import annotations
import os
import subprocess
import sys


def run_check():
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    bot_path = os.path.join(repo_dir, "bot.py")
    if not os.path.exists(bot_path):
        bot_path = os.path.join(repo_dir, "legacy", "bot.py")

    # Locate official check.py and arena directory
    candidates_check = [
        os.path.join(repo_dir, "mechfight-kit", "check", "check.py"),
        os.path.join(repo_dir, "..", "bt-mech", "mech", "mechfight-kit", "check", "check.py"),
        os.path.join(repo_dir, "..", "mechfight-kit", "check", "check.py"),
        "D:/GMU Fall 2026/bt-mech/mech/mechfight-kit/check/check.py",
    ]
    check_script = None
    for c in candidates_check:
        if os.path.exists(c):
            check_script = os.path.abspath(c)
            break

    if not check_script:
        print("ERROR: Official check.py not found in candidate paths.")
        sys.exit(1)

    candidates_arena = [
        os.path.join(repo_dir, "mechfight-kit", "arena"),
        os.path.join(repo_dir, "..", "bt-mech", "mech", "mechfight-kit", "arena"),
        os.path.join(repo_dir, "..", "arena"),
        "D:/GMU Fall 2026/bt-mech/mech/mechfight-kit/arena",
    ]
    arena_dir = None
    for a in candidates_arena:
        if os.path.exists(a):
            arena_dir = os.path.abspath(a)
            break

    cmd = [sys.executable, check_script]
    if arena_dir:
        cmd.extend(["--map-dir", arena_dir])
    cmd.extend(["--", sys.executable, "-u", bot_path])

    print(f"Running conformance check: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    run_check()
