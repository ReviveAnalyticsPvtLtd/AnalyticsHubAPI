import os
from langchain_google_genai import ChatGoogleGenerativeAI

def getGenaiLlm(model: str, temperature: float, max_tokens: int = None) -> ChatGoogleGenerativeAI:
    """
    Factory function to instantiate ChatGoogleGenerativeAI, enforcing the use of API keys
    and disabling GCE metadata check.
    """
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
