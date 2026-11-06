#!/usr/bin/env python3
"""Entry point for the Android Deep Clean skill.

Examples:
    python scripts/adc.py doctor --state .adc-state
    python scripts/adc.py scan --state .adc-state
    python scripts/adc.py analyze --state .adc-state

The CLI is intentionally conservative: scan/analyze are read-only, execute
requires a token created by select, and quarantine is the default.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from adc_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
