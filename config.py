"""Paths and the Claude CLI, shared by every script.

HN_DATA_DIR and HN_PROFILE_DIR move the (gitignored) data and profile folders.
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
DATA = Path(os.environ.get("HN_DATA_DIR", ROOT / "data"))
PROFILE = Path(os.environ.get("HN_PROFILE_DIR", ROOT / "profile"))
THREADS = DATA / "threads.json"
# Resolve the full path so Windows' claude.cmd works without a shell.
CLAUDE = shutil.which("claude") or "claude"
