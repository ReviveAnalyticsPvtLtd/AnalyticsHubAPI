"""
llmOutputParser.py

Shared utility for parsing potentially messy LLM output into validated JSON objects.
Handles code fences, language tags, and extra prose around JSON.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["parseModelJsonOutput", "extractBetween", "stripFenceLanguage"]


from langchain_experimental.utilities import PythonREPL
import orjson


def extractBetween(text: str, opening: str, closing: str) -> str | None:
    """Extract substring from first opening to last closing token."""
    startIdx = text.find(opening)
    endIdx = text.rfind(closing)
    if startIdx == -1 or endIdx == -1 or endIdx <= startIdx:
        return None
    return text[startIdx : endIdx + 1].strip()


def stripFenceLanguage(block: str) -> str:
    """Remove fence language tags like `json` from fenced blocks."""
    cleaned = block.strip()
    lowered = cleaned.lower()
    if lowered.startswith("json\n"):
        return cleaned.split("\n", 1)[1].strip()
    if lowered.startswith("json "):
        return cleaned[5:].strip()
    if "\n" in cleaned:
        firstLine, rest = cleaned.split("\n", 1)
        if firstLine.strip().lower() in {"json", "python", "javascript", "js"}:
            return rest.strip()
    return cleaned


def parseModelJsonOutput(rawOutput: object, stage: str) -> dict:
    """
    Parse potentially messy model output into a JSON object.

    Handles code fences, language tags, and extra prose around JSON.

    Args:
        rawOutput (object): Raw model output (str, dict, or None).
        stage (str): Label for error messages identifying the calling context.

    Returns:
        dict: Parsed JSON object.

    Raises:
        ValueError: If the output cannot be parsed into a JSON dict.
    """
    if isinstance(rawOutput, dict):
        return rawOutput
    if rawOutput is None:
        raise ValueError(f"{stage} model output is empty.")

    rawText = str(rawOutput).strip()
    if not rawText:
        raise ValueError(f"{stage} model output is blank.")

    sanitized = PythonREPL().sanitize_input(query=rawText).strip()
    candidates: list[str] = [rawText, sanitized]

    for baseText in (rawText, sanitized):
        if "```" in baseText:
            for block in baseText.split("```"):
                block = stripFenceLanguage(block)
                if block:
                    candidates.append(block)
        for opening, closing in (("{", "}"), ("[", "]")):
            sliced = extractBetween(baseText, opening, closing)
            if sliced:
                candidates.append(sliced)

    seen: set[str] = set()
    for candidate in candidates:
        candidate = stripFenceLanguage(candidate).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = orjson.loads(candidate.encode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError(f"{stage} output must be a JSON object.")
            return parsed
        except Exception:
            continue

    preview = sanitized[:300].replace("\n", "\\n")
    raise ValueError(
        f"{stage} returned non-JSON output. Preview: {preview}"
    )
