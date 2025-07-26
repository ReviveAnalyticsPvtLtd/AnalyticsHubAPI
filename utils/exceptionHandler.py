"""
exceptionHandler.py

This module defines application-specific exceptions with detailed context
for debugging and logging.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["CustomException"]


import traceback

class CustomException(Exception):
    """
    CustomException provides detailed error information including
    the line number, file name, and original error message where
    the exception was raised.
    """
    def __init__(self, exception: Exception) -> None:
        """
        Initialize the CustomException with detailed traceback information.

        Args:
            exception (Exception): The original exception to wrap.
        """
        tb = traceback.extract_tb(tb = exception.__traceback__)[-1]
        customErrorMessage = "Error encountered in line no [{lineNumber}], filename : [{fileName}], saying [{errorMessage}]"
        customErrorMessage = customErrorMessage.format(
            lineNumber = tb.lineno,
            fileName = tb.filename,
            errorMessage = str(exception)
        )
        super().__init__(customErrorMessage)