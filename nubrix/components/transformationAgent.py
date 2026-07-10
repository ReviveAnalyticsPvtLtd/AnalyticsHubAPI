"""
transformationAgent.py

This module defines the TransformationAgent for generating executable pandas
transformation code, Mermaid flowcharts, and user-facing summaries.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["TransformationAgent"]


from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from utils.llm import getGenaiLlm
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
    and summarizes history older than the last 2 exchanges (4 messages) to optimize context window usage.
    """

    _MAX_SUMMARY_CACHE_SIZE = 256

    def __init__(self):
        """Initialize model configuration."""
        logger.info("Initializing TransformationAgent.")
        self.transformationAgentConfig = TransformationAgentConfig()
        self.config = getConfig(self.transformationAgentConfig.configPath)
        self.systemPrompt = readYaml(self.transformationAgentConfig.yamlPath).get("transformationAgentPrompt")
        self.llm = getGenaiLlm(
            model=self.config.get("TRANSFORMATIONAGENT", "model"),
            temperature=self.config.getfloat("TRANSFORMATIONAGENT", "temperature"),
            max_tokens=self.config.getint("TRANSFORMATIONAGENT", "maxTokens", fallback=8192),
        )
        # LRU cache for raw LLM summaries (keyed by message-id chain)
        self._historySummaryCache: OrderedDict[str, str] = OrderedDict()
        # Per-thread summary state (keyed by transformationId)
        self._threadSummaryCache: dict[str, tuple[str, int]] = {}

    async def _summarizeHistory(
        self,
        oldMessages: list[BaseMessage],
        cacheKey: str,
        userId: str | None = None,
    ) -> str:
        """Summarize old chat history messages using the LLM, with LRU-bounded caching.

        Args:
            oldMessages: LangChain messages to compress into a summary.
            cacheKey: Stable key used to memoize the generated summary (LRU-bounded).
            userId: Optional authenticated user id; used for Langfuse tracing and
                credit tracking. Must be a non-empty string when provided.

        Returns:
            A summary string. On any internal failure, returns a safe fallback
            string instead of raising, so the calling agent can continue.
        """
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
                    if parsed.get("pythonCode"):
                        content += " (Transformation code was generated)"
            except Exception:
                pass
            formatted.append(f"{role}: {content}")
        textToSummarize = "\n".join(formatted)

        prompt = (
            "Summarize the following data transformation conversation history as compact bullet points. "
            "The summary will be used as context for FUTURE steps that must build cumulatively on prior work, "
            "so preserve concrete, actionable details and DROP pleasantries.\n\n"
            "MUST PRESERVE (be specific, use exact identifiers):\n"
            "- Source table names loaded (e.g. `orders`, `customers`) and any aliases assigned.\n"
            "- Every transformation applied in order: filters (with conditions), joins (with keys), "
            "column renames (old -> new), column additions/deletions, aggregations (group-by keys, agg funcs), "
            "sorts, type conversions, and the resulting intermediate/final variable names.\n"
            "- Any user constraints or decisions: required columns in output, naming conventions, "
            "business rules the user stated.\n"
            "- Failed attempts the user redirected away from (e.g. 'tried join on user_id, switched to customer_id'), "
            "so we don't repeat the same mistake.\n"
            "- The current output table name (if any has been applied).\n\n"
            "DROP: greetings, apologies, generic phrases like 'I will help you', restatements of the user's request.\n\n"
            f"CONVERSATION:\n{textToSummarize}"
        )
        # Normalize userId: only treat it as truthy if it is a non-empty string.
        # The downstream tracing/credit helpers do not accept None, and Langfuse
        # metadata keys would otherwise be polluted with literal "None" strings.
        safeUserId = userId if isinstance(userId, str) and userId.strip() else None
        summaryConfig: dict = {}
        try:
            from utils.llm import getLangfuseConfig
            from api.services.credits.creditTrackingCallback import CreditTrackingCallback

            if safeUserId:
                try:
                    summaryConfig = getLangfuseConfig(
                        trace_name="TransformationAgent-HistorySummary",
                        userId=safeUserId,
                    ) or {}
                except Exception as cfgErr:
                    logger.warning(f"Failed to build Langfuse config for history summary: {cfgErr}")
                    summaryConfig = {}

                try:
                    summaryConfig.setdefault("callbacks", []).append(
                        CreditTrackingCallback(
                            userId=safeUserId,
                            operationType="transformation_history_summary",
                        )
                    )
                except Exception as cbErr:
                    # Credit tracking is best-effort: never fail the summary for it.
                    logger.warning(f"Failed to attach CreditTrackingCallback: {cbErr}")

            summaryResponse = await self.llm.ainvoke(prompt, config=summaryConfig or None)
            summary = getattr(summaryResponse, "content", None) or ""

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

    async def _getMessages(self, chatHistory: list[dict], formattedInput: str, transformationId: str, userId: str | None = None) -> list[BaseMessage]:
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
        for i in range(len(chatHistory) - 1, -1, -1):
            msg = chatHistory[i]
            if msg.get("role") == "assistant" and msg.get("python_code"):
                last_python_code = msg.get("python_code")
                last_mermaid_code = msg.get("artifact", {}).get("code") if msg.get("artifact") else None
                break

        # Keep past 4 messages (last 2 exchanges) in full; summarize anything older
        summaryMessage = None
        unsummarized_msgs = []
        recentHistory = history

        if len(history) > 4:
            oldHistory = history[:-4]
            recentHistory = history[-4:]
            oldChatHistory = chatHistory[:-4]

            # Get cached summary for this transformation thread
            cached_summary, cached_count = self._threadSummaryCache.get(transformationId, ("", 0))

            # Detect stale cache (e.g. after rollback truncated messages)
            if cached_count > len(oldHistory):
                cached_summary = ""
                cached_count = 0

            # Check how many new messages need to be summarized since the last summary
            new_unsummarized_count = len(oldHistory) - cached_count

            if new_unsummarized_count >= 4 or (not cached_summary and len(oldHistory) >= 4):
                # Regenerate summary and cache it (only if there's enough old history to justify an LLM call)
                logger.info(f"Regenerating conversation history summary for thread {transformationId}.")
                cacheKey = ":".join(m.get("message_id", "") for m in oldChatHistory)
                _safeUserId = userId if isinstance(userId, str) and userId.strip() else None
                summaryText = await self._summarizeHistory(
                    oldHistory,
                    cacheKey,
                    userId=_safeUserId,
                )
                self._threadSummaryCache[transformationId] = (summaryText, len(oldHistory))
                cached_summary = summaryText
                unsummarized_msgs = []
            else:
                # Use cached summary and pass any unsummarized old messages in full
                # (also covers the case where oldHistory < 4 and no summary exists yet)
                unsummarized_msgs = oldHistory[cached_count:]

            if cached_summary:
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
        callbacks: list | None = None,
        userId: str | None = None,
    ) -> TransformationAgentResponse:
        """
        Generate a structured transformation response, with self-healing retries and a ReAct agent loop.
        History is summarized and windowed via _getMessages(); the last approved active code state is
        always injected so the LLM builds cumulatively on prior steps.
        """
        from nubrix.components.transformationExecutor import TransformationExecutor
        from langchain.agents import create_agent

        executor = TransformationExecutor()

        # Define the inspection tool
        @tool
        def inspect_dataset(pythonCode: str) -> str:
            """
            Run pandas inspection code in a safe Python sandbox on the project's data.
            Use this tool to inspect table shapes, column names, duplicate counts, unique values, and sample data.
            Your code MUST fetch data using fetch_data(projectId, '<table_name>'), perform inspection operations, and print the results.
            The printed output will be returned to you. Limit pythonCode to read-only inspection operations.
            """
            code_to_exec = pythonCode.strip()
            # If the LLM wraps the argument as a JSON/dict object, defensively unpack it
            if (code_to_exec.startswith("{") and code_to_exec.endswith("}")) or "pythonCode" in code_to_exec:
                try:
                    parsed = json.loads(code_to_exec)
                    if isinstance(parsed, dict) and "pythonCode" in parsed:
                        code_to_exec = parsed["pythonCode"]
                except Exception:
                    # Attempt simple regex extraction for escaped/nested quotes
                    import re
                    match = re.search(r'"pythonCode"\s*:\s*"(.*?)"', code_to_exec, re.DOTALL)
                    if match:
                        try:
                            code_to_exec = match.group(1).encode().decode('unicode-escape')
                        except Exception:
                            code_to_exec = match.group(1)

            return executor.executeInspection(projectId, code_to_exec)

        tools = [inspect_dataset]

        # Custom parser helper in case structured_response is missing
        def parse_agent_response(text: str) -> tuple[TransformationAgentResponse | None, str | None]:
            text = text.strip()
            # Clean enclosing markdown blocks if present
            clean_text = text
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
            clean_text = clean_text.strip()

            try:
                parsed = json.loads(clean_text)
                resp = TransformationAgentResponse(
                    pythonCode=parsed.get("pythonCode"),
                    mermaidCode=parsed.get("mermaidCode"),
                    userFacingResponse=parsed.get("userFacingResponse") or ""
                )
                return resp, None
            except Exception:
                start = text.find('{')
                end = text.rfind('}')
                if start != -1 and end != -1 and end > start:
                    preamble = text[:start].strip()
                    json_candidate = text[start:end+1]
                    try:
                        parsed = json.loads(json_candidate)
                        user_resp = parsed.get("userFacingResponse") or ""
                        if preamble:
                            user_resp = f"{preamble}\n\n{user_resp}"
                        resp = TransformationAgentResponse(
                            pythonCode=parsed.get("pythonCode"),
                            mermaidCode=parsed.get("mermaidCode"),
                            userFacingResponse=user_resp
                        )
                        return resp, None
                    except Exception as e:
                        return None, f"JSON candidate block found but failed to parse/validate: {str(e)}"

            return TransformationAgentResponse(
                pythonCode=None,
                mermaidCode=None,
                userFacingResponse=text
            ), None

        try:
            # Build system prompt with metadata injected (passed to the agent, not the user turn)
            metadataJson = json.dumps(metadata, ensure_ascii=False, default=str)
            system_prompt = (
                self.systemPrompt
                .replace("{metadata}", metadataJson)
                .replace("{user_request}", userMessage)
            )

            # Instantiate the agent once; message list is rebuilt per retry
            agent = create_agent(
                model=self.llm,
                tools=tools,
                system_prompt=system_prompt,
                response_format=TransformationAgentResponse
            )

            base_input = f"Please perform the transformation: {userMessage}"
            max_retries = 3
            error_feedback = ""
            response: TransformationAgentResponse | None = None

            for attempt in range(max_retries):
                # Inject retry error feedback into the user turn if a previous attempt failed
                current_input = base_input
                if error_feedback:
                    current_input = (
                        f"{base_input}\n\n"
                        f"[RETRY FEEDBACK]: Your previous response failed with the following error:\n"
                        f"{error_feedback}\n"
                        "Please correct the formatting and code structure."
                    )

                # Build full message list: summary + active-code injection + windowed history + current turn
                from utils.llm import getLangfuseConfig
                messages_to_send = await self._getMessages(
                    chatHistory=chatHistory,
                    formattedInput=current_input,
                    transformationId=transformationId,
                    userId=userId,
                )

                agentConfig = getLangfuseConfig(
                    trace_name="TransformationAgent",
                    projectId=projectId,
                    userId=userId,
                )
                # Cap tool-call iterations: create_agent defaults to recursion_limit=9999
                # which causes infinite loops with lighter models. 10 is more than enough.
                agentConfig["recursion_limit"] = 10
                if callbacks:
                    agentConfig.setdefault("callbacks", []).extend(callbacks)

                try:
                    res = await agent.ainvoke(
                        {"messages": messages_to_send},
                        config=agentConfig,
                    )
                except Exception as agent_err:
                    # GraphRecursionError or similar — agent hit the tool-call iteration cap
                    err_name = type(agent_err).__name__
                    logger.warning(f"Agent invocation failed on attempt {attempt + 1} ({err_name}): {agent_err}")
                    error_feedback = (
                        "You exceeded the maximum number of tool-call iterations. "
                        "Stop calling inspect_dataset and produce your final JSON response directly "
                        "with pythonCode, mermaidCode, and userFacingResponse."
                    )
                    response = None
                    continue

                response = res.get("structured_response")
                if not response:
                    last_msg = res["messages"][-1]
                    response, parse_err = parse_agent_response(last_msg.content)
                else:
                    parse_err = None

                if parse_err:
                    logger.warning(f"Agent response parsing failed on attempt {attempt + 1}: {parse_err}")
                    error_feedback = (
                        f"Your output failed structure or schema validation with the following error:\n"
                        f"{parse_err}\n"
                        "Please correct the output format, ensure pythonCode and mermaidCode are aligned, "
                        "and strictly conform to the expected JSON schema."
                    )
                    response = None
                    continue

                # No code means clarification / conversational reply — return immediately
                if not response.pythonCode:
                    return response

                # Test-execute the generated Python code; retry with error feedback on failure
                try:
                    executor._execute_code(projectId=projectId, pythonCode=response.pythonCode)
                    logger.info(f"Generated python code executed successfully on attempt {attempt + 1}.")
                    return response
                except Exception as e:
                    error_msg = str(e)
                    logger.warning(f"Transformation code execution failed on attempt {attempt + 1}: {error_msg}")
                    error_feedback = (
                        f"The Python code you generated failed to execute with the following error:\n"
                        f"{error_msg}\n"
                        "Please analyze the error, rewrite the Python code and Mermaid diagram to resolve it, "
                        "and ensure the output is correct and executable."
                    )

            # All retries exhausted — return whatever we have, or a safe fallback
            if response is None:
                logger.error("TransformationAgent exhausted all retries with no parseable response.")
                return TransformationAgentResponse(
                    pythonCode=None,
                    mermaidCode=None,
                    userFacingResponse=(
                        "I was unable to generate a valid transformation after multiple attempts. "
                        "Please try rephrasing your request with more specific table and column details."
                    ),
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
        chatHistory: list[dict],
        callbacks: list | None = None,
        userId: str | None = None,
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
            callbacks=callbacks,
            userId=userId,
        )
        summaryTokens = response.userFacingResponse.split(" ")
        for token in summaryTokens:
            yield {"type": "token", "delta": f"{token} "}
        yield {"type": "done", "structured": response}
