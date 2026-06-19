"""
transformationService.py

Service layer for AI-powered data transformations.
"""

__version__ = "1.0.0"
__author__ = "Platform Engineering"
__all__ = ["TransformationService", "transformationService", "get_saver"]


from langgraph.checkpoint.memory import InMemorySaver
from utils.exceptionHandler import CustomException
from api.models import TransformationAgentResponse
from api.commons import client, updateProjectModifiedAt
from utils.logger import logger
from typing import TYPE_CHECKING
import threading
import redis
import httpx
import orjson
import json
import os

if TYPE_CHECKING:
    from nubrix.components.transformationAgent import TransformationAgent
    from nubrix.components.transformationExecutor import TransformationExecutor


_saver_registry: dict[str, InMemorySaver] = {}
_registry_lock = threading.Lock()


def get_saver(projectId: str, transformationId: str) -> InMemorySaver:
    """
    Return the in-memory saver for a project/transformation pair.
    """
    key = f"{projectId}::{transformationId}"
    with _registry_lock:
        if key not in _saver_registry:
            _saver_registry[key] = InMemorySaver()
        return _saver_registry[key]


class TransformationService:
    """
    Encapsulates transformation persistence, agent calls, preview, and apply.
    """
    def __init__(self):
        """Initialize service dependencies."""
        self.supabase = client
        self._agent: "TransformationAgent | None" = None
        self._executor: "TransformationExecutor | None" = None

    @property
    def agent(self) -> "TransformationAgent":
        """Create the LLM agent lazily to avoid import-time model initialization."""
        if self._agent is None:
            from nubrix.components.transformationAgent import TransformationAgent
            self._agent = TransformationAgent()
        return self._agent

    @property
    def executor(self) -> "TransformationExecutor":
        """Create the transformation executor lazily."""
        if self._executor is None:
            from nubrix.components.transformationExecutor import TransformationExecutor
            self._executor = TransformationExecutor()
        return self._executor

    def _redis_client(self) -> redis.Redis:
        """Create a Redis client using environment credentials."""
        return redis.Redis(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ["REDIS_PORT"]),
            password=os.environ["REDIS_PASSWORD"],
        )

    def _sse(self, event: str, payload: dict) -> str:
        """Format an SSE event."""
        return f"event: {event}\ndata: {orjson.dumps(payload).decode('utf-8')}\n\n"

    def _metadata_url(self, projectId: str) -> str:
        """Build the project metadata URL."""
        fileUrlTemplate = os.environ.get("FILE_URL")
        if fileUrlTemplate:
            return fileUrlTemplate.format(projectId=projectId, fileName="metadata.json").replace(".parquet", "")
        return f"https://rnyvgjoacnpvscanmhnj.supabase.co/storage/v1/object/public/AnalyticsHub/{projectId}/metadata.json"

    def _preview_cache_key(self, projectId: str, transformationId: str, messageId: str, tableName: str) -> str:
        """Return the Redis preview cache key."""
        return f"{projectId}::transformation_preview::{transformationId}::{messageId}::{tableName}"

    async def _get_metadata(self, projectId: str) -> dict:
        """Fetch project metadata with a short Redis cache."""
        cacheKey = f"{projectId}::metadata.json"
        redisClient = None
        try:
            redisClient = self._redis_client()
            cached = redisClient.get(cacheKey)
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Metadata cache read failed for project {projectId}: {e}")

        async with httpx.AsyncClient(timeout=20) as httpClient:
            response = await httpClient.get(self._metadata_url(projectId))
            response.raise_for_status()
            metadata = response.json()

        if redisClient is not None:
            try:
                redisClient.set(cacheKey, json.dumps(metadata), ex=300)
            except Exception as e:
                logger.warning(f"Metadata cache write failed for project {projectId}: {e}")
        return metadata

    def _ensure_transformation(self, projectId: str, transformationId: str) -> dict:
        """Fetch a transformation and verify project ownership."""
        response = (
            self.supabase.table("transformations")
            .select("*")
            .eq("project_id", projectId)
            .eq("transformation_id", transformationId)
            .limit(1)
            .execute()
        )
        if not response.data:
            raise ValueError("Transformation not found.")
        return response.data[0]

    def _get_message(self, transformationId: str, messageId: str) -> dict:
        """Fetch a transformation message."""
        response = (
            self.supabase.table("transformation_messages")
            .select("*")
            .eq("transformation_id", transformationId)
            .eq("message_id", messageId)
            .limit(1)
            .execute()
        )
        if not response.data:
            raise ValueError("Transformation message not found.")
        return response.data[0]

    async def create_transformation(self, projectId: str, name: str, description: str | None) -> dict:
        """Insert into transformations table and return the row."""
        try:
            response = self.supabase.table("transformations").insert({
                "project_id": projectId,
                "transformation_name": name,
                "description": description,
            }).execute()
            updateProjectModifiedAt(projectId)
            return response.data[0]
        except Exception as e:
            exception = CustomException(e, statusCode=400, uiMessage="Failed to create transformation.")
            logger.error(exception)
            raise exception

    async def list_transformations(self, projectId: str) -> list[dict]:
        """Return all transformations for a project."""
        try:
            response = (
                self.supabase.table("transformations")
                .select("*")
                .eq("project_id", projectId)
                .order("created_at", desc=True)
                .execute()
            )
            return response.data or []
        except Exception as e:
            exception = CustomException(e, statusCode=400, uiMessage="Failed to list transformations.")
            logger.error(exception)
            raise exception

    async def get_messages(self, projectId: str, transformationId: str) -> list[dict]:
        """Fetch messages ordered by creation time."""
        try:
            self._ensure_transformation(projectId=projectId, transformationId=transformationId)
            response = (
                self.supabase.table("transformation_messages")
                .select("message_id, transformation_id, role, content, artifact, created_at")
                .eq("transformation_id", transformationId)
                .order("created_at", desc=False)
                .execute()
            )
            return response.data or []
        except Exception as e:
            exception = CustomException(e, statusCode=400, uiMessage=str(e))
            logger.error(exception)
            raise exception

    async def send_message_stream(self, projectId: str, transformationId: str, content: str):
        """
        Persist a user message, stream the agent response, and persist the assistant artifact.
        """
        userMessageId = None
        try:
            self._ensure_transformation(projectId=projectId, transformationId=transformationId)
            userResponse = self.supabase.table("transformation_messages").insert({
                "transformation_id": transformationId,
                "role": "user",
                "content": content,
            }).execute()
            userMessageId = userResponse.data[0].get("message_id") if userResponse.data else None

            metadata = await self._get_metadata(projectId=projectId)
            saver = get_saver(projectId=projectId, transformationId=transformationId)
            structuredResponse = None

            async for event in self.agent.astream(
                projectId=projectId,
                transformationId=transformationId,
                userMessage=content,
                metadata=metadata,
                saver=saver,
            ):
                if event.get("type") == "token":
                    yield self._sse("token", {"delta": event.get("delta", "")})
                elif event.get("type") == "done":
                    structuredResponse = event.get("structured")

            if not isinstance(structuredResponse, TransformationAgentResponse):
                fallback = "I could not generate a structured transformation. Please refine the request."
                assistantResponse = self.supabase.table("transformation_messages").insert({
                    "transformation_id": transformationId,
                    "role": "assistant",
                    "content": fallback,
                    "artifact": None,
                    "python_code": None,
                }).execute()
                messageId = assistantResponse.data[0].get("message_id") if assistantResponse.data else None
                yield self._sse("done", {"message_id": messageId, "content": fallback, "artifact": None})
                return

            artifact = {
                "type": "mermaid",
                "code": structuredResponse.mermaidCode,
                "is_approved": False,
            }
            assistantResponse = self.supabase.table("transformation_messages").insert({
                "transformation_id": transformationId,
                "role": "assistant",
                "content": structuredResponse.userFacingResponse,
                "artifact": artifact,
                "python_code": structuredResponse.pythonCode,
            }).execute()
            messageId = assistantResponse.data[0].get("message_id") if assistantResponse.data else None
            yield self._sse(
                "done",
                {
                    "message_id": messageId,
                    "content": structuredResponse.userFacingResponse,
                    "artifact": {"type": "mermaid", "code": structuredResponse.mermaidCode},
                },
            )
        except Exception as e:
            logger.error(f"Transformation stream failed after user message {userMessageId}: {e}")
            fallback = "I could not generate a transformation because the request failed. Please try again."
            try:
                assistantResponse = self.supabase.table("transformation_messages").insert({
                    "transformation_id": transformationId,
                    "role": "assistant",
                    "content": fallback,
                    "artifact": None,
                    "python_code": None,
                }).execute()
                messageId = assistantResponse.data[0].get("message_id") if assistantResponse.data else None
            except Exception:
                messageId = None
            yield self._sse("token", {"delta": ""})
            yield self._sse("done", {"message_id": messageId, "content": fallback, "artifact": None})

    async def approve_message(
        self,
        projectId: str,
        transformationId: str,
        messageId: str,
        newTransformedTableName: str,
    ) -> dict:
        """Execute an assistant message artifact and return a 10-row preview."""
        try:
            self._ensure_transformation(projectId=projectId, transformationId=transformationId)
            message = self._get_message(transformationId=transformationId, messageId=messageId)
            pythonCode = message.get("python_code")
            artifact = message.get("artifact")
            if not pythonCode or not artifact:
                raise ValueError("Only assistant messages with transformation artifacts can be approved.")
            previewRows, parquetBytes = self.executor.execute_and_preview(
                projectId=projectId,
                pythonCode=pythonCode,
                tableName=newTransformedTableName,
            )
            self._redis_client().set(
                name=self._preview_cache_key(projectId, transformationId, messageId, newTransformedTableName),
                value=parquetBytes,
                ex=900,
            )
            artifact["is_approved"] = True
            self.supabase.table("transformation_messages").update({
                "artifact": artifact,
                "new_transformed_table_name": newTransformedTableName,
            }).eq("message_id", messageId).execute()
            return {"data": previewRows}
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(e, statusCode=400, uiMessage=str(e))
            logger.error(exception)
            raise exception

    async def apply_transformation(
        self,
        projectId: str,
        transformationId: str,
        messageId: str,
        newTransformedTableName: str,
    ) -> dict:
        """Persist an approved transformation output and update metadata."""
        try:
            self._ensure_transformation(projectId=projectId, transformationId=transformationId)
            message = self._get_message(transformationId=transformationId, messageId=messageId)
            artifact = message.get("artifact")
            if not artifact:
                raise ValueError("Only messages with transformation artifacts can be applied.")

            redisClient = self._redis_client()
            cacheKey = self._preview_cache_key(projectId, transformationId, messageId, newTransformedTableName)
            parquetBytes = redisClient.get(cacheKey)
            if parquetBytes is None:
                raise ValueError("Preview cache expired. Approve the transformation again before applying.")

            self.executor.apply(
                projectId=projectId,
                parquetBytes=parquetBytes,
                tableName=newTransformedTableName,
            )
            latestArtifact = {
                "mermaid_code": artifact.get("code"),
                "message_id": messageId,
                "table_name": newTransformedTableName,
            }
            self.supabase.table("transformations").update({
                "latest_approved_artifact": latestArtifact,
            }).eq("transformation_id", transformationId).execute()
            artifact["is_approved"] = True
            self.supabase.table("transformation_messages").update({
                "artifact": artifact,
                "is_applied": True,
                "new_transformed_table_name": newTransformedTableName,
            }).eq("message_id", messageId).execute()
            redisClient.delete(cacheKey)
            updateProjectModifiedAt(projectId)
            return {
                "status": "200",
                "message": f"Transformation applied successfully. Table '{newTransformedTableName}' is now available.",
            }
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(e, statusCode=400, uiMessage=str(e))
            logger.error(exception)
            raise exception


transformationService = TransformationService()
