"""
parallelReportingToolWorkflow.py

This module defines the ParallelReportingToolWorkflow class, which orchestrates a multi-agent workflow
specifically optimized for parallel chart generation without query interruptions/doubts.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["buildParallelReportingWorkflow"]


from nubrix.components.queryRephraser import ParallelQueryRephaser
from nubrix.components.codeGenerator import CodeGenerator
from nubrix.components.codeDebugger import CodeDebugger
from langgraph.graph import StateGraph, START, END
from utils.codeExecutor import REPLManager
from typing_extensions import TypedDict
from utils.logger import logger
import json

class State(TypedDict):
    """
    State defines the structure for workflow state, holding all intermediate and data for parallel execution.
    """
    projectId: str
    inputQuery: str
    metadata: str
    rephrasedQuery: str
    generatedCode: str
    codeOutput: str
    finalOutput: dict

class ParallelReportingToolWorkflow:
    """
    ParallelReportingToolWorkflow manages parallel query generation without doubt interruptions.
    """
    def __init__(self):
        """Initializes the ParallelReportingToolWorkflow with its own chain instances."""
        logger.info("Initializing parallel agentic reporting workflow.")
        self.queryRephraseChain = ParallelQueryRephaser().getQueryRephraserChain()
        self.codeGeneratorChain = CodeGenerator().getCodeGeneratorChain()
        self.codeDebuggerChain = CodeDebugger().getCodeDebuggerChain()
        self.replManager = REPLManager(timeoutSeconds=7)

    def _rephraseQuery(self, state: State):
        """
        Rephrases the query using the parallel rephraser (no doubt field in output).
        Falls back to the raw input query if anything goes wrong.
        """
        try:
            response = self.queryRephraseChain.invoke({
                "query": state["inputQuery"],
                "metadata": state["metadata"]
            })
            # Ensure rephrasedOutput is populated
            if not response.get("rephrasedOutput"):
                response["rephrasedOutput"] = state["inputQuery"]
        except Exception as e:
            logger.warning(f"Query rephrase failed: {e}. Falling back to input query.")
            response = {
                "rephrasedOutput": state["inputQuery"],
            }
        return {
            "rephrasedQuery": response
        }
    
    def _generateCode(self, state: State):
        """
        Generates code for the rephrased query.
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
        Executes the generated code in Python sandbox.
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
        Determines if the code output is valid JSON.
        """
        try:
            _ = json.loads(state["codeOutput"])
            return "pass"
        except json.JSONDecodeError:
            return "fail"
        
    def _codeDebugger(self, state: State):
        """
        Invokes the code debugger chain.
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
        Formats the final output as a JSON response.
        """
        try:
            response = json.loads(state["codeOutput"])
        except Exception as e:
            response = {"error": f"Endpoint says: {e}"}
        return {
            "finalOutput": response
        }

    def createWorkflow(self):
        """
        Constructs and compiles the parallel reporting tool workflow.
        """
        logger.info("Compiling parallel reporting workflow.")
        workflow = StateGraph(State)
        workflow.add_node("rephraseQuery", self._rephraseQuery)
        workflow.add_node("generateCode", self._generateCode)
        workflow.add_node("runInPythonSandbox", self._runInPythonSandbox)
        workflow.add_node("debugger", self._codeDebugger)
        workflow.add_node("debuggerPythonSandbox", self._runInPythonSandbox)
        workflow.add_node("formatJsonResponse", self._formatJsonResponse)
        
        # Connect directly to generateCode (no conditional routing/interrupt on doubt)
        workflow.add_edge(START, "rephraseQuery")
        workflow.add_edge("rephraseQuery", "generateCode")
        workflow.add_edge("generateCode", "runInPythonSandbox")
        workflow.add_conditional_edges("runInPythonSandbox", self._outputEvaluationRouter, {"pass": "formatJsonResponse", "fail": "debugger"})
        workflow.add_edge("debugger", "debuggerPythonSandbox")
        workflow.add_edge("debuggerPythonSandbox", "formatJsonResponse")
        workflow.add_edge("formatJsonResponse", END)
        
        workflow = workflow.compile()
        logger.info("Parallel reporting workflow compilation successful.")
        return workflow


def buildParallelReportingWorkflow():
    """
    Builds the parallel reporting workflow.
    """
    graph = ParallelReportingToolWorkflow()
    return graph.createWorkflow()
