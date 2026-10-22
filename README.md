# CreditBridge: Setup

AI-assisted transfer-credit evaluation demo. **All data is synthetic.** Full spec: `CREDITBRIDGE_CLAUDE_BUILD_PLAN.md`.

## 1. Install (macOS)

```bash
brew install python node tesseract tesseract-lang ollama
ollama pull qwen3.5:4b          # ~4 GB
```

Needs: Python 3.11+, Node 20+, git.

## 2. Get the code

```bash
git clone https://github.com/emoood/CreditBridge.git
cd CreditBridge
```

## 3. Backend

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Edit `.env` and set `DATABASE_URL` (get it privately from the team; **never commit `.env`**):

```
DATABASE_URL=postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Use the Supabase **Session pooler** string (port 5432), with `postgresql+psycopg://` at the start.

## 4. Run

Three terminals:

```bash
# 1. Model server
OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_CONTEXT_LENGTH=8192 ollama serve

# 2. Backend (from repo root, venv active)
uvicorn backend.app.main:app --reload --port 8000

# 3. Frontend
cd frontend && npm install && npm run dev      # http://localhost:5173
```

## 5. Check it works

```bash
curl localhost:8000/api/v1/health      # provider ok: true
pytest -q                              # tests need no Ollama, DB or Tesseract
ruff check .
```

Live AI check (uses the real model, ~3 min): `python benchmarks/live_check.py`

## Useful

| Need | Command |
|---|---|
| See loaded model | `ollama ps` |
| Live AI events | `curl -N localhost:8000/api/v1/dev/stream` |
| Regenerate fixtures | `python fixtures/generate_demo_v1.py` (macOS fonts) |
| No Ollama / slow machine | Run `pytest` only (uses the fake provider) |
| Arabic OCR languages | `tesseract --list-langs` should list `ara` and `eng` |

## Troubleshooting

- **`failed to resolve host`:** you used the Direct connection string. Use the Session pooler one.
- **Health shows `model not pulled`:** run `ollama pull qwen3.5:4b`.
- **Health shows `unreachable`:** `ollama serve` is not running.
- **Windows/Linux:** install Ollama and Tesseract (with Arabic data) from their sites; behavior of the model may differ from the Mac results in `docs/decisions.md`.

## Rules

- The AI recommends only; humans approve. No LLM in the rules engine.
- Synthetic data only. No real student data. No secrets in git.
