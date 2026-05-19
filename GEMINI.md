# AnalyticsHub

AnalyticsHub is a high-performance, FastAPI-powered data analytics and insights platform. It integrates Large Language Models (LLMs) to provide intelligent data exploration, automated metadata generation, and dynamic code execution for data visualization.

## Project Overview

-   **Backend:** FastAPI (Python 3.10+)
-   **AI/LLM Integration:** Extensively uses Google Gemini 1.5 Flash via LangChain for query rephrasing, insight generation, and code generation. It also supports Groq, OpenAI (via OpenRouter), and Whisper for speech-to-text.
-   **Data Processing:** Uses DuckDB for local data handling, Pandas for manipulation, and supports external sources like MySQL, PostgreSQL, and MongoDB.
-   **State & Auth:** Supabase is used for authentication, session management, and project state persistence.
-   **Task Management:** Celery with Redis/AMQP handles background tasks like report generation and subscription management.
-   **Process Management:** Supervisor orchestrates the FastAPI application (Gunicorn/Uvicorn), Celery worker, and Celery beat.

## Key Directories

-   `api/`: Contains FastAPI routers and services.
    -   `routers/`: API endpoints (auth, projects, loaders, reporting, etc.).
    -   `services/`: Business logic corresponding to the routers.
-   `analyticsHub/`: Core business logic and components.
    -   `components/`: Specialized modules for AI processing (metadata generation, code generators, etc.).
    -   `triggers/`: Celery task definitions.
    -   `workflows/`: Higher-level orchestration logic.
-   `utils/`: Shared utilities like logging (`loguru`), exception handling, and code execution.

## Building and Running

The project uses `uv` for dependency management.

### Prerequisites
- Python 3.10+
- `uv` installed
- Redis or RabbitMQ (for Celery)
- Environment variables configured (see `.env.example` or equivalent if available, or infer from `api/commons.py` and `config.ini`).

### Installation
```bash
uv sync
```

### Running Locally
You can run the processes individually or via Supervisor.

**FastAPI:**
```bash
uv run gunicorn main:app --workers=4 --worker-class=uvicorn.workers.UvicornWorker --bind=0.0.0.0:7860
```

**Celery Worker:**
```bash
uv run celery -A analyticsHub.triggers.celery.celeryApp worker --loglevel=info
```

**Celery Beat:**
```bash
uv run celery -A analyticsHub.triggers.celery.celeryApp beat --loglevel=info
```

### Docker
A `Dockerfile` is provided that uses `startup.sh` to launch `supervisord`, which manages all three processes.
```bash
docker build -t analyticshub .
docker run -p 7860:7860 analyticshub
```

## Development Conventions

-   **Logging:** Use the logger from `utils/logger.py` (powered by `loguru`). Avoid standard `print` statements.
-   **Data Validation:** All API request/response bodies should use Pydantic models defined in `api/models.py`.
-   **Authentication:** Routes are protected by `verifyToken` in `api/commons.py`, which checks the `Sessions` table in Supabase.
-   **Service Pattern:** Maintain a strict separation between routers (HTTP logic) and services (business logic). Routers should call services.
-   **AI Configuration:** AI prompts and model parameters are centrally managed in `prompts.yaml` and `config.ini`.
-   **Database Interactions:** Use `api/commons.py` for Supabase client interactions. For project-specific data mutations, always call `updateProjectModifiedAt`.
-   **Testing:** Currently, no automated tests are defined in the project. Adding tests (e.g., using `pytest`) is encouraged for new features.
