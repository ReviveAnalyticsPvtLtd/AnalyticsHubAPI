"""Strict public request and response contracts for website visit tracking."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebsiteVisitRequest(_StrictModel):
    sessionId: UUID
    path: str = Field(min_length=1, max_length=2048)

    @field_validator("path")
    @classmethod
    def validatePath(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "?" in value
            or "#" in value
            or "\\" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("path must be a safe site-relative path")
        return value


class WebsiteVisitResponse(_StrictModel):
    success: Literal[True]
