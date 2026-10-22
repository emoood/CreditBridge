"""Preload the local model and send one warm-up call (run before a demo). Prints what happened; exits non-zero if it failed."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.config import get_settings
from backend.app.providers.warmup import warm_up

r = asyncio.run(warm_up(get_settings()))
print(json.dumps(r, indent=1))
sys.exit(0 if r.get("ok") else 1)
