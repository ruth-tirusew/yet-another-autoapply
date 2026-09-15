"""Bootstrap hiring-agent vendored modules with correct import paths."""

from __future__ import annotations

import sys
from pathlib import Path

HA_DIR = Path(__file__).resolve().parent
if str(HA_DIR) not in sys.path:
    sys.path.insert(0, str(HA_DIR))
