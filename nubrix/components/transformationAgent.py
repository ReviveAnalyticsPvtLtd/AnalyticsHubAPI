"""
transformationAgent.py

This module defines the TransformationAgent for generating executable pandas
transformation code, Mermaid flowcharts, and user-facing summaries.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["TransformationAgent"]


from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from utils.exceptionHandler import CustomException
from api.models import TransformationAgentResponse
from nubrix.utils import getConfig, readYaml
from collections import OrderedDict
from utils.logger import logger
from dataclasses import dataclass
import json
import os


@dataclass
class TransformationAgentConfig:
    """Configuration for the TransformationAgent."""
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")


class TransformationAgent:
    """
    Agent wrapper for transformation generation.

    Maintains per-transformation chat context persisted in PostgreSQL/Supabase
    and summarizes history older than 10 messages to optimize context window usage.
    """

    _MAX_SUMMARY_CACHE_SIZE = 256

    def __init__(self):
        """Initialize model configuration."""
        logger.info("Initializing TransformationAgent.")
        self.transformationAgentConfig = TransformationAgentConfig()
        self.config = getConfig(self.transformationAgentConfig.configPath)
        self.systemPrompt = readYaml(self.transformationAgentConfig.yamlPath).get("transformationAgentPrompt")
        self.llm = ChatGoogleGenerativeAI(
            model=self.config.get("TRANSFORMATIONAGENT", "model"),
            temperature=self.config.getfloat("TRANSFORMATIONAGENT", "temperature"),
            max_tokens=self.config.getint("TRANSFORMATIONAGENT", "maxTokens", fallback=8192),
        )
        # Bind tool definition for structured generation
        self.boundLlm = self.llm.bind_tools([TransformationAgentResponse])
        # LRU cache for raw LLM summaries (keyed by message-id chain)
        self._historySummaryCache: OrderedDict[str, str] = OrderedDict()
        # Per-thread summary state (keyed by transformationId)
        self._threadSummaryCache: dict[str, tuple[str, int]] = {}

    def _buildInput(self, userMessage: str, metadata: dict) -> str:
        """Build the prompt input from metadata and user request."""
        metadataJson = json.dumps(metadata, ensure_ascii=False, default=str)
        return (
            self.systemPrompt
            .replace("{metadata}", metadataJson)
            .replace("{user_request}", userMessage)
        )

    async def _summarizeHistory(self, oldMessages: list[BaseMessage], cacheKey: str) -> str:
        """Summarize old chat history messages using the LLM, with LRU-bounded caching."""
        if not oldMessages:
            return ""
        if cacheKey in self._historySummaryCache:
            logger.info("Using cached history summary.")
            self._historySummaryCache.move_to_end(cacheKey)
            return self._historySummaryCache[cacheKey]
        formatted = []
        for msg in oldMessages:
            role = "User" if isinstance(msg, HumanMessage) else "Assistant"
            content = msg.content
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "userFacingResponse" in parsed:
                    content = parsed["userFacingResponse"]
                    # Include code context note in summary input so summaries retain transformation awareness
                    if parsed.get("pythonCode"):
                        content += " (Transformation code was generated)"
            except Exception:
                pass
            formatted.append(f"{role}: {content}")
        textToSummarize = "\n".join(formatted)

        prompt = (
            "Summarize the following data transformation conversation history briefly, "
            "focusing on what tables were loaded, what transformations were requested/done, "
            "and any schema details decided, so it can be used as context for future steps:\n\n"
            f"{textToSummarize}"
        )
        try:
            summaryResponse = await self.llm.ainvoke(prompt)
            summary = summaryResponse.content
            # LRU eviction: remove oldest entry if cache exceeds max size
            self._historySummaryCache[cacheKey] = summary
            if len(self._historySummaryCache) > self._MAX_SUMMARY_CACHE_SIZE:
                self._historySummaryCache.popitem(last=False)
            return summary
        except Exception as e:
            logger.warning(f"Failed to summarize history: {e}")
            return "Earlier conversation history summary could not be generated."

    def invalidateThreadCache(self, transformationId: str) -> None:
        """Invalidate the cached summary for a transformation thread (e.g. after rollback)."""
        self._threadSummaryCache.pop(transformationId, None)

    async def _getMessages(self, chatHistory: list[dict], formattedInput: str, transformationId: str) -> list[BaseMessage]:
        """Convert database chat history to LangChain messages, summarizing older history efficiently."""
        history = []
        for msg in chatHistory:
            role = msg.get("role")
            if role == "user":
                history.append(HumanMessage(content=msg.get("content") or ""))
            elif role == "assistant":
                # Reconstruct full assistant model output representation as JSON (including nulls)
                assistantContent = json.dumps({
                    "userFacingResponse": msg.get("content") or "",
                    "pythonCode": msg.get("python_code"),
                    "mermaidCode": msg.get("artifact", {}).get("code") if msg.get("artifact") else None
                }, ensure_ascii=False)
                history.append(AIMessage(content=assistantContent))

        # Find the last active python and mermaid code in the entire history to preserve active code state
        last_python_code = None
        last_mermaid_code = None
        last_code_msg_index = -1
        for i in range(len(chatHistory) - 1, -1, -1):
            msg = chatHistory[i]
            if msg.get("role") == "assistant" and msg.get("python_code"):
                last_python_code = msg.get("python_code")
                last_mermaid_code = msg.get("artifact", {}).get("code") if msg.get("artifact") else None
                last_code_msg_index = i
                break

        # Keep past 10 messages in full
        summaryMessage = None
        unsummarized_msgs = []
        recentHistory = history
        recentWindowStart = 0  # index in chatHistory where the recent 10-message window begins

        if len(history) > 10:
            oldHistory = history[:-10]
            recentHistory = history[-10:]
            oldChatHistory = chatHistory[:-10]
            recentWindowStart = len(chatHistory) - 10

            # Get cached summary for this transformation thread
            cached_summary, cached_count = self._threadSummaryCache.get(transformationId, ("", 0))

            # Detect stale cache (e.g. after rollback truncated messages)
            if cached_count > len(oldHistory):
                cached_summary = ""
                cached_count = 0

            # Check how many new messages need to be summarized since the last summary
            new_unsummarized_count = len(oldHistory) - cached_count

            if new_unsummarized_count >= 4 or not cached_summary:
                # Regenerate summary and cache it
                logger.info(f"Regenerating conversation history summary for thread {transformationId}.")
                cacheKey = ":".join(m.get("message_id", "") for m in oldChatHistory)
                summaryText = await self._summarizeHistory(oldHistory, cacheKey)
                self._threadSummaryCache[transformationId] = (summaryText, len(oldHistory))
                cached_summary = summaryText
                unsummarized_msgs = []
            else:
                # Use cached summary and pass the unsummarized old messages in full
                unsummarized_msgs = oldHistory[cached_count:]

            summaryMessage = SystemMessage(
                content=f"Summary of earlier conversation history:\n{cached_summary}"
            )

        messages_to_send = []
        if summaryMessage:
            messages_to_send.append(summaryMessage)

        # Inject the active code state if it exists, so the model always knows the current active code
        if last_python_code:
            activeCodeMessage = SystemMessage(
                content=(
                    "Current Active Code State:\n"
                    f"### Python Code:\n```python\n{last_python_code}\n```\n\n"
                    f"### Mermaid Flowchart:\n```mermaid\n{last_mermaid_code or ''}\n```\n"
                    "CRITICAL DIRECTIVE: You must build upon, modify, or extend this active code state. "
                    "Your output Python code must represent the cumulative pipeline (including the previous steps and the new step). "
                    "Do not restart from scratch unless the user explicitly requests a reset."
                )
            )
            messages_to_send.append(activeCodeMessage)

        messages_to_send.extend(unsummarized_msgs)
        messages_to_send.extend(recentHistory)
        messages_to_send.append(HumanMessage(content=formattedInput))
        return messages_to_send

    async def invoke(
        self,
        projectId: str,
        transformationId: str,
        userMessage: str,
        metadata: dict,
        chatHistory: list[dict],
    ) -> TransformationAgentResponse:
        """
        Generate a structured transformation response, with self-healing retries.
        """
        from nubrix.components.transformationExecutor import TransformationExecutor
        executor = TransformationExecutor()

        try:
            formattedInput = self._buildInput(userMessage=userMessage, metadata=metadata)
            messages = await self._getMessages(
                chatHistory=chatHistory,
                formattedInput=formattedInput,
                transformationId=transformationId,
            )

            max_retries = 3
            current_messages = list(messages)

            for attempt in range(max_retries):
                raw_res = await self.boundLlm.ainvoke(current_messages)
                
                # Custom parsing to guarantee a robust structured response or a clean text fallback
                response = None
                if raw_res.tool_calls:
                    args = raw_res.tool_calls[0]["args"]
                    response = TransformationAgentResponse(
                        pythonCode=args.get("pythonCode"),
                        mermaidCode=args.get("mermaidCode"),
                        userFacingResponse=args.get("userFacingResponse") or ""
                    )
                else:
                    text = raw_res.content.strip()
                    # Clean enclosing markdown blocks if present
                    if text.startswith("```json"):
                        text = text[7:]
                    if text.endswith("```"):
                        text = text[:-3]
                    text = text.strip()
                    
                    try:
                        parsed = json.loads(text)
                        response = TransformationAgentResponse(
                            pythonCode=parsed.get("pythonCode"),
                            mermaidCode=parsed.get("mermaidCode"),
                            userFacingResponse=parsed.get("userFacingResponse") or ""
                        )
                    except Exception:
                        # Treat the raw text block directly as a userFacingResponse conversational fallback
                        response = TransformationAgentResponse(
                            pythonCode=None,
                            mermaidCode=None,
                            userFacingResponse=raw_res.content
                        )

                # If no Python code is returned (e.g. conversational response or clarification),
                # no execution is needed, return it directly.
                if not response.pythonCode:
                    return response

                # Test execution of the generated Python code
                try:
                    executor._execute_code(projectId=projectId, pythonCode=response.pythonCode)
                    # If it successfully executed, return the working response!
                    logger.info(f"Generated python code executed successfully on attempt {attempt + 1}.")
                    return response
                except Exception as e:
                    error_msg = str(e)
                    logger.warning(f"Transformation code execution failed on attempt {attempt + 1}: {error_msg}")
                    if attempt == max_retries - 1:
                        # Out of retries, return the response as is (caller will handle the error)
                        return response
                    
                    # Feed the error back to the model for self-healing
                    # We append the model's failed attempt and a correction request
                    current_messages.append(AIMessage(content=json.dumps({
                        "pythonCode": response.pythonCode,
                        "mermaidCode": response.mermaidCode,
                        "userFacingResponse": response.userFacingResponse
                    })))
                    current_messages.append(HumanMessage(content=(
                        f"The Python code you generated failed to execute with the following error:\n"
                        f"```\n{error_msg}\n```\n"
                        "Please analyze the error, rewrite the Python code and Mermaid diagram to resolve it, "
                        "and ensure the output is correct and executable."
                    )))
            
            return response
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    async def astream(
        self,
        projectId: str,
        transformationId: str,
        userMessage: str,
        metadata: dict,
        chatHistory: list[dict],
    ):
        """
        Stream transformation output events, yielding progress status updates.
        """
        yield {"type": "status", "message": "Analyzing request..."}
        yield {"type": "status", "message": "Thinking and generating transformation..."}
        response = await self.invoke(
            projectId=projectId,
            transformationId=transformationId,
            userMessage=userMessage,
            metadata=metadata,
            chatHistory=chatHistory,
        )
        summaryTokens = response.userFacingResponse.split(" ")
        for token in summaryTokens:
            yield {"type": "token", "delta": f"{token} "}
        yield {"type": "done", "structured": response}
