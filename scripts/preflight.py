"""Fail fast, with a clear message, if anything the app needs is missing. Run by scripts/start.sh. Read-only checks."""
import asyncio
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

from backend.app.config import get_settings
from backend.app.db.session import get_app_engine
from backend.app.providers.router import build_provider

s = get_settings()
problems: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def bad(msg: str, fix: str) -> None:
    print(f"  FAIL  {msg}\n        -> {fix}")
    problems.append(msg)


def scrub(e: Exception) -> str:
    return type(e).__name__ + ": " + str(e).splitlines()[0][:140]


print("CreditBridge preflight")
# 1. this app's own database (jobs, units, decisions)
try:
    eng = get_app_engine()
    with eng.connect() as c:
        c.execute(text("select 1"))
    ok(f"app database reachable ({eng.url.database or 'local'})")
except Exception as e:  # noqa: BLE001
    bad(f"app database not reachable ({scrub(e)})",
        "start Postgres (brew services start postgresql@18) and create the database: createdb creditbridge (or set APP_DATABASE_URL)")

# 2. Model provider
base, model = s.route(s.llm_default_route)
try:
    if s.llm_default_route == "gemini":
        prov = build_provider(s)
        h = asyncio.run(prov.health())
        if h.ok:
            ok(f"Gemini API reachable, model {model} ready ({h.detail})")
        else:
            bad(f"Gemini API error: {h.detail}", "check your GEMINI_API_KEY in .env")
    elif s.llm_default_route == "lmstudio":
        ids = {m["id"] for m in json.load(urllib.request.urlopen(base.rstrip("/") + "/models", timeout=5))["data"]}
        ok(f"LM Studio reachable, model {model} loaded") if model in ids else bad(f"model {model} not listed by LM Studio", "load it in LM Studio")
    else:
        tags = json.load(urllib.request.urlopen(f"{base}/api/tags", timeout=5))
        if model not in {m["name"] for m in tags["models"]}:
            bad(f"model {model} is not pulled", f"ollama pull {model}")
        else:
            req = urllib.request.Request(f"{base}/api/show", json.dumps({"model": model}).encode(), {"Content-Type": "application/json"})
            caps = json.load(urllib.request.urlopen(req, timeout=10)).get("capabilities", [])
            ok(f"Ollama reachable, {model} present, capabilities {caps}")
            for need in ("vision", "tools"):
                if need not in caps:
                    bad(f"{model} lacks the '{need}' capability", "use a model that supports vision and tool calling")
except Exception as e:  # noqa: BLE001
    bad(f"model server not reachable at {base} ({scrub(e)})", "check provider setup and credentials in .env")

# 3. curriculum database (teammate's, read-only)
if not s.database_url:
    bad("DATABASE_URL (curriculum database) is not set", "put the Supabase Session-pooler string in .env as DATABASE_URL=postgresql+psycopg://...")
else:
    try:
        eng = create_engine(s.database_url, connect_args={"connect_timeout": 8})
        with eng.connect() as c:
            n = c.execute(text("select count(*) from curriculum.subjects")).scalar()
        ok(f"curriculum database reachable (Supabase), {n} subjects") if n else bad("curriculum.subjects is empty", "ask the teammate to load the curriculum data")
    except Exception as e:  # noqa: BLE001
        bad(f"curriculum database not reachable ({scrub(e)})", "check DATABASE_URL and your network")

# 4. catalog, folders, frontend deps
cat = {"demo": ROOT / "fixtures/demo_v1/catalog.json", "aud_2026_2027": ROOT / "catalog/aud_undergrad_2026_2027/courses.json"}.get(s.catalog)
ok(f"catalog '{s.catalog}' found") if cat and cat.exists() else bad(f"catalog '{s.catalog}' not found", "set CATALOG=demo or aud_2026_2027, or run scripts/parse_catalog.py")
doc_root = Path(s.document_root)
try:
    doc_root.mkdir(parents=True, exist_ok=True)
    (doc_root / ".w").write_text("x")
    (doc_root / ".w").unlink()
    ok(f"uploads folder writable ({doc_root}, gitignored)")
except OSError as e:
    bad(f"cannot write to {doc_root} ({e})", "check folder permissions")
ok("frontend dependencies installed") if (ROOT / "frontend/node_modules").exists() else bad("frontend dependencies missing", "cd frontend && npm install")

if problems:
    print(f"\n{len(problems)} problem(s). Fix them and run again.")
    sys.exit(1)
print("\nAll checks passed.")
