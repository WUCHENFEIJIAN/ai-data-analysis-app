from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ModelPresetRead(BaseModel):
    id: str
    label: str
    description: str
    provider: str
    api_base: str
    model: str
    requires_api_base: bool = False


class ModelPresetList(BaseModel):
    items: list[ModelPresetRead]


class ModelConfigurationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    preset_id: str
    provider: str
    display_name: str
    api_base: str
    model: str
    api_key_configured: bool
    api_key_hint: str | None = None
    updated_at: datetime | None = None


class ModelConfigurationUpdate(BaseModel):
    preset_id: str = Field(min_length=1, max_length=60)
    api_base: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=5000)
    model: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)
    clear_api_key: bool = False


class ModelConnectionTestRequest(BaseModel):
    preset_id: str = Field(min_length=1, max_length=60)
    api_base: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=5000)
    model: str | None = Field(default=None, max_length=200)


class ModelConnectionTestRead(BaseModel):
    ok: bool = True
    provider: str
    model: str
    latency_ms: int = Field(ge=0)
    message: str
