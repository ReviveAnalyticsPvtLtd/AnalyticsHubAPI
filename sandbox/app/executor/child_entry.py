"""
child_entry.py

Bootstrap script executed as a child process for each sandbox execution.
Reads job configuration from a temp directory, applies resource limits,
injects approved helpers, and executes the user code.

Usage:
    python child_entry.py <job_dir>

The job_dir must contain:
    - code.py: the generated code to execute
    - config.json: {"projectId": "...", "timeoutSeconds": N, "memoryLimitMB": N, "allowedHelpers": [...]}
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = []

import json
import sys
import os
import traceback
from pathlib import Path

# Ensure the sandbox root is on sys.path so that `from app.*` imports resolve
_SANDBOX_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _SANDBOX_ROOT not in sys.path:
    sys.path.insert(0, _SANDBOX_ROOT)


def main():
    if len(sys.argv) < 2:
        print("Usage: child_entry.py <job_dir>", file=sys.stderr)
        sys.exit(1)

    job_dir = Path(sys.argv[1])
    config_path = job_dir / "config.json"
    code_path = job_dir / "code.py"

    if not config_path.exists() or not code_path.exists():
        print("Missing config.json or code.py in job directory", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    memory_limit_mb = config.get("memoryLimitMB", 512)
    timeout_seconds = config.get("timeoutSeconds", 7)
    cpu_limit = timeout_seconds + config.get("cpuLimitBufferSeconds", 2)
    project_id = config.get("projectId", "")
    allowed_helpers = config.get("allowedHelpers", ["fetch_data", "serializer"])

    from app.executor.resource_limits import apply_resource_limits
    apply_resource_limits(memory_limit_mb, cpu_limit)

    with open(code_path) as f:
        code = f.read()

    restricted_builtins = _build_restricted_builtins()

    global_context = {
        "__builtins__": restricted_builtins,
        "__name__": "__main__",
    }

    if "fetch_data" in allowed_helpers:
        from app.executor.helpers.fetch_data import fetch_data
        global_context["fetch_data"] = fetch_data

    if "serializer" in allowed_helpers:
        from app.executor.helpers.serializer import serializer
        global_context["serializer"] = serializer

    try:
        exec(code, global_context)
    except SystemExit:
        pass
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


def _build_restricted_builtins() -> dict:
    """Build a restricted builtins dict that removes dangerous functions."""
    import builtins

    allowed = [
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
        "callable", "chr", "classmethod", "complex", "delattr", "dict",
        "dir", "divmod", "enumerate", "filter", "float", "format",
        "frozenset", "getattr", "globals", "hasattr", "hash", "hex",
        "id", "int", "isinstance", "issubclass", "iter", "len", "list",
        "locals", "map", "max", "min", "next", "object", "oct", "ord",
        "pow", "print", "property", "range", "repr", "reversed", "round",
        "set", "setattr", "slice", "sorted", "staticmethod", "str",
        "sum", "super", "tuple", "type", "vars", "zip",
        "True", "False", "None",
        "Exception", "BaseException", "ValueError", "TypeError",
        "KeyError", "IndexError", "AttributeError", "RuntimeError",
        "StopIteration", "ZeroDivisionError", "OverflowError",
        "ImportError", "ModuleNotFoundError", "FileNotFoundError",
        "NotImplementedError", "NameError",
    ]

    restricted = {}
    for name in allowed:
        val = getattr(builtins, name, None)
        if val is not None:
            restricted[name] = val

    restricted["__import__"] = _restricted_import
    return restricted


_ALLOWED_IMPORTS = {"pandas", "numpy", "datetime", "math", "re", "json", "collections"}

_real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__


def _restricted_import(name, *args, **kwargs):
    """Only allow importing from a predefined allowlist."""
    top_level = name.split(".")[0]
    if top_level not in _ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed in sandbox")
    return _real_import(name, *args, **kwargs)


if __name__ == "__main__":
    main()
