"""
langfuseClient.py

Initializes the Langfuse singleton client used by the credit system for usage
analytics (Metrics API) and by the STT path for manual generation logging.

Per-request LLM tracing with user/session/tag scoping is handled by
``utils.llm.getLangfuseConfig``; this module owns the durable client used for
out-of-band logging and metrics queries.
"""

from langfuse import Langfuse
from utils.logger import logger
import os


def _resolveHost() -> str:
    """Resolve the Langfuse host, honouring both LANGFUSE_HOST and the
    LANGFUSE_BASE_URL alias used in some environments."""
    return (
        os.environ.get("LANGFUSE_HOST")
        or os.environ.get("LANGFUSE_BASE_URL")
        or "https://cloud.langfuse.com"
    )


def _initLangfuseClient() -> Langfuse | None:
    """
    Create the module-level Langfuse singleton.

    Returns None (with a warning) when credentials are missing so the
    application can still start in local/dev environments without Langfuse.
    """
    publicKey = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secretKey = os.environ.get("LANGFUSE_SECRET_KEY")
    host = _resolveHost()

    if not publicKey or not secretKey:
        logger.warning(
            "Langfuse credentials not set (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY). "
            "LLM usage analytics and manual tracing will be disabled."
        )
        return None

    try:
        client = Langfuse(
            public_key=publicKey,
            secret_key=secretKey,
            host=host,
        )
        logger.info(f"Langfuse client initialized — host={host}")
        return client
    except Exception as e:
        logger.error(f"Failed to initialize Langfuse client: {e}")
        return None


langfuseClient: Langfuse | None = _initLangfuseClient()


def logManualGeneration(
    userId: str,
    name: str,
    model: str,
    inputSummary: dict | str,
    output: str,
    usage: dict | None = None,
    tags: list[str] | None = None,
) -> None:
    """
    Log a non-LangChain LLM/API call (e.g. Groq STT) to Langfuse as a
    generation observation so it appears alongside LangChain traces.

    Uses ``propagate_attributes`` to attach trace-level ``user_id`` and
    ``tags`` so the observation is filterable by userId in the Metrics API
    (required for ``/credits/usage`` breakdown).

    Safe to call when Langfuse is not configured — silently returns.  All
    Langfuse interactions are best-effort and never raise to the caller.

    Args:
        userId:       Platform user ID attached at the trace level.
        name:         Descriptive generation name, e.g. "speech-to-text".
        model:        Model identifier, e.g. "whisper-large-v3-turbo".
        inputSummary: Compact representation of the input (not raw audio).
        output:       The generation output text.
        usage:        Optional token counts, e.g. ``{"input": N, "output": N}``.
        tags:         Operation tags, e.g. ``["utility", "speech_to_text"]``.
    """
    if langfuseClient is None:
        return

    try:
        from langfuse import propagate_attributes

        with propagate_attributes(user_id=userId, tags=tags or []):
            with langfuseClient.start_as_current_observation(
                as_type="generation",
                name=name,
                model=model,
            ) as gen:
                gen.update(
                    input=inputSummary,
                    output=output,
                    usage_details=usage,
                    metadata={"userId": userId, "tags": tags or []},
                )
        try:
            langfuseClient.flush()
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"logManualGeneration failed — name={name}, userId={userId}: {e}")
