"""
llmOutputParser.py

Shared utility for parsing potentially messy LLM output into validated JSON objects.
Handles code fences, language tags, and extra prose around JSON.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["parseModelJsonOutput", "extractBetween", "stripFenceLanguage", "flattenContentBlocks"]


from langchain_experimental.utilities import PythonREPL
import orjson


def flattenContentBlocks(rawOutput: object) -> object:
    """
    Flatten LangChain multi-part message content into plain text.

    Recent Gemini models return `message.content` as a list of content blocks
    (e.g. ``[{"type": "text", "text": "...", "extras": {"signature": "..."}}]``)
    instead of a bare string. Without flattening, the parser stringifies the
    block's Python repr and then happily parses the *wrapper* back out, so
    callers receive ``{"type": ..., "text": ..., "extras": ...}`` instead of the
    model's JSON. Non-text blocks (reasoning/thought parts) are dropped.

    Args:
        rawOutput (object): Raw model output, possibly multi-part content.

    Returns:
        object: Concatenated text when the input was multi-part content,
            otherwise the input unchanged.
    """
    if isinstance(rawOutput, dict):
        if rawOutput.get("type") == "text" and isinstance(rawOutput.get("text"), str):
            return rawOutput["text"]
        return rawOutput
    if isinstance(rawOutput, list):
        parts: list[str] = []
        for block in rawOutput:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") not in (None, "text"):
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return rawOutput


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
        rawOutput (object): Raw model output (str, dict, list of content
            blocks, or None).
        stage (str): Label for error messages identifying the calling context.

    Returns:
        dict: Parsed JSON object.

    Raises:
        ValueError: If the output cannot be parsed into a JSON dict.
    """
    rawOutput = flattenContentBlocks(rawOutput)

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
        
        # Method 1: Strict JSON parsing
        try:
            parsed = orjson.loads(candidate.encode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        # Method 2: Python dict parsing (handles single quotes, trailing commas)
        try:
            import ast
            parsed_ast = ast.literal_eval(candidate)
            if isinstance(parsed_ast, dict):
                return parsed_ast
        except Exception:
            pass
            
    # Method 3: Attempt to extract anything that looks like a dict
    for candidate in candidates:
        candidate = stripFenceLanguage(candidate).strip()
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            dict_str = candidate[start:end+1]
            try:
                parsed = orjson.loads(dict_str.encode("utf-8"))
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                try:
                    import ast
                    parsed_ast = ast.literal_eval(dict_str)
                    if isinstance(parsed_ast, dict):
                        return parsed_ast
                except Exception:
                    pass

    preview = sanitized[:300].replace("\n", "\\n")
    raise ValueError(
        f"{stage} returned non-JSON output. Preview: {preview}"
    )
