"""Pytest configuration: make the project root importable as ``src``.

Placing this conftest at the project root ensures ``roofing-cost-model/`` is on
sys.path so tests can ``import src.*`` regardless of the invocation directory.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
