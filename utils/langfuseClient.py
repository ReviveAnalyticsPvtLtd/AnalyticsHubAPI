"""
langfuseClient.py

Initializes the Langfuse singleton client and provides a factory for creating
LangChain CallbackHandler instances.  Used by the credit system for usage
analytics (Metrics API) and by the STT path for manual generation logging.

Targets the Langfuse v3/v4 SDK (``langfuse>=3.0.0``).  Per-request LLM tracing
with user/session/tag scoping is handled by ``utils.llm.getLangfuseConfig``
(which attaches ``langfuse_user_id`` / ``langfuse_tags`` via run-config
metadata); this module owns the durable client used for out-of-band logging
and metrics queries.
"""

__version__ = "2.0.0"
__author__ = "Rohit Mishra"
__all__ = ["langfuseClient", "getLangfuseHandler", "logManualGeneration"]


from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
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


def getLangfuseHandler(
    userId: str | None = None,
    sessionId: str | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> CallbackHandler | None:
    """
    Factory that returns a Langfuse LangChain CallbackHandler.

    Note (v3/v4): the handler itself is no longer scoped to a user/session at
    construction time. User/session/tag scoping is applied per invocation via
    run-config metadata — see ``utils.llm.getLangfuseConfig``, which is the
    preferred entry point for tracing chain/agent/workflow calls.  The
    ``userId``/``sessionId``/``tags``/``metadata`` arguments are retained for
    backward compatibility and are ignored by the underlying handler.

    Returns None when Langfuse is not configured so callers can safely filter
    it out of their callback lists.
    """
    if langfuseClient is None:
        return None

    try:
        return CallbackHandler()
    except Exception as e:
        logger.warning(f"Failed to create Langfuse callback handler: {e}")
        return None


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
