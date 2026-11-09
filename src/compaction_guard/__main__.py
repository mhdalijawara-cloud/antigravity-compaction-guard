"""CLI entry point for python -m compaction_guard."""

from __future__ import annotations

import sys
from .cli import main

if __name__ == "__main__":
    main(sys.argv[1:])
