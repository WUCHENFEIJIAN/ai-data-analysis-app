from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.factory import create_provider
from app.models import ModelConfiguration

MODEL_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "chatgpt",
        "label": "ChatGPT",
        "description": "OpenAI 官方 Chat Completions",
        "provider": "openai_compatible",
        "api_base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "requires_api_base": False,
    },
    {
        "id": "claude",
        "label": "Claude / Claude Code",
        "description": "Anthropic Messages API",
        "provider": "anthropic",
        "api_base": "https://api.anthropic.com",
        "model": "claude-3-7-sonnet-latest",
        "requires_api_base": False,
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "description": "DeepSeek 官方 OpenAI-compatible API",
        "provider": "openai_compatible",
        "api_base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "requires_api_base": False,
    },
    {
        "id": "qwen",
        "label": "通义千问",
        "description": "阿里云 DashScope 兼容模式",
        "provider": "openai_compatible",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "requires_api_base": False,
    },
    {
        "id": "zhipu",
        "label": "智谱清言",
        "description": "智谱 BigModel API",
        "provider": "openai_compatible",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "requires_api_base": False,
    },
    {
        "id": "custom",
        "label": "中转站 / 自定义",
        "description": "填写兼容 OpenAI 或 Anthropic 的中转站地址",
        "provider": "openai_compatible",
        "api_base": "",
        "model": "",
        "requires_api_base": True,
    },
)


def get_preset(preset_id: str) -> dict[str, Any]:
    for preset in MODEL_PRESETS:
        if preset["id"] == preset_id:
            return dict(preset)
    raise AppError("model_preset_not_found", "不支持的模型预设", 400)


def get_active(session: Session) -> ModelConfiguration | None:
    return session.scalar(
        select(ModelConfiguration).where(ModelConfiguration.id == 1)
    )


def upsert_active(
    session: Session,
    *,
    preset_id: str,
    api_base: str | None,
    api_key: str | None,
    model: str | None,
    display_name: str | None,
    clear_api_key: bool = False,
) -> ModelConfiguration:
    preset = get_preset(preset_id)
    record = get_active(session)
    if record is None:
        record = ModelConfiguration(id=1)
        session.add(record)
    record.preset_id = preset_id
    record.provider = str(preset["provider"])
    record.display_name = display_name or str(preset["label"])
    record.api_base = (api_base if api_base is not None else str(preset["api_base"])).strip()
    record.model = (model if model is not None else str(preset["model"])).strip()
    if clear_api_key:
        record.api_key = ""
    elif api_key is not None and api_key.strip():
        record.api_key = api_key.strip()
    if not record.api_base or not record.model or not record.api_key:
        # Saving an incomplete profile is useful for filling the form in stages,
        # but it must not silently replace a working provider.
        return record
    return record


def provider_from_record(record: ModelConfiguration | None, settings: Settings):
    if record is None:
        return None
    values: Mapping[str, Any] = {
        "provider": record.provider,
        "api_base": record.api_base,
        "api_key": record.api_key,
        "model": record.model,
    }
    if not all(str(values[key]).strip() for key in ("api_base", "api_key", "model")):
        return None
    return create_provider(
        provider=str(values["provider"]),
        api_base=str(values["api_base"]),
        api_key=str(values["api_key"]),
        model=str(values["model"]),
        settings=settings,
    )


def mask_api_key(api_key: str) -> str | None:
    if not api_key:
        return None
    return f"••••{api_key[-4:]}" if len(api_key) > 4 else "••••"
