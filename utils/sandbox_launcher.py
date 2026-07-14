import sys
import os
import resource
import json
import traceback

def main():
    # 1. Apply resource limits (CPU time and Virtual Memory)
    # CPU limit: 7 seconds soft, 8 seconds hard.
    # Virtual memory limit: 512 MB.
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (25, 30))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except Exception:
        pass

    # 2. Read inputs from stdin
    try:
        input_data = json.load(sys.stdin)
        projectId = input_data["projectId"]
        codeString = input_data["code"]
    except Exception as e:
        sys.stderr.write(f"Failed to read/parse input JSON from stdin: {e}\n")
        sys.exit(1)

    # 3. Setup data fetchers and sanitize imports
    from utils.initMethods import fetch_data, fetch_data_pl, scan_data, serializer

    def safe_fetch_data(targetProjectId, tableName, *args, **kwargs):
        if targetProjectId != projectId:
            raise PermissionError(f"Access denied: You can only query data for project '{projectId}' (requested '{targetProjectId}').")
        return fetch_data(targetProjectId, tableName, *args, **kwargs)

    def safe_fetch_data_pl(targetProjectId, tableName, *args, **kwargs):
        if targetProjectId != projectId:
            raise PermissionError(f"Access denied: You can only query data for project '{projectId}' (requested '{targetProjectId}').")
        return fetch_data_pl(targetProjectId, tableName, *args, **kwargs)

    def safe_scan_data(targetProjectId, tableName, *args, **kwargs):
        if targetProjectId != projectId:
            raise PermissionError(f"Access denied: You can only query data for project '{projectId}' (requested '{targetProjectId}').")
        return scan_data(targetProjectId, tableName, *args, **kwargs)

    # 4. Environment is NOT cleared — fetch_data* needs REDIS/SUPABASE env vars
    # at call time. Security is enforced via safe builtins (no open, no __import__
    # except whitelist, no subprocess access).

    # 5. Define safe builtins — allow __import__ for whitelisted modules only
    import importlib
    _ALLOWED_MODULES = frozenset({
        "polars", "pl", "json", "math", "datetime", "decimal",
        "collections", "itertools", "functools", "statistics",
        "numpy", "np", "pandas", "pd",
    })

    def _safe_import(name, *args, **kwargs):
        if name not in _ALLOWED_MODULES:
            raise ImportError(f"Import of '{name}' is not allowed in sandbox.")
        return __import__(name, *args, **kwargs)

    safe_builtins = {
        'abs': abs, 'all': all, 'any': any, 'bin': bin, 'bool': bool, 'bytearray': bytearray,
        'bytes': bytes, 'chr': chr, 'dict': dict, 'dir': dir, 'divmod': divmod, 'enumerate': enumerate,
        'filter': filter, 'float': float, 'format': format, 'frozenset': frozenset, 'getattr': getattr,
        'hasattr': hasattr, 'hash': hash, 'hex': hex, 'id': id, 'int': int, 'isinstance': isinstance,
        'issubclass': issubclass, 'iter': iter, 'len': len, 'list': list, 'locals': locals, 'map': map,
        'max': max, 'min': min, 'next': next, 'object': object, 'oct': oct, 'ord': ord, 'pow': pow,
        'print': print, 'range': range, 'repr': repr, 'reversed': reversed, 'round': round, 'set': set,
        'slice': slice, 'sorted': sorted, 'str': str, 'sum': sum, 'tuple': tuple, 'type': type,
        'zip': zip, '__import__': _safe_import,
    }

    globalContext = {
        "fetch_data": safe_fetch_data,
        "fetch_data_pl": safe_fetch_data_pl,
        "scan_data": safe_scan_data,
        "serializer": serializer,
        "__name__": "__main__",
        "__builtins__": safe_builtins,
    }

    try:
        import polars as pl
        globalContext["pl"] = pl
    except Exception:
        pass

    try:
        import pandas as pd
        globalContext["pd"] = pd
    except Exception:
        pass

    # 6. Execute user code
    try:
        exec(codeString, globalContext)
    except Exception:
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
