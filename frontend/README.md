# BriefForge frontend

Chinese single-user research workspace built with React, TypeScript and Vite. The four views share persisted project, source, run and immutable report data from the API. No research results are embedded in the frontend.

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm build
pnpm test
```

Development uses port 5173 and proxies `/api` to `http://127.0.0.1:8788`. The backend serves `dist/` in production. `pnpm-workspace.yaml` allows only esbuild's dependency installation script. Optional Google Fonts gracefully fall back to system fonts when offline.

With the API and worker running, install Chromium with `pnpm exec playwright install chromium` if needed, then run:

```sh
pnpm test:e2e
node e2e/interactions.mjs
```

Set `BRIEFFORGE_URL` to test another local deployment. These tests create clearly labelled demo/test workspaces, use fixed-response replay only, and write screenshots/results/video under `artifacts/frontend/`. They do not invoke paid models. The smoke test covers demo creation, brief editing, outline approval, run execution, exact evidence quotation, persistent history, revision, both exports and mobile layout. The interaction test additionally covers real keyboard input, new public workspace creation, file upload and source version replacement/history.

The SSE stream is reconciled with the persistent event-log endpoint. A polling path updates task/run state when SSE is interrupted. Exact quote highlighting reads the frozen source snapshot associated with the selected report. Unknown source dates and unsafe external link schemes are handled without trusting source-provided metadata.

The report and collaboration views also read `/api/runs/{id}/collaboration`. The report card is always bound to its frozen report's run, independently of the run selected elsewhere. It displays persisted before/after decisions, actual task attribution and followups, historical source links, measured research-task intervals, reuse counts, and costs. Missing history stays unknown; deterministic normalization is labelled automatic checking; neither corrections nor parallel overlap is presented as proof of superiority or saved time.

New research explicitly selects `gemini-budget` by default (tested project profile). `qwen-default` remains an explicit experimental option. Existing run/report profiles and revision behavior are preserved. To verify this UI against an existing completed workspace without submitting research or paid requests:

```sh
BRIEFFORGE_PROJECT=<existing-project-id> node e2e/collaboration.mjs
```

This test reads real collaboration records and source snapshots, opens and closes the outline without starting research, verifies report-version isolation and mobile layout, and injects one client-only 503 response to verify the history-retry state. Results are saved under `artifacts/frontend/collaboration/`.
