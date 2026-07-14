import os
import redis
from api.commons import client
from utils.logger import logger

class SyncSessionActivityTask:
    def execute(self) -> dict:
        redis_host = os.environ.get("REDIS_HOST", "localhost")
        redis_port = int(os.environ.get("REDIS_PORT", 6379))
        redis_password = os.environ.get("REDIS_PASSWORD", None)

        try:
            r = redis.Redis(host=redis_host, port=redis_port, password=redis_password)
            # Fetch all buffered last activity records
            activity_data = r.hgetall("session:last_activity")
            if not activity_data:
                return {"status": "SUCCESS", "message": "No activity updates to sync."}

            # Delete the hash first to prevent double-processing on next ticks
            r.delete("session:last_activity")

            count = 0
            for token_bytes, ts_bytes in activity_data.items():
                token = token_bytes.decode("utf-8") if isinstance(token_bytes, bytes) else token_bytes
                ts = ts_bytes.decode("utf-8") if isinstance(ts_bytes, bytes) else ts_bytes

                try:
                    # Update row in Sessions table
                    client.table("Sessions").update({"lastActivity": ts}).eq("accessToken", token).execute()
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to sync session lastActivity for token {token[:10]}...: {e}")
                    # Re-buffer in Redis on failure so we try again
                    r.hset("session:last_activity", token, ts)

            return {"status": "SUCCESS", "synced_count": count}
        except Exception as e:
            logger.error(f"Failed to execute SyncSessionActivityTask: {e}")
            return {"status": "FAILURE", "error": str(e)}
