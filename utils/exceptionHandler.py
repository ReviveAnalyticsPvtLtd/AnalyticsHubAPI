"""
exceptionHandler.py

This module defines application-specific exceptions with detailed context
for debugging and logging.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["CustomException"]


from fastapi import HTTPException
import traceback

class CustomException(Exception):
    """
    CustomException provides detailed error information including
    the line number, file name, and original error message where
    the exception was raised.
    """
    def __init__(self, exception: Exception, statusCode: int = 500, uiMessage: str = "Something went wrong. Please try again later.") -> None:
        """
        Initialize the CustomException with detailed traceback information.

        Args:
            exception (Exception): The original exception to wrap.
        """
        self.statusCode = statusCode
        self.uiMessage = uiMessage
        tbList = traceback.extract_tb(exception.__traceback__) if exception.__traceback__ else []
        if tbList:
            tb = tbList[-1]
            lineNumber = tb.lineno
            fileName = tb.filename
            customErrorMessage = "Error encountered in line no: [{lineNumber}], filename: [{fileName}], saying: [{errorMessage}]"
            self.customErrorMessage = customErrorMessage.format(
                lineNumber = lineNumber,
                fileName = fileName,
                errorMessage = str(exception)
            )
        else:
            self.customErrorMessage = self.uiMessage
        super().__init__(self.customErrorMessage)

def raiseHttpException(e: CustomException):
    raise HTTPException(
        status_code=e.statusCode,
        detail={
            "status": e.statusCode,
            "message": e.uiMessage,
            "backendLogMessage": e.customErrorMessage,
        }
    )