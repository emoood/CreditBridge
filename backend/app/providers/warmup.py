"""Preload the local model and keep it resident: one load request, then one tiny chat call so the first real call is not slow.
Local only (the configured Ollama URL). Never raises: a failed warm-up is reported, not fatal."""
import time
from typing import Any

import httpx

from ..config import Settings


async def warm_up(s: Settings) -> dict[str, Any]:
    if s.llm_default_route == "gemini":
        return {"ok": True, "skipped": "Gemini cloud model does not require local preloading"}
    if s.llm_default_route == "lmstudio":
        return {"ok": True, "skipped": "LM Studio manages its own model loading"}
    url, model = s.route(s.llm_default_route)
    t0 = time.time()
    out: dict[str, Any] = {"ok": False, "model": model, "think": s.llm_think}
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{url.rstrip('/')}/api/generate", json={"model": model, "keep_alive": s.ollama_keep_alive, "prompt": ""})  # load only
            r.raise_for_status()
            out["load_s"] = round(time.time() - t0, 1)
            t1 = time.time()
            body: dict[str, Any] = {"model": model, "stream": False, "keep_alive": s.ollama_keep_alive, "think": s.llm_think,
                                    "messages": [{"role": "user", "content": "Reply with the single word: ready"}], "options": {"temperature": 0, "num_ctx": s.llm_context_tokens}}
            d = (await (c.post(f"{url.rstrip('/')}/api/chat", json=body))).json()
            out.update({"warm_call_s": round(time.time() - t1, 1), "reply": d["message"].get("content", "")[:40], "thinking_chars": len(d["message"].get("thinking", "") or ""),
                        "output_tokens": d.get("eval_count"), "ok": True})
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out
