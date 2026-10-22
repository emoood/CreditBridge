# Build progress

- [x] Step 0: spikes (see decisions.md)
- [x] Step 1: backend skeleton, providers (Ollama, LM Studio, fake), config, dev event stream, fixtures (demo_v1, demo_v2)
- [x] Rules engine, evidence + citation validation, TF-IDF retrieval, real AUD 2026-27 catalog (601 courses, descriptions only)
- [x] Two agents (investigator + adversary) with case file, precedent memory, revision round, triage, JSON-action option
- [x] Hard per-call timeout + one process-wide model lock; keep-alive; redacted logs
- [x] Background processing: local PostgreSQL state, in-process worker, phase 1 reading (vision) -> officer gate -> phase 2 evaluation, resume, recovery
- [x] API <-> UI contract test; UI: upload progress, verification table with cross-check flags, confirm gate, resume, progressive results
- [x] scripts/start.sh + preflight + README test script
- [ ] Recorded real run + replay label (so a live demo never depends on the model)
- [ ] Browser check of the screens (types and build only so far)
- [ ] Tune agent quality on the real catalog (see docs/decisions.md for measured model comparisons)
- [ ] Hand-written ground truth from the admissions officer (past decisions)
