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
    via SQLChatMessageHistory and summarizes history older than 7 messages to
    optimize context window usage.
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
        self.structuredLlm = self.llm.with_structured_output(TransformationAgentResponse, method="json_mode")

    def _threadKey(self, projectId: str, transformationId: str) -> str:
        """Return the stable chat thread key."""
        return f"{projectId}::{transformationId}"

    def _buildInput(self, userMessage: str, metadata: dict) -> str:
        """Build the prompt input from metadata and user request."""
        metadataJson = json.dumps(metadata, ensure_ascii=False, default=str)
        return (
            self.systemPrompt
            .replace("{metadata}", metadataJson)
            .replace("{user_request}", userMessage)
        )

    def _getHistory(self, projectId: str, transformationId: str):
        """Return the SQLChatMessageHistory instance for the chat thread."""
        from langchain_community.chat_message_histories import SQLChatMessageHistory
        key = self._threadKey(projectId, transformationId)
        return SQLChatMessageHistory(
            session_id=key,
            connection=os.environ.get("DATABASE_URL")
        )

    def syncHistoryFromDb(self, projectId: str, transformationId: str, dbMessages: list[dict]) -> None:
        """Synchronize SQL-backed chat history messages with the database messages list."""
        try:
            historyConn = self._getHistory(projectId, transformationId)
            historyConn.clear()

            langchainMessages = []
            for msg in dbMessages:
                role = msg.get("role")
                if role == "user":
                    langchainMessages.append(HumanMessage(content=msg.get("content") or ""))
                elif role == "assistant":
                    # Reconstruct assistant model output representation for chat context continuity
                    assistantContent = json.dumps({
                        "userFacingResponse": msg.get("content") or "",
                        "pythonCode": msg.get("python_code"),
                        "mermaidCode": msg.get("artifact", {}).get("code") if msg.get("artifact") else None
                    }, ensure_ascii=False)
                    langchainMessages.append(AIMessage(content=assistantContent))

            if langchainMessages:
                historyConn.add_messages(langchainMessages)
            logger.info(f"Synchronized SQL checkpointer for transformation {transformationId} with {len(langchainMessages)} messages.")
        except Exception as e:
            logger.error(f"Failed to synchronize SQL checkpointer for transformation {transformationId}: {e}")

    async def _summarizeHistory(self, oldMessages: list[BaseMessage]) -> str:
        """Summarize old chat history messages using the LLM."""
        if not oldMessages:
            return ""
        formatted = []
        for msg in oldMessages:
            role = "User" if isinstance(msg, HumanMessage) else "Assistant"
            # Extract content cleanly in case it is structured JSON
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
            return summaryResponse.content
        except Exception as e:
            logger.warning(f"Failed to summarize history: {e}")
            return "Earlier conversation history summary could not be generated."

    async def _getMessages(self, projectId: str, transformationId: str, formattedInput: str) -> list[BaseMessage]:
        """Return the message list for the current request, summarizing if history is too long."""
        historyConn = self._getHistory(projectId, transformationId)
        history = historyConn.messages

        # Summarize older history if message count exceeds 7
        if len(history) > 7:
            oldHistory = history[:-7]
            recentHistory = history[-7:]
            summaryText = await self._summarizeHistory(oldHistory)
            summaryMessage = SystemMessage(
                content=f"Summary of earlier conversation history:\n{summaryText}"
            )
            return [summaryMessage, *recentHistory, HumanMessage(content=formattedInput)]

        return [*history, HumanMessage(content=formattedInput)]

    def _appendHistory(
        self,
        projectId: str,
        transformationId: str,
        userMessage: str,
        response: TransformationAgentResponse,
    ) -> None:
        """Append a completed exchange to the SQL chat history."""
        historyConn = self._getHistory(projectId, transformationId)
        assistantContent = json.dumps(response.model_dump(), ensure_ascii=False)
        historyConn.add_messages([
            HumanMessage(content=userMessage),
            AIMessage(content=assistantContent),
        ])

    async def invoke(
        self,
        projectId: str,
        transformationId: str,
        userMessage: str,
        metadata: dict,
        saver=None,
    ) -> TransformationAgentResponse:
        """
        Generate a structured transformation response.
        """
        try:
            formattedInput = self._buildInput(userMessage=userMessage, metadata=metadata)
            messages = await self._getMessages(
                projectId=projectId,
                transformationId=transformationId,
                formattedInput=formattedInput,
            )
            response = await self.structuredLlm.ainvoke(messages)
            self._appendHistory(
                projectId=projectId,
                transformationId=transformationId,
                userMessage=userMessage,
                response=response,
            )
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
        saver=None,
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
            saver=saver,
        )
        summaryTokens = response.userFacingResponse.split(" ")
        for token in summaryTokens:
            yield {"type": "token", "delta": f"{token} "}
        yield {"type": "done", "structured": response}
