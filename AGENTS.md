# AGENTS.md

This file provides guidance to AI agents (like opencode) when working with code in this repository.

## Project Overview

NubrixAI / AnalyticsHubAPI is a FastAPI backend powering an AI-driven analytics platform. Users create projects, load data, blend tables, generate dashboards/reports via natural language, and manage Razorpay-based subscriptions with entitlement and billing enforcement.

Python 3.10 (see `.python-version`). Dependencies are pinned in `pyproject.toml` and `uv.lock`; managed with `uv`.

## Commands

### Local development
```bash
uv sync                                            # install all pinned dependencies
uv run gunicorn main:app --workers=2 --worker-class=uvicorn.workers.UvicornWorker --bind=0.0.0.0:7860
```
`main.py` mounts all routers under `root_path="/api/latest"`. Docs are at `/api/latest/documentation/docs` and `/api/latest/documentation/redoc`.

### Celery worker + beat (background jobs)
```bash
uv run celery -A nubrix.triggers.celery.celeryApp worker --loglevel=info --concurrency=2
uv run celery -A nubrix.triggers.celery.celeryApp beat --loglevel=info
```

### Containerized run (mirrors production)
```bash
docker build -t nubrix-api . && docker run --env-file .env -p 7860:7860 nubrix-api
```
The image runs `startup.sh` → `supervisord` (see `supervisord.conf`), which manages gunicorn (8 workers, `--max-requests=20 --max-requests-jitter=10 --timeout=300`), celery worker, and celery beat under one process tree.

### Tests
There is no automated test suite in the repo. Validate changes by hitting endpoints via the docs UI or `curl`, and by running the celery tasks locally against a Redis/Supabase reachable via `.env`.

### Linting / formatting
No project-enforced linter is wired up. The codebase uses loguru, pydantic v2, FastAPI's dependency injection, and snake_case.

## Architecture

### Entry point — `main.py`
Single FastAPI app. Mounts routers under prefixes: `/auth`, `/projects`, `/loaders`, `/blends`, `/reportingTool`, `/dashboard`, `/utils`, `/subscriptions`, `/webhooks`, `/billing-admin`, `/transformations`. Adds CORS (wildcard), GZip, and a global `HTTPException` handler that unwraps `CustomException` into a flat `{status, message, backendLogMessage}` payload.

### `api/` — HTTP layer
- `routers/` — thin FastAPI routers. Each delegates to a corresponding `api/services/*Service.py` module.
- `services/` — business logic. Heavy services split into subpackages: `services/billing/` (billingEngine, invoiceService, reconciliationService, taxEngine, taxConfigLoader, billingConfig, billingModels), `services/subscriptions/` (subscriptionService, paymentValidationService, subscriptionFieldUtils).
- `commons.py` — Supabase client (`client`), `verifyToken` / `verifyTokenWithExpiry` FastAPI dependencies (JWT decode + cross-check against the `Sessions` table), `updateProjectModifiedAt` helper. **Every service that mutates project data must call `updateProjectModifiedAt(projectId)` at the end.**
- `models.py` — all pydantic request/response models in one place.

### `nubrix/` — domain logic and AI
- `components/` — self-contained LLM-driven units (queryRephraser, codeGenerator, codeDebugger, metadataGenerator, insightGenerator, **insightContextBuilder**, imageToInsights, pdfTableExtractor, speechToText, dashboardNameGenerator, domainKpiMapper, reportGenerator, signalEngine, subscriptionManager, subscriptionStatus, **transformationAgent**, **transformationExecutor**). Each is configured via sections in `config.ini` and prompts in `prompts.yaml`.
- `workflows/reportingToolWorkflow.py` — LangGraph `StateGraph` for the reporting tool: `rephraseQuery → generateCode → runInPythonSandbox → (pass | fail→debugger→debuggerPythonSandbox) → formatJsonResponse`. Conditional edge on whether the rephraser flags a "doubt". `parallelReportingToolWorkflow.py` runs the same pipeline over multiple queries concurrently.
- `triggers/celery.py` — `CeleryWrapper` that registers all tasks (`generateForecasts`, `dailyBilling`, `annualRenewal`, `renewalLifecycle`, `pastDueSuspension`, `entitlementBoundary`, `reconciliation`, `billingMetrics`, `subscriptionExpiry`) and the beat schedule. Tasks live under `triggers/tasks/`. To run a single task on demand (without beat): `uv run celery -A nubrix.triggers.celery.celeryApp call nubrix.triggers.celery.celeryApp.<taskName>`.
- `utils.py` — `readYaml` and `getConfig` helpers.

### `utils/` — shared infrastructure
- `initMethods.py` — `fetch_data(projectId, tableName, baseFilters)` reads parquet from Supabase Storage (`FILE_URL`), caches in Redis for 60s, applies column-level filters (`contains`, `startswith`, `endswith`, `min`, `max`, `isin`, equality). `serializer` handles numpy/pandas/datetime/NaN. **Generated code in the REPL and transformation executor relies on these.**
- `codeExecutor.py` — `REPLManager` runs LLM-generated code with a hard timeout (default 7s) in a thread pool, with `fetch_data`, `serializer`, and restricted `print`. Used by the reporting workflow.
- `exceptionHandler.py` — `CustomException` captures file/line/message; `raiseHttpException` flattens it for the HTTP layer.
- `logger.py` — loguru configured with Logtail handler, stdout (colored), and `logs/runLogs.log` (rotating at 1 MB).
- `llmOutputParser.py`, `webhookExceptions.py` — narrow helpers.

### Transformations feature (newest, see `e599429` and earlier)
Conversational data transformation with SSE streaming:
1. `POST /transformations` (no body) → creates a `transformations` row with an empty JSONB list of `messages`.
2. `POST /transformations/{tid}/messages` streams agent output as SSE (`event: token`, `event: done`). Persistence: appends user + assistant message dicts (carrying `artifact` and `python_code`) to the `messages` array in the `transformations` row.
3. `POST /transformations/{tid}/messages/{mid}` → approve. Runs `transformationExecutor.executeAndPreview`, returns 10-row preview, caches the parquet in Redis for 900s, and updates `artifact.is_approved` in the inline `messages` array.
4. `POST /transformations/{tid}/messages/{mid}/apply` → persist as a project table. Uploads to `AnalyticsHub` storage, refreshes Redis cache, updates `latest_approved_artifact` in the transformation row, sets `is_applied = true` in the message dict in the inline `messages` array, and calls `updateProjectModifiedAt`.

`transformationAgent` maintains per-thread chat history in-memory (lost on restart) keyed by `projectId::transformationId`. Uses Gemini with `with_structured_output(TransformationAgentResponse, method="json_mode")` (the installed LangChain lacks `langchain.agents.create_agent`).

`transformationExecutor` runs generated pandas code in a hardened sandbox:
- Custom `__builtins__` whitelist (no `open`, `eval`, `exec`, etc.).
- `_restricted_import` allows only `pandas`, `numpy`, `datetime`, `math`, `re`.
- 30s timeout via thread pool.
- Output table names validated against `^[A-Za-z][A-Za-z0-9_]*$`.
- Must produce a `final_df` variable.

`transformationService` keeps an `InMemorySaver` per `(projectId, transformationId)` in a process-local registry.

## Configuration and secrets

- `.env` — all secrets and URLs. Required: `SUPABASE_URL`, `SUPABASE_KEY`, `SECRET_KEY`, `REDIS_HOST/PORT/PASSWORD`, `FILE_URL`, plus LLM keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `OPENAI_API_KEY`), `RAZORPAY_KEY_ID/SECRET`, `LOGTAIL_TOKEN/HOST`, LangSmith vars.
- `config.ini` — per-component model/temperature/maxTokens/dpi/concurrency. Sections: `QUERYREPHRASER`, `METADATAGENERATOR`, `CODEGENERATOR`, `CODEDEBUGGER`, `INSIGHTGENERATOR`, `DOMAINKPIMAPPER`, `SPEECHTOTEXT`, `IMAGETOINSIGHTS`, `PDFTABLE`, `DASHBOARDNAMEGENERATOR`, `TRANSFORMATIONAGENT`. Read via `nubrix.utils.getConfig`.
- `prompts.yaml` — system prompts keyed by component. Read via `nubrix.utils.readYaml`.
- `codeTemplates.yaml` — code templates referenced by `codeGenerator`.
- `config/tax_rules.json` — versioned product-tax-code → rule map (intra/inter-state GST split, cess, rounding, effective dates). Loaded by `api/services/billing/taxConfigLoader.py` and applied by `taxEngine.py`.

## Cross-cutting conventions

- **Auth**: every business endpoint takes `token = Depends(verifyToken)` (or `verifyTokenWithExpiry` for the auth verify endpoint). The token must exist in the `Sessions` table and its JWT `email` claim must match the row's `email`. `verifyToken` updates `lastActivity` on success.
- **Error shape**: services raise `CustomException(e, statusCode, uiMessage)`. Routers catch and call `raiseHttpException(e)`. The middleware in `main.py` flattens the resulting `HTTPException` to `{"status", "message"}` (dropping `backendLogMessage`) when those two keys are present in `detail`.
- **Project mutation**: call `api.commons.updateProjectModifiedAt(projectId)` after any service that writes project state. This is the field that powers "last modified" in the UI.
- **Supabase pattern**: `client.table("TableName").select(...).eq(...).limit(1).execute()` and similar. Storage at `client.storage.from_("AnalyticsHub")`. Bucket is project-scoped: `{projectId}/{fileName}.parquet`.
- **LLM components** follow the same shape: a class that lazily constructs its `ChatGoogleGenerativeAI` / `ChatGroq` etc. from `config.ini`, reads its system prompt from `prompts.yaml`, and exposes `invoke` / `astream` / `astream_events`.
- **Python sandboxing**: the reporting tool uses `REPLManager` (timeoutSeconds=7, loose). The transformations feature uses `TransformationExecutor` (timeoutSeconds=30, hardened, restricted imports). Do not reuse `REPLManager` for transformation code — it lacks the import restrictions.
- **Module metadata**: every file carries `__version__`, `__author__`, and `__all__`. Keep these updated when adding new public symbols.

## CI / scheduled jobs

`.github/workflows/subscriptionCron.yaml` runs `nubrix/components/subscriptionManager.py` directly (outside the FastAPI container) daily at 01:00 UTC using GitHub Actions secrets. This is a **separate script** from the in-app `subscriptionExpiry` Celery task — it covers a different slice of subscription lifecycle. The container's `.env` is not used in that workflow. The app's other subscription/billing jobs run via Celery Beat (see `nubrix/triggers/celery.py` beat schedule) — daily billing at 00:00 UTC, annual renewal sweep at 00:30, renewal reminders at 02:00, past-due/entitlement sweeps every 30 min, reconciliation every 15 min, billing metrics every 30 min, subscription-expiry sweep at 01:00 UTC.