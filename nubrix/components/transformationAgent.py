"""
transformationAgent.py

This module defines the TransformationAgent for generating executable pandas
transformation code, Mermaid flowcharts, and user-facing summaries.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["TransformationAgent"]


from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
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
    and summarizes history older than 10 messages to optimize context window usage.
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
            from utils.llm import getLangfuseConfig
            summaryResponse = await self.llm.ainvoke(
                prompt,
                config=getLangfuseConfig(trace_name="TransformationAgent-HistorySummary")
            )
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
        callbacks: list | None = None,
        userId: str | None = None,
    ) -> TransformationAgentResponse:
        """
        Generate a structured transformation response, with self-healing retries and a ReAct agent loop.
        """
        from nubrix.components.transformationExecutor import TransformationExecutor
        from langchain.agents import create_agent
        from langchain_core.tools import tool

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

        try:
            # Load previous code context from database messages
            last_python_code = None
            last_mermaid_code = None
            for msg in reversed(chatHistory):
                if msg.get("role") == "assistant" and msg.get("python_code"):
                    last_python_code = msg.get("python_code")
                    last_mermaid_code = msg.get("artifact", {}).get("code") if msg.get("artifact") else None
                    break

            active_code_prefix = ""
            if last_python_code:
                active_code_prefix = (
                    "Current Active Code State:\n"
                    f"### Python Code:\n```python\n{last_python_code}\n```\n\n"
                    f"### Mermaid Flowchart:\n```mermaid\n{last_mermaid_code or ''}\n```\n"
                    "CRITICAL DIRECTIVE: You must build upon, modify, or extend this active code state. "
                    "Your output Python code must represent the cumulative pipeline (including the previous steps and the new step). "
                    "Do not restart from scratch unless the user explicitly requests a reset.\n\n"
                )

            # Build system_prompt and base input
            metadataJson = json.dumps(metadata, ensure_ascii=False, default=str)
            system_prompt = self.systemPrompt.replace("{metadata}", metadataJson).replace("{user_request}", userMessage)
            
            base_input = active_code_prefix + f"Please perform the transformation: {userMessage}"

            # Format the conversation history
            messages = []
            for msg in chatHistory:
                role = "user" if msg.get("role") == "user" else "assistant"
                content = msg.get("content") or ""
                if msg.get("python_code"):
                    content += f"\nGenerated Python Code:\n```python\n{msg.get('python_code')}\n```"
                if msg.get("artifact") and msg.get("artifact").get("code"):
                    content += f"\nGenerated Flowchart:\n```mermaid\n{msg.get('artifact').get('code')}\n```"
                messages.append((role, content))

            # Instantiate the LangChain 1.x agent using create_agent
            agent = create_agent(
                model=self.llm,
                tools=tools,
                system_prompt=system_prompt,
                response_format=TransformationAgentResponse
            )

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

                resp = TransformationAgentResponse(
                    pythonCode=None,
                    mermaidCode=None,
                    userFacingResponse=text
                )
                return resp, None

            max_retries = 3
            error_feedback = ""

            for attempt in range(max_retries):
                # Construct final prompt with feedback if there was a previous failure
                current_input = base_input
                if error_feedback:
                    current_input = f"{base_input}\n\n[RETRY FEEDBACK]: Your previous response failed with the following error:\n{error_feedback}\nPlease correct the formatting and code structure."

                # Append current user query
                current_messages = list(messages) + [("user", current_input)]

                # Invoke the agent graph
                from utils.llm import getLangfuseConfig
                agentConfig = getLangfuseConfig(trace_name="TransformationAgent", projectId=projectId, userId=userId)
                if callbacks:
                    agentConfig.setdefault("callbacks", []).extend(callbacks)
                res = await agent.ainvoke(
                    {"messages": current_messages},
                    config=agentConfig or None,
                )

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
                    continue

                if not response.pythonCode:
                    return response

                # Test execution of the generated Python code
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

            # If all retries failed, return the last parsed response
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
