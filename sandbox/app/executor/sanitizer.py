"""
sanitizer.py

Code sanitization: strip fences, validate size, reject empty input.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["sanitize_code"]


class CodeSanitizationError(Exception):
    pass


def sanitize_code(code: str, max_code_bytes: int) -> str:
    """
    Sanitize incoming code string:
    1. Strip markdown code fences if present.
    2. Strip leading/trailing whitespace.
    3. Reject empty code.
    4. Reject code exceeding max_code_bytes.

    Returns cleaned code string ready for execution.
    """
    if "```" in code:
        parts = code.split("```")
        if len(parts) >= 3:
            inner = parts[-2]
            lines = inner.split("\n")
            if lines and lines[0].strip().isalpha():
                lines = lines[1:]
            code = "\n".join(lines)

    code = code.strip()

    if not code:
        raise CodeSanitizationError("Empty code submitted")

    code_bytes = len(code.encode("utf-8"))
    if code_bytes > max_code_bytes:
        raise CodeSanitizationError(
            f"Code size ({code_bytes} bytes) exceeds limit ({max_code_bytes} bytes)"
        )

    return code
