"""Vercel serverless entrypoint (WSGI)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force serverless upload path before app import
os.environ.setdefault("VERCEL", "1")

from app import app  # WSGI callable
