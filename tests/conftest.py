"""Shared pytest configuration for the RPP test suite.

Two pieces of setup must happen before any test module is imported:

1. Put the project root on ``sys.path`` so tests can ``import src`` / ``import
   api`` / ``import pages`` regardless of the directory pytest is invoked from.
   Each test module used to repeat this boilerplate itself, and some imported
   project modules *before* doing so — which only worked because pytest was
   launched from the repository root.
2. Force a non-interactive matplotlib backend. The default backend on a
   workstation is a GUI one (``tkagg`` on Windows); drawing a figure then tries
   to open a window, which intermittently fails and makes otherwise
   deterministic tests flaky. ``Agg`` renders to memory only.

``conftest.py`` is imported ahead of every test module, which is why both live
here rather than in the individual test files.
"""

import sys
from pathlib import Path

import matplotlib

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

matplotlib.use("Agg", force=True)
