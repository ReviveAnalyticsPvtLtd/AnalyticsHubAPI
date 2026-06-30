"""
reportingToolWorkflow.py

This module defines the ReportingToolWorkflow class, which orchestrates a multi-agent workflow for query rephrasing, code generation, code execution, error handling, and response formatting in a reporting tool context. It leverages LangGraph for workflow management and integrates with various code and query processing components.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["buildReportingWorkflow"]


from nubrix.components.queryRephraser import QueryRephaser
from nubrix.components.codeGenerator import CodeGenerator
from nubrix.components.codeDebugger import CodeDebugger
from langgraph.graph import StateGraph, START, END
from utils.codeExecutor import REPLManager
from typing_extensions import TypedDict
from utils.logger import logger
import json

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

class ReportingToolWorkflow:
    """
    ReportingToolWorkflow manages the multi-step process of query rephrasing, code generation, execution, error handling, and response formatting for the reporting tool.
    """
    def __init__(self):
        """
        Initializes the ReportingToolWorkflow with its own chain instances and replManager
        to ensure thread-safe parallel execution without shared state.
        """
        logger.info("Initializing multi-agentic reporting workflow.")
        self.queryRephraseChain = QueryRephaser().getQueryRephraserChain()
        self.codeGeneratorChain = CodeGenerator().getCodeGeneratorChain()
        self.codeDebuggerChain = CodeDebugger().getCodeDebuggerChain()
        self.replManager = REPLManager(timeoutSeconds=7)

    def _rephraseQuery(self, state: State):
        """
        Rephrases the user's input query using the query rephraser chain.

        Args:
            state (State): The current workflow state.

        Returns:
            dict: Updated state with the rephrased query.
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

        Args:
            state (State): The current workflow state.

        Returns:
            dict: Updated state with the generated code.
        """
        response = self.codeGeneratorChain.invoke({
            "query": state["rephrasedQuery"],
            "metadata": state["metadata"]
        })
        return {
            "generatedCode": f'fetch_data("{state["projectId"]}", '.join(response.split("fetch_data(")).replace('indent=4', 'default=serializer')
        }
    
    def _runInPythonSandbox(self, state: State):
        """
        Executes the generated code in a Python sandbox environment and captures the output.

        Args:
            state (State): The current workflow state.

        Returns:
            dict: Updated state with the code execution output.
        """
        if "```" in state["generatedCode"]:
            code = "\n".join(state["generatedCode"].split("```")[-2].split("\n")[1:])
        else:
            code = state["generatedCode"].split("</think>")[-1]
        response = self.replManager.run(code)
        return {
            "codeOutput": response
        }
    
    def _outputEvaluationRouter(self, state: State):
        """
        Determines if the code output is valid JSON. Routes workflow based on output validity.

        Args:
            state (State): The current workflow state.

        Returns:
            str: "pass" if output is valid JSON, otherwise "fail".
        """
        try:
            _ = json.loads(state["codeOutput"])
            return "pass"
        except json.JSONDecodeError:
            return "fail"
        
    def _codeDebugger(self, state: State):
        """
        Invokes the code debugger chain to handle errors in code generation or execution.

        Args:
            state (State): The current workflow state.

        Returns:
            dict: Updated state with the corrected/generated code.
        """
        response = self.codeDebuggerChain.invoke({
            "user_query": state["rephrasedQuery"],
            "metadata_context": state["metadata"],
            "code_with_errors": state["generatedCode"],
            "error_message": state["codeOutput"]
        })
        return {
            "generatedCode": response
        }
    
    def _formatJsonResponse(self, state: State):
        """
        Formats the final output as a JSON response, handling errors if present.

        Args:
            state (State): The current workflow state.

        Returns:
            dict: Updated state with the final output.
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
            return {
                "finalOutput": {"response": state["rephrasedQuery"]["doubt"]}
            }

    def _router(self, state: State):
        """
        Determines workflow routing based on the presence of a 'doubt' in the rephrased query.

        Args:
            state (State): The current workflow state.

        Returns:
            str: "continue" if no doubt, otherwise "interrupt".
        """
        if state["rephrasedQuery"]["doubt"] is None:
            return "continue"
        else:
            return "interrupt"

    def createWorkflow(self):
        """
        Constructs and compiles the reporting tool workflow using StateGraph.

        Returns:
            StateGraph: The compiled workflow graph ready for execution.
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
        workflow.add_edge("debuggerPythonSandbox", "formatJsonResponse")
        workflow.add_edge("formatJsonResponse", END)
        workflow = workflow.compile()
        logger.info("reporting workflow compilation successful.")
        return workflow


def buildReportingWorkflow():
    """
    Builds the reporting workflow for generating reports.

    Args:
        None

    Returns:
        StateGraph: The compiled workflow graph ready for execution.
    """
    graph = ReportingToolWorkflow()
    return graph.createWorkflow()