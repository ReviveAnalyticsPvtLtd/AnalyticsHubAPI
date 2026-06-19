"""
transformationAgent.py

This module defines the TransformationAgent for generating executable pandas
transformation code, Mermaid flowcharts, and user-facing summaries.
"""

__version__ = "1.0.0"
__author__ = "Platform Engineering"
__all__ = ["TransformationAgent"]


from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from utils.exceptionHandler import CustomException
from api.models import TransformationAgentResponse
from nubrix.utils import getConfig, readYaml
from utils.logger import logger
from dataclasses import dataclass
import json
import os
import threading


@dataclass
class TransformationAgentConfig:
    """Configuration for the TransformationAgent."""
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")


class TransformationAgent:
    """
    Agent wrapper for transformation generation.

    The installed LangChain version does not expose `langchain.agents.create_agent`,
    so this implementation uses Gemini structured output directly and maintains
    per-transformation chat context in memory.
    """
    def __init__(self):
        """Initialize model configuration and in-memory chat history."""
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
        self._history: dict[str, list[BaseMessage]] = {}
        self._historyLock = threading.Lock()

    def _thread_key(self, projectId: str, transformationId: str) -> str:
        """Return the stable chat thread key."""
        return f"{projectId}::{transformationId}"

    def _build_input(self, userMessage: str, metadata: dict) -> str:
        """Build the prompt input from metadata and user request."""
        metadataJson = json.dumps(metadata, ensure_ascii=False, default=str)
        return (
            self.systemPrompt
            .replace("{metadata}", metadataJson)
            .replace("{user_request}", userMessage)
        )

    def _get_messages(self, projectId: str, transformationId: str, formattedInput: str) -> list[BaseMessage]:
        """Return the message list for the current request."""
        key = self._thread_key(projectId, transformationId)
        with self._historyLock:
            history = list(self._history.get(key, []))
        return [*history, HumanMessage(content=formattedInput)]

    def _append_history(
        self,
        projectId: str,
        transformationId: str,
        userMessage: str,
        response: TransformationAgentResponse,
    ) -> None:
        """Append a completed exchange to the in-memory chat history."""
        key = self._thread_key(projectId, transformationId)
        assistantContent = json.dumps(response.model_dump(), ensure_ascii=False)
        with self._historyLock:
            self._history.setdefault(key, []).extend([
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
            formattedInput = self._build_input(userMessage=userMessage, metadata=metadata)
            messages = self._get_messages(
                projectId=projectId,
                transformationId=transformationId,
                formattedInput=formattedInput,
            )
            response = await self.structuredLlm.ainvoke(messages)
            self._append_history(
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
        Stream transformation output events.

        Gemini structured output is not reliably token-streamable in this
        dependency stack, so this method emits summary tokens after the
        structured response has been generated.
        """
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
