# CreditBridge Dashboard — Frontend UI

A responsive React + TypeScript + Vite UI prototype for the agreed **CreditBridge operations dashboard** (visual direction C: enterprise UI with understated AI elements).

## Run locally

```bash
npm install
npm run dev
```

Open the URL shown by Vite (usually `http://localhost:5173`). Requires Node.js 20+.

## Add to an existing VS Code project

- If your project does not yet have a frontend, copy this entire folder into `frontend/` and run the commands above from there.
- If your project already has a React frontend, copy `src/App.tsx`, `src/data.ts` and `src/styles.css` into it, add `lucide-react`, and adapt the entrypoint and routes.
- Do not replace your existing backend or database folders.

## What works

- Sidebar page navigation and responsive mobile menu
- Application search and status filters
- Clickable priority queue, recent applications, and application detail drawer
- Notifications popover
- New Evaluation entrypoint modal
- Status indicators and AI activity timeline

## What is intentionally demo-only

All names, figures, statuses, and agent events are **synthetic illustrative data** from `src/data.ts`. Nothing is written to a database. The New Evaluation flow, Course Library, University Setup, Approvals persistence, and Reports need connections to the actual application logic. Do not claim this frontend implements academic evaluations.

## Integration guide for Claude Code

1. Keep layout and CSS variables as the visual baseline.
2. Replace `src/data.ts` with typed FastAPI API requests, preserving shapes and loading/error/empty states.
3. Wire `New Evaluation` to the transcript + syllabus upload wizard.
4. Wire application selection to the real evaluation detail and comparison screens.
5. Stream agent progress from backend SSE events into the activity timeline (do not display private chain-of-thought).
6. Bind university/account display to authenticated user context and enforce backend role permissions.
7. Ensure status metrics are computed from real records; never ship the sample values as live data.
8. For offline demo, serve the same UI against local FastAPI and local PostgreSQL.

## Colors

- Navy sidebar: `#142239`
- Primary blue: `#2563eb`
- Canvas: `#f6f8fc`
- Neutral borders: `#e7ecf4`
- Teal success: `#16a17f`
- Amber attention: `#c88a30`

Typography: Plus Jakarta Sans for headings and DM Sans for UI. Font fallback works offline, but for production bundle properly licensed webfonts locally if offline appearance must be identical.
