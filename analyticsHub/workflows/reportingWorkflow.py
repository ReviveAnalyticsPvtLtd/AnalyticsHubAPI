from ..components.failsafeAgent import FailsafeCodeGenerator
from ..components.queryRephraserAgent import QueryRephaser
from ..components.codeGeneratorAgent import CodeGenerator
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict
from ..components import replManager
import json

failsafeCodeGeneratorChain = FailsafeCodeGenerator().getFailsafeCodeGeneratorChain()
queryRephraseChain = QueryRephaser().getQueryRephraserChain()
codeGeneratorChain = CodeGenerator().getCodeGeneratorChain()

class State(TypedDict):
    projectId: str
    inputQuery: str
    metadata: str
    rephrasedQuery: str
    generatedCode: str
    codeOutput: str
    finalOutput: dict

class ReportingToolWorkflow:
    def __init__(self):
        pass
    def rephraseQuery(self, state: State):
        response = queryRephraseChain.invoke({
            "query": state["inputQuery"] + " /no_think",
            "metadata": state["metadata"]
        })
        return {
            "rephrasedQuery": response
        }
    def generateCode(self, state: State):
        response = codeGeneratorChain.invoke({
            "query": state["rephrasedQuery"] + " /no_think",
            "metadata": state["metadata"]
        })
        return {
            "generatedCode": f'fetch_data("{state["projectId"]}", '.join(response.split("fetch_data(")).replace('indent=4', 'default=serializer')
        }
    def runInPythonSandbox(self, state: State):
        code = "\n".join(state["generatedCode"].split("```")[-2].split("\n")[1:])
        response = replManager.run(code)
        return {
            "codeOutput": response
        }
    def outputEvaluationRouter(self, state: State):
        try:
            _ = json.loads(state["codeOutput"])
            return "pass"
        except json.JSONDecodeError:
            return "fail"
    def failsafe(self, state: State):
        response = failsafeCodeGeneratorChain.invoke({
            "user_query": state["rephrasedQuery"] + " /no_think",
            "metadata_context": state["metadata"],
            "code_with_errors": state["generatedCode"],
            "error_message": state["codeOutput"]
        })
        return {
            "generatedCode": response
        }
    def formatJsonResponse(self, state: State):
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
    def router(self, state: State):
        if state["rephrasedQuery"]["doubt"] == None:
            return "continue"
        else:
            return "interrupt"
    def createWorkflow(self):
        workflow = StateGraph(State)
        workflow.add_node("rephraseQuery", self.rephraseQuery)
        workflow.add_node("generateCode", self.generateCode)
        workflow.add_node("runInPythonSandbox", self.runInPythonSandbox)
        workflow.add_node("failsafe", self.failsafe)
        workflow.add_node("failsafePythonSandbox", self.runInPythonSandbox)
        workflow.add_node("formatJsonResponse", self.formatJsonResponse)
        workflow.add_edge(START, "rephraseQuery")
        workflow.add_conditional_edges("rephraseQuery", self.router, {"continue": "generateCode", "interrupt": "formatJsonResponse"})
        workflow.add_edge("generateCode", "runInPythonSandbox")
        workflow.add_conditional_edges("runInPythonSandbox", self.outputEvaluationRouter, {"pass": "formatJsonResponse", "fail": "failsafe"})
        workflow.add_edge("failsafe", "failsafePythonSandbox")
        workflow.add_edge("failsafePythonSandbox", "formatJsonResponse")
        workflow.add_edge("formatJsonResponse", END)
        workflow = workflow.compile()
        return workflow
    
graph = ReportingToolWorkflow()
reportingToolWorkflow = graph.createWorkflow()