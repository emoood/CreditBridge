# CreditBridge: Google Gemini Provider & Multimodal Vision Integration

This document outlines the architecture, implementation details, and configuration for the Google Gemini integration in CreditBridge.

---

## 1. Overview

CreditBridge has been enhanced to support **Google Gemini** as a first-class remote LLM provider alongside the existing local Ollama and OpenAI-compatible routes. This upgrade replaces local Qwen models with cloud-based Gemini models, providing higher extraction accuracy, native multimodal vision OCR for transcript reading, and structured tool calling.

---

## 2. Key Components Added & Modified

### 2.1 Gemini Provider (`backend/app/providers/gemini_provider.py`)
- **Official SDK Integration**: Built using the modern Google GenAI SDK (`google-genai`).
- **Schema Sanitization (`clean_schema_for_gemini`)**:
  - Inlines `$defs` and `$ref` references recursively.
  - Strips unsupported JSON schema properties (`$schema`, `additionalProperties`, `title`) that cause Gemini API schema validation failures.
- **Tool Calling Support**:
  - Translates OpenAI-format tool definitions into `google.genai.types.Tool` and `FunctionDeclaration`.
  - Reconstructs model `function_call` invocations and matches them with `function_response` tool parts.
- **Thought Signature Preservation**:
  - Caches Assistant model contents to preserve mandatory Gemini thought signatures across multi-turn tool loops.
- **Model Fallback & Health Checks**:
  - Automated fallback between configured models (e.g. `gemini-3.5-flash-lite`, `gemini-3.5-flash`, `gemini-3.8-flash`).
  - Robust health status reporting through the `/api/v1/health` endpoint.

### 2.2 Multimodal Vision OCR Support
- **Inline Image Processing**:
  - `GeminiProvider._build_contents()` decodes base64-encoded cropped PNG blocks sent by `vision_reader.py` into native image parts:
    ```python
    types.Part.from_bytes(data=img_bytes, mime_type="image/png")
    ```
  - Allows Gemini to directly inspect high-resolution transcript crops, accurately transcribing course titles, grades, and credit counts with zero hallucinated entries.

### 2.3 Adaptive Rate Limiting (`backend/app/providers/rate_limiter.py`)
- **Sliding-Window RPM Enforcement**:
  - Tracks request timestamps over a 60-second sliding window to strictly adhere to Google AI Studio quotas.
  - Automatically paces requests with uniform interval spacing (`60.0 / rpm`).
- **Dynamic 429 Retry Backoff**:
  - Inspects Google API 429 error messages for `retryDelay` recommendations and pauses execution until quota resets.

### 2.4 Database & Catalog Resilience
- **Dual-Database Architecture**:
  - **Curriculum Database**: Direct PostgreSQL connection to Supabase holding real course curriculum data.
  - **App State**: Added SQLite fallback (`data/creditbridge.db`) in `backend/app/db/session.py` when a local PostgreSQL server is not running.
- **Official AUD Catalog (`aud_2026_2027`)**:
  - Integrated the complete 601-course AUD Undergraduate Catalog (`catalog/aud_undergrad_2026_2027/courses.json`) to prevent false-positive "course not in catalog" policy errors.

### 2.5 Tests & Diagnostic Tooling
- **Unit Tests (`backend/tests/unit/test_gemini_provider.py`)**:
  - Validates schema sanitization, tool conversion, message serialization, and multimodal image handling.
- **Preflight Verification (`scripts/preflight.py`)**:
  - Validates database connectivity, Gemini model readiness, catalog availability, and upload folder permissions.
- **Benchmarks**:
  - `benchmarks/live_check.py` and `benchmarks/model_eval.py` updated to benchmark Gemini accuracy and response latency.

---

## 3. Configuration Reference (`.env`)

Add the following environment variables to your `.env` file:

```env
# LLM Provider Configuration
LLM_DEFAULT_ROUTE=gemini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_RPM=15

# Catalog Configuration
CATALOG=aud_2026_2027

# Database Configuration
DATABASE_URL=postgresql+psycopg://postgres:<PASSWORD>@db.<PROJECT_REF>.supabase.co:5432/postgres
APP_DATABASE_URL=sqlite:///data/creditbridge.db
```

### Free Tier Limits Reference:
- **`gemini-3.5-flash-lite`** (Recommended): 15 RPM, 500 RPD, 250,000 TPM
- **`gemini-3.5-flash` / `gemini-3.8-flash`**: 5 RPM, 20 RPD

---

## 4. Verification & Testing

### Run Preflight Checks:
```bash
python scripts/preflight.py
```

### Run Unit Tests:
```bash
pytest backend/tests/unit/test_gemini_provider.py
```

### Start the Application Stack:
1. **Backend**:
   ```bash
   uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
2. **Frontend**:
   ```bash
   cd frontend
   npm run dev
   ```

---

## 5. UI Operational Modes

- **Demo Mode (`Alt+D` / Toggle ON)**: Plays back deterministic pre-recorded runs instantly without consuming API rate limits. Ideal for demonstrations and UI validation.
- **Live Mode (`Alt+D` / Toggle OFF)**: Runs real-time multimodal OCR via Gemini 3.5 Flash Lite with rate-limiting safeguards, extracting course rows directly from uploaded scans and digital PDFs.
