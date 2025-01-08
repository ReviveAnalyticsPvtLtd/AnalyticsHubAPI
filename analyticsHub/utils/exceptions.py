import sys

class CustomException(Exception):
    def __init__(self, errorMessage):
        """
        Initialize a CustomException with a detailed error message.

        Args:
            errorMessage (str): The error message to be logged.
        """
        super().__init__(errorMessage)
        self.errorMessage = self.errorMessageDetail(errorMessage)

    @staticmethod
    def errorMessageDetail(error):
        """
        Generate a detailed error message.

        Args:
            error: The error object.

        Returns:
            str: A formatted error message including line number and filename.
        """
        _, _, exc_info = sys.exc_info()
        filename = exc_info.tb_frame.f_code.co_filename
        lineno = exc_info.tb_lineno
        errorMessage = "Error encountered in line no [{}], filename : [{}], saying [{}]".format(lineno, filename, error)
        return errorMessage

    def __str__(self) -> str:
        """Return the detailed error message."""
        return self.errorMessage