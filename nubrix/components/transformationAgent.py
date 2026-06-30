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
        self._summaryCache: dict[str, str] = {}

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

    async def _getMessages(self, chatHistory: list[dict], formattedInput: str) -> list[BaseMessage]:
        """Convert database chat history to LangChain messages, summarizing if history is too long."""
        history = []
        for msg in chatHistory:
            role = msg.get("role")
            if role == "user":
                history.append(HumanMessage(content=msg.get("content") or ""))
            elif role == "assistant":
                # Reconstruct assistant model output representation for chat context continuity
                payload = {
                    "userFacingResponse": msg.get("content") or "",
                }
                python_code = msg.get("python_code")
                mermaid_code = msg.get("artifact", {}).get("code") if msg.get("artifact") else None
                
                if python_code is not None:
                    payload["pythonCode"] = python_code
                if mermaid_code is not None:
                    payload["mermaidCode"] = mermaid_code

                assistantContent = json.dumps(payload, ensure_ascii=False)
                history.append(AIMessage(content=assistantContent))

        # Summarize older history if message count exceeds 7
        if len(history) > 7:
            oldHistory = history[:-7]
            recentHistory = history[-7:]
            oldChatHistory = chatHistory[:-7]
            cacheKey = ":".join(m.get("message_id", "") for m in oldChatHistory)
            summaryText = await self._summarizeHistory(oldHistory, cacheKey)
            summaryMessage = SystemMessage(
                content=f"Summary of earlier conversation history:\n{summaryText}"
            )
            return [summaryMessage, *recentHistory, HumanMessage(content=formattedInput)]

        return [*history, HumanMessage(content=formattedInput)]

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
