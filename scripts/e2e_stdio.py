#!/usr/bin/env python3
"""Local operator entrypoint; credentials are never command-line arguments."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from timetree_mcp.e2e_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
