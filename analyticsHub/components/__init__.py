from ..utils.functions import readYaml
import contextlib
import traceback
import io

class REPLManager:
    def __init__(self):
        params = readYaml("params.yaml")
        self.__persistentGlobals = {"__name__": "__main__"}
        self.__stdout = io.StringIO()
        self.__stderr = io.StringIO()
        exec(params.get("redisFunctionCode"), self.__persistentGlobals)
        exec(params.get("panelChartDataCode"), self.__persistentGlobals)
        exec(params.get("jsonSerializer"), self.__persistentGlobals)
        self.__globals = dict(self.__persistentGlobals)

    def run(self, codeString):
        with contextlib.redirect_stdout(self.__stdout), contextlib.redirect_stderr(self.__stderr):
            try:
                exec(codeString, self.__globals)
            except Exception:
                traceback.print_exc(file=self.__stderr)
        output, error = self.__stdout.getvalue(), self.__stderr.getvalue()
        self.__stdout.truncate(0)
        self.__stdout.seek(0)
        self.__stderr.truncate(0)
        self.__stderr.seek(0)
        self.__globals = dict(self.__persistentGlobals)
        if (output != "") & (error == ""):
            return output
        elif (output != "") & (error != ""):
            return output
        elif (output == "") & (error != ""):
            return error
        else:
            return output

replManager = REPLManager()