from ..components.codeGeneratorAgent import CodeGenerator
from ..components.queryRephraserAgent import QueryRephaser
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict
from ..components import replManager
import json

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
            "query": state["inputQuery"],
            "metadata": state["metadata"]
        })
        return {
            "rephrasedQuery": response
        }
    def generateCode(self, state: State):
        response = codeGeneratorChain.invoke({
            "query": state["rephrasedQuery"],
            "metadata": state["metadata"]
        })
        return {
            "generatedCode": f'fetch_data("{state["projectId"]}", '.join(response.split("fetch_data(")).replace("import pandas", "import fireducks.pandas").replace('indent=4', 'default=serializer')
        }
    def runInPythonSandbox(self, state: State):
        code = "\n".join(state["generatedCode"].split("```")[-2].split("\n")[1:])
        response = replManager.manager.get(state["projectId"]).run(code)
        return {
            "codeOutput": response
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
        workflow.add_node("formatJsonResponse", self.formatJsonResponse)
        workflow.add_edge(START, "rephraseQuery")
        workflow.add_conditional_edges("rephraseQuery", self.router, {"continue": "generateCode", "interrupt": "formatJsonResponse"})
        workflow.add_edge("generateCode", "runInPythonSandbox")
        workflow.add_edge("runInPythonSandbox", "formatJsonResponse")
        workflow.add_edge("formatJsonResponse", END)
        workflow = workflow.compile()
        return workflow
    
graph = ReportingToolWorkflow()
workflow = graph.createWorkflow()