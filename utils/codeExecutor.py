"""
codeExecutor.py — sandboxed subprocess code execution with timeout and concurrency locks.

Runs LLM-generated code in an isolated subprocess with stdout/stderr capture,
hard resource limits, and environment cleanup. Employs a Redis Sorted Set (ZSET)
concurrency semaphore to limit executions to 2 per tenant.
"""

__all__ = ["replManager", "REPLManager", "_remove_code_fences"]


import sys
import os
import json
import subprocess
import time
import uuid
import re
from api.services.credits.creditService import creditService
from utils.logger import logger


def _remove_code_fences(code: str) -> str:
    """Strip ```python ... ``` fences if present."""
    return re.sub(r"^```(?:python)?\s*\n|\s*```\s*$", "", code.strip(), flags=re.MULTILINE)


class REPLManager:
    """
    Executes code strings inside a secure isolated subprocess.
    """

    def __init__(self, timeoutSeconds: int):
        self.timeoutSeconds = timeoutSeconds

    def run(self, codeString: str, projectId: str = None) -> str:
        if not projectId:
            return "Error: projectId is required for sandboxed code execution."

        if "```" in codeString:
            codeString = _remove_code_fences(codeString)

        # 1. Per-tenant concurrency limits: ZSET-based semaphore
        r = creditService._redis()
        sem_key = f"semaphore:{projectId}"
        slot_id = str(uuid.uuid4())
        now = time.time()

        acquired = False
        try:
            # Clean up slots older than self.timeoutSeconds + 5 seconds to prevent deadlocks
            r.zremrangebyscore(sem_key, 0, now - (self.timeoutSeconds + 5))
            count = r.zcard(sem_key)
            if count < 2:
                r.zadd(sem_key, {slot_id: now})
                r.expire(sem_key, self.timeoutSeconds + 5)
                acquired = True
            else:
                return f"Concurrency limit reached: Too many running code execution tasks for project '{projectId}'. Please try again in a few seconds."
        except Exception as e:
            logger.warning(f"Failed to check/acquire concurrency semaphore for project {projectId}: {e}")
            # Fallback to allow execution if Redis is down, preventing a hard crash for the user
            acquired = True

        # 2. Launch subprocess using isolated Python mode
        stdout = ""
        stderr = ""
        launcher_path = os.path.join(os.path.dirname(__file__), "sandbox_launcher.py")
        try:
            # Standard python paths + environment credentials needed for data fetching
            # passed explicitly during Popen startup
            app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            clean_env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": app_root + os.pathsep + os.environ.get("PYTHONPATH", ""),
                "REDIS_HOST": os.environ.get("REDIS_HOST", ""),
                "REDIS_PORT": os.environ.get("REDIS_PORT", ""),
                "REDIS_PASSWORD": os.environ.get("REDIS_PASSWORD", ""),
                "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
                "SUPABASE_KEY": os.environ.get("SUPABASE_KEY", ""),
                "FILE_URL": os.environ.get("FILE_URL", ""),
                "POLARS_MAX_THREADS": "2",
                "OPENBLAS_NUM_THREADS": "2",
            }
            proc = subprocess.Popen(
                [sys.executable, "-s", launcher_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=clean_env
            )

            payload = json.dumps({"projectId": projectId, "code": codeString})
            try:
                stdout, stderr = proc.communicate(input=payload, timeout=self.timeoutSeconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                stderr += f"\nExecution timed out after {self.timeoutSeconds} seconds.\n"
        except Exception as e:
            stderr = f"Error during subprocess execution: {e}\n"
        finally:
            if acquired:
                try:
                    r.zrem(sem_key, slot_id)
                except Exception as e:
                    logger.warning(f"Failed to release semaphore for project {projectId}: {e}")

        if stdout:
            return stdout
        return stderr


replManager = REPLManager(timeoutSeconds=30)