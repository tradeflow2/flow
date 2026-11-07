"""ASGI entry point: `uvicorn run:app --workers 1` (from the api/ directory; PYTHONPATH must include ../core)."""
import os
import sys

try:
    import tradeflow_core  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

from app.main import create_app
from app.settings import from_env

app = create_app(from_env())
