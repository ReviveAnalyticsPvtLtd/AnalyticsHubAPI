from ..utils.functions import serializer, fetch_data, getDataForChart
import contextlib
import traceback
import io

class REPLManager:
    def __init__(self):
        self.__persistentGlobals = {
            "fetch_data": fetch_data,
            "serializer": serializer,
            "getDataForChart": getDataForChart,
            "__name__": "__main__",
            "__builtins__": __builtins__
        }
        self.__stdout = io.StringIO()
        self.__stderr = io.StringIO()
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