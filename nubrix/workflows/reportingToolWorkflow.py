"""
reportingToolWorkflow.py

This module defines the ReportingToolWorkflow class, which orchestrates a multi-agent workflow for query rephrasing, code generation, code execution, error handling, and response formatting in a reporting tool context. It leverages LangGraph for workflow management and integrates with various code and query processing components.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["buildReportingWorkflow"]


from nubrix.components.queryRephraser import QueryRephaser
from nubrix.components.llmChainFactory import buildLlmChain
from langgraph.graph import StateGraph, START, END
from utils.codeExecutor import REPLManager, _remove_code_fences
from typing_extensions import TypedDict
from utils.logger import logger
import json
import os

class State(TypedDict):
    """
    State defines the structure for workflow state, holding all intermediate and final data for the reporting workflow.
    """
    projectId: str
    inputQuery: str
    metadata: str
    rephrasedQuery: str
    generatedCode: str
    codeOutput: str
    finalOutput: dict
    debugAttempts: int

class ReportingToolWorkflow:
    """
    ReportingToolWorkflow manages the multi-step process of query rephrasing, code generation, execution, error handling, and response formatting for the reporting tool.
    """
    MAX_DEBUG_ATTEMPTS = 2

    def __init__(self):
        """
        Initializes the ReportingToolWorkflow with its own chain instances and replManager
        to ensure thread-safe parallel execution without shared state.
        """
        logger.info("Initializing multi-agentic reporting workflow.")
        self.queryRephraseChain = QueryRephaser().getQueryRephraserChain()
        self.codeGeneratorChain = buildLlmChain("CODEGENERATOR", "codeGeneratorAgentPrompt")
        self.codeDebuggerChain = buildLlmChain("CODEDEBUGGER", "codeDebuggerAgentPrompt")
        self.replManager = REPLManager(timeoutSeconds=7)

    def _rephraseQuery(self, state: State):
        """
        Rephrases the user's input query using the query rephraser chain.
        """
        response = self.queryRephraseChain.invoke({
            "query": state["inputQuery"],
            "metadata": state["metadata"]
        })
        return {
            "rephrasedQuery": response
        }

    def _generateCode(self, state: State):
        """
        Generates code for the rephrased query using the code generator chain.
        Injects projectId into all fetch_data / fetch_data_pl / scan_data calls.
        """
        response = self.codeGeneratorChain.invoke({
            "query": state["rephrasedQuery"],
            "metadata": state["metadata"]
        })
        pid = state["projectId"]
        # Inject projectId into all fetch function variants.
        for fn in ("fetch_data_pl", "scan_data", "fetch_data"):
            response = f'{fn}("{pid}", '.join(response.split(f"{fn}("))
        # Deterministic routing: per-table size class drives fetch_data_pl
        # -> scan_data for massive tables so we always get lazy pushdown.
        response = self._route_large_tables_to_scan(response, pid)
        return {
            "generatedCode": response.replace('indent=4', 'default=serializer')
        }

    @staticmethod
    def _route_large_tables_to_scan(code: str, projectId: str) -> str:
        """Rewrite eager ``fetch_data_pl("<pid>", "<table>")`` -> lazy ``scan_data(...)``.

        Only applied to tables classified as 'large' or 'massive' (>=100k rows)
        so small tables keep the eager fast-path. No-op when the underlying
        cache lookup misses (no information beats a bad heuristic).
        """
        import re
        from utils.initMethods import classify_table_size
        try:
            threshold = int(os.getenv("LAZY_FETCH_ROW_THRESHOLD", "100000"))
        except Exception:
            threshold = 100000
        if threshold <= 0:
            return code
        def _maybe_swap(match: re.Match) -> str:
            table = match.group(1)
            size = classify_table_size(projectId, table)
            rows = size.get("rows_estimate")
            if isinstance(rows, int) and rows >= threshold:
                return f'scan_data("{projectId}", "{table}"'
            return match.group(0)
        return re.sub(r'fetch_data_pl\("' + re.escape(projectId) + r'",\s*"([^"]+)"', _maybe_swap, code)

    def _runInPythonSandbox(self, state: State):
        """
        Executes the generated code in a Python sandbox environment and captures the output.
        """
        code = _remove_code_fences(state["generatedCode"])
        response = self.replManager.run(code)
        return {
            "codeOutput": response
        }

    def _outputEvaluationRouter(self, state: State):
        """
        Determines if the code output is valid JSON. Routes workflow based on output validity.
        """
        try:
            _ = json.loads(state["codeOutput"])
            return "pass"
        except (json.JSONDecodeError, TypeError):
            return "fail"

    def _codeDebugger(self, state: State):
        """
        Invokes the code debugger chain to handle errors in code generation or execution.
        Bounded by MAX_DEBUG_ATTEMPTS so a persistent failure doesn't loop forever.
        """
        attempts = state.get("debugAttempts", 0)
        response = self.codeDebuggerChain.invoke({
            "user_query": state["rephrasedQuery"],
            "metadata_context": state["metadata"],
            "code_with_errors": state["generatedCode"],
            "error_message": state["codeOutput"]
        })
        return {
            "generatedCode": response,
            "debugAttempts": attempts + 1,
        }

    def _debugRouter(self, state: State):
        """After a debug attempt, route to format if output is valid JSON, else retry up to budget."""
        try:
            json.loads(state.get("codeOutput", ""))
            return "formatJsonResponse"
        except (json.JSONDecodeError, TypeError):
            pass
        if state.get("debugAttempts", 0) >= self.MAX_DEBUG_ATTEMPTS:
            return "formatJsonResponse"
        return "debugger"

    def _formatJsonResponse(self, state: State):
        """
        Formats the final output as a JSON response, handling errors if present.
        """
        if "codeOutput" in state.keys():
            try:
                response = json.loads(state["codeOutput"])
            except Exception as e:
                response = {"error": f"Endpoint says: {e}"}
            return {
                "finalOutput": response
            }
        else:
            rephrased = state.get("rephrasedQuery") or {}
            if isinstance(rephrased, dict):
                doubt = rephrased.get("doubt") or "Query could not be processed."
            else:
                doubt = str(rephrased)
            return {
                "finalOutput": {"response": doubt}
            }

    def _router(self, state: State):
        """
        Determines workflow routing based on the presence of a 'doubt' in the rephrased query.
        """
        rephrased = state.get("rephrasedQuery") or {}
        doubt = rephrased.get("doubt") if isinstance(rephrased, dict) else None
        if doubt is None:
            return "continue"
        else:
            return "interrupt"

    def createWorkflow(self):
        """
        Constructs and compiles the reporting tool workflow using StateGraph.
        """
        logger.info("compiling reporting workflow.")
        workflow = StateGraph(State)
        workflow.add_node("rephraseQuery", self._rephraseQuery)
        workflow.add_node("generateCode", self._generateCode)
        workflow.add_node("runInPythonSandbox", self._runInPythonSandbox)
        workflow.add_node("debugger", self._codeDebugger)
        workflow.add_node("debuggerPythonSandbox", self._runInPythonSandbox)
        workflow.add_node("formatJsonResponse", self._formatJsonResponse)
        workflow.add_edge(START, "rephraseQuery")
        workflow.add_conditional_edges("rephraseQuery", self._router, {"continue": "generateCode", "interrupt": "formatJsonResponse"})
        workflow.add_edge("generateCode", "runInPythonSandbox")
        workflow.add_conditional_edges("runInPythonSandbox", self._outputEvaluationRouter, {"pass": "formatJsonResponse", "fail": "debugger"})
        workflow.add_edge("debugger", "debuggerPythonSandbox")
        workflow.add_conditional_edges("debuggerPythonSandbox", self._debugRouter, {"debugger": "debugger", "formatJsonResponse": "formatJsonResponse"})
        workflow.add_edge("formatJsonResponse", END)
        workflow = workflow.compile()
        logger.info("reporting workflow compilation successful.")
        return workflow


def buildReportingWorkflow():
    """
    Builds the reporting workflow for generating reports.
    """
    graph = ReportingToolWorkflow()
    return graph.createWorkflow()