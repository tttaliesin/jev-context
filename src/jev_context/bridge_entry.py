"""Absolute, isolated-mode launch target for exported Workroom descriptors."""

from __future__ import annotations

import sys
from pathlib import Path

# -I ignores caller PYTHONPATH and the working directory. Load only our installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev_context.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "serve", "--bridge-only", *sys.argv[1:]]
    main()
