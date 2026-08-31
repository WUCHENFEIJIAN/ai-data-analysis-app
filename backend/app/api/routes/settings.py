import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.core.errors import AppError
from app.schemas.settings import (
    ModelConfigurationRead,
    ModelConfigurationUpdate,
    ModelConnectionTestRead,
    ModelConnectionTestRequest,
    ModelPresetList,
    ModelPresetRead,
)
from app.services.model_config import (
    MODEL_PRESETS,
    get_active,
    mask_api_key,
    provider_from_record,
    upsert_active,
)

router = APIRouter(prefix="/settings", tags=["settings"])
SessionDependency = Annotated[Session, Depends(get_session)]


def serialize(record) -> ModelConfigurationRead | None:
    if record is None:
        return None
    return ModelConfigurationRead(
        preset_id=record.preset_id,
        provider=record.provider,
        display_name=record.display_name,
        api_base=record.api_base,
        model=record.model,
        api_key_configured=bool(record.api_key),
        api_key_hint=mask_api_key(record.api_key),
        updated_at=record.updated_at,
    )


@router.get("/models", response_model=ModelPresetList)
def list_model_presets() -> ModelPresetList:
    return ModelPresetList(items=[ModelPresetRead(**preset) for preset in MODEL_PRESETS])


@router.get("/model", response_model=ModelConfigurationRead | None)
def get_model_configuration(session: SessionDependency) -> ModelConfigurationRead | None:
    return serialize(get_active(session))


@router.put("/model", response_model=ModelConfigurationRead)
def update_model_configuration(
    payload: ModelConfigurationUpdate,
    request: Request,
    session: SessionDependency,
) -> ModelConfigurationRead:
    try:
        record = upsert_active(
            session,
            preset_id=payload.preset_id,
            api_base=payload.api_base,
            api_key=payload.api_key,
            model=payload.model,
            display_name=payload.display_name,
            clear_api_key=payload.clear_api_key,
        )
    except AppError:
        raise
    session.commit()
    provider = provider_from_record(record, request.app.state.settings)
    if provider is None:
        raise AppError(
            "模型配置已保存，但 API 地址、模型名或 API Key 尚未完整填写",
            "model_not_configured",
            400,
        )
    request.app.state.llm_provider = provider
    return serialize(record)


@router.post("/model/test", response_model=ModelConnectionTestRead)
async def test_model_connection(
    payload: ModelConnectionTestRequest,
    request: Request,
    session: SessionDependency,
) -> ModelConnectionTestRead:
    preset = next((item for item in MODEL_PRESETS if item["id"] == payload.preset_id), None)
    if preset is None:
        raise AppError("model_preset_not_found", "不支持的模型预设", 400)
    active = get_active(session)
    api_key = (payload.api_key or "").strip()
    if not api_key and active is not None and active.preset_id == payload.preset_id:
        api_key = active.api_key
    api_base = (payload.api_base if payload.api_base is not None else str(preset["api_base"])).strip()
    model = (payload.model if payload.model is not None else str(preset["model"])).strip()
    if not api_base or not model or not api_key:
        raise AppError("model_test_not_configured", "请填写 API 地址、模型名称和 API Key 后再测试", 400)
    record = type(
        "ConnectionTestConfiguration",
        (),
        {
            "provider": str(preset["provider"]),
            "api_base": api_base,
            "api_key": api_key,
            "model": model,
        },
    )()
    provider = provider_from_record(record, request.app.state.settings)
    if provider is None:
        raise AppError("model_test_not_configured", "模型连接参数不完整", 400)
    started = time.perf_counter()
    await provider.text_chat([{"role": "user", "content": "Reply with OK only."}])
    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    return ModelConnectionTestRead(
        provider=str(preset["provider"]),
        model=model,
        latency_ms=latency_ms,
        message=f"连接成功，模型 {model} 已响应（{latency_ms} ms）",
    )
