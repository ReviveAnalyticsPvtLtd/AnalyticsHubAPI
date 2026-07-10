import os
from langchain_google_genai import ChatGoogleGenerativeAI

def getGenaiLlm(model: str, temperature: float, max_tokens: int = None) -> ChatGoogleGenerativeAI:
    """
    Factory function to instantiate ChatGoogleGenerativeAI, enforcing the use of API keys
    and disabling GCE metadata check.
    """
    # Suppress noisy warnings from langchain_google_genai / google-genai
    import warnings
    warnings.filterwarnings("ignore", message=".*non-text parts.*")

    # Enforce NO_GCE_CHECK to disable GCE metadata checks globally in google-auth
    os.environ["NO_GCE_CHECK"] = "true"
    # Force the use of Google AI Studio (API key) instead of Vertex AI (which requires GCP IAM credentials)
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
    
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError(
            "Google GenAI API Key is missing. Please set GEMINI_API_KEY or GOOGLE_API_KEY in your environment."
        )
        
    kwargs = {
        "model": model,
        "temperature": temperature,
        "api_key": api_key,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
        
    return ChatGoogleGenerativeAI(**kwargs)

from langchain_core.messages import AIMessage

def cleanThinkTokens(message: AIMessage) -> AIMessage:
    """
    Robustly removes <think> and </think> tokens from AIMessage content,
    supporting both raw string content and multi-part list content structures.
    """
    content = message.content
    if isinstance(content, str):
        clean_content = content.replace("<think>", "").replace("</think>", "")
    elif isinstance(content, list):
        clean_list = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                part_copy = dict(part)
                part_copy["text"] = part_copy["text"].replace("<think>", "").replace("</think>", "")
                clean_list.append(part_copy)
            elif isinstance(part, str):
                clean_list.append(part.replace("<think>", "").replace("</think>", ""))
            else:
                clean_list.append(part)
        clean_content = clean_list
    else:
        clean_content = content
    return AIMessage(
        content=clean_content,
        additional_kwargs=message.additional_kwargs,
        response_metadata=message.response_metadata,
        id=message.id
    )

from langfuse.langchain import CallbackHandler

_langfuse_handler = None

def getLangfuseHandler() -> CallbackHandler | None:
    """
    Returns a cached instance of the Langfuse CallbackHandler for LangChain tracing.
    """
    global _langfuse_handler
    if _langfuse_handler is None:
        # Map user's LANGFUSE_BASE_URL to standard LANGFUSE_HOST if needed
        if "LANGFUSE_BASE_URL" in os.environ and "LANGFUSE_HOST" not in os.environ:
            os.environ["LANGFUSE_HOST"] = os.environ["LANGFUSE_BASE_URL"]
            
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        
        if public_key and secret_key:
            _langfuse_handler = CallbackHandler()
    return _langfuse_handler

def getLangfuseConfig(
    trace_name: str,
    projectId: str = None,
    userId: str = None,
    session_id: str = None,
    tags: list[str] = None
) -> dict:
    """
    Constructs the config dictionary to pass to LangChain chain executions
    (e.g., config=getLangfuseConfig(...)) containing callbacks for Langfuse.
    """
    handler = getLangfuseHandler()
    if not handler:
        return {}
        
    metadata = {
        "trace_name": trace_name,
    }
    
    if projectId:
        metadata["projectId"] = projectId
        metadata["langfuse_session_id"] = projectId
    if userId:
        metadata["userId"] = userId
        metadata["langfuse_user_id"] = userId
    if session_id:
        metadata["langfuse_session_id"] = session_id
        
    if tags:
        metadata["langfuse_tags"] = tags
    else:
        metadata["langfuse_tags"] = [trace_name]
        
    return {
        "callbacks": [handler],
        "metadata": metadata
    }
