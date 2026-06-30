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
    and summarizes history older than 7 messages to optimize context window usage.
    """
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
        self.structuredLlm = self.llm.with_structured_output(TransformationAgentResponse)
        self._summaryCache: dict[str, tuple[str, int]] = {}

    def _buildInput(self, userMessage: str, metadata: dict) -> str:
        """Build the prompt input from metadata and user request."""
        metadataJson = json.dumps(metadata, ensure_ascii=False, default=str)
        return (
            self.systemPrompt
            .replace("{metadata}", metadataJson)
            .replace("{user_request}", userMessage)
        )

    async def _summarizeHistory(self, oldMessages: list[BaseMessage], cacheKey: str) -> str:
        """Summarize old chat history messages using the LLM, with in-memory caching."""
        if not oldMessages:
            return ""
        if cacheKey in self._summaryCache:
            logger.info("Using cached history summary.")
            return self._summaryCache[cacheKey]
        formatted = []
        for msg in oldMessages:
            role = "User" if isinstance(msg, HumanMessage) else "Assistant"
            content = msg.content
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "userFacingResponse" in parsed:
                    content = parsed["userFacingResponse"]
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
            self._summaryCache[cacheKey] = summary
            return summary
        except Exception as e:
            logger.warning(f"Failed to summarize history: {e}")
            return "Earlier conversation history summary could not be generated."

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
        for msg in reversed(chatHistory):
            if msg.get("role") == "assistant" and msg.get("python_code"):
                last_python_code = msg.get("python_code")
                last_mermaid_code = msg.get("artifact", {}).get("code") if msg.get("artifact") else None
                break

        # Keep past 10 messages in full
        summaryMessage = None
        unsummarized_msgs = []
        recentHistory = history

        if len(history) > 10:
            oldHistory = history[:-10]
            recentHistory = history[-10:]
            oldChatHistory = chatHistory[:-10]

            # Get cached summary for this transformation thread
            cached_summary, cached_count = self._summaryCache.get(transformationId, ("", 0))
            
            # Check how many new messages need to be summarized since the last summary
            new_unsummarized_count = len(oldHistory) - cached_count

            if new_unsummarized_count >= 4 or not cached_summary:
                # Regenerate summary and cache it
                logger.info(f"Regenerating conversation history summary for thread {transformationId}.")
                # Compute a temporary key based on the message IDs of the entire oldHistory
                cacheKey = ":".join(m.get("message_id", "") for m in oldChatHistory)
                summaryText = await self._summarizeHistory(oldHistory, cacheKey)
                self._summaryCache[transformationId] = (summaryText, len(oldHistory))
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
                    "You must build upon, modify, or refer to this active code state if the user's new request is a follow-up or modification."
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
        Generate a structured transformation response.
        """
        try:
            formattedInput = self._buildInput(userMessage=userMessage, metadata=metadata)
            messages = await self._getMessages(
                chatHistory=chatHistory,
                formattedInput=formattedInput,
                transformationId=transformationId,
            )
            response = await self.structuredLlm.ainvoke(messages)
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
