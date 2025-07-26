"""
codeExecutor.py

This module provides the REPLManager class for executing arbitrary code strings in a controlled environment with timeout and output/error capture.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["replManager"] 


from utils.initMethods import serializer, fetch_data, getDataForChart
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from utils.logger import logger
import contextlib
import traceback
import io

class REPLManager:
    """
    Manages a REPL environment for executing code strings with timeout and output/error capture.
    """
    def __init__(self, timeoutSeconds: int):
        """
        Initializes the REPLManager with a timeout for code execution.

        Args:
            timeoutSeconds (int): Maximum allowed execution time in seconds.
        """
        logger.info("Initializing REPLManager.")
        self.timeoutSeconds = timeoutSeconds

    @staticmethod
    def _executeCode(codeString, globalContext, stdout, stderr):
        """
        Executes the provided code string in the given global context, redirecting stdout and stderr.

        Args:
            codeString (str): The code to execute.
            globalContext (dict): The global context for code execution.
            stdout (io.StringIO): Stream to capture standard output.
            stderr (io.StringIO): Stream to capture standard error.
        """
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exec(codeString, globalContext)
            except Exception:
                traceback.print_exc(file=stderr)

    def run(self, codeString):
        """
        Runs the provided code string in a controlled environment with timeout.

        Args:
            codeString (str): The code to execute.

        Returns:
            str: The output or error message from code execution.
        """
        globalContext = {
            "fetch_data": fetch_data,
            "serializer": serializer,
            "getDataForChart": getDataForChart,
            "__name__": "__main__",
            "__builtins__": __builtins__,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._executeCode, codeString, globalContext, stdout, stderr)
            try:
                future.result(timeout=self.timeoutSeconds)
            except TimeoutError:
                stderr.write(f"Execution timed out after {self.timeoutSeconds} seconds.\n")
        output = stdout.getvalue()
        error = stderr.getvalue()
        if output and not error:
            return output
        elif output and error:
            return output
        elif not output and error:
            return error
        else:
            return output
        
replManager = REPLManager(timeoutSeconds = 7)