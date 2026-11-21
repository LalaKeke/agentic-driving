"""Resolve external CorDriver dependencies relative to the checkout or explicit environment."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(os.environ.get("CORDRIVER_TOOLS", ROOT / "runs/tools")).expanduser().resolve()
CHECK_PYTHON = Path(os.environ.get("CORDRIVER_CHECK_PYTHON", ROOT / ".venv/bin/python")).expanduser().absolute()
CONFIG = Path(__file__).resolve().parent / "cordriver_config"
