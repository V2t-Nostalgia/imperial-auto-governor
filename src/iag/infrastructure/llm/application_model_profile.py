"""Named model selections owned by a first-party Application."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApplicationModelProfile(BaseModel):
    """A reusable player-named binding from one Application to one model."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=96)
    display_name: str = Field(min_length=1, max_length=120)
    application_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    pool_id: str = Field(min_length=1, max_length=96)
    model_id: str = Field(min_length=1, max_length=120)
    request_options: dict[str, Any] = Field(default_factory=dict)
    application_options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_json_options(self) -> "ApplicationModelProfile":
        """Reject values that cannot survive explicit JSON persistence."""
        json.dumps(self.request_options, ensure_ascii=False)
        json.dumps(self.application_options, ensure_ascii=False)
        return self
