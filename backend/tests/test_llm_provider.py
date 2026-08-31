import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.errors import LLMError
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.schemas.actions import AgentActionResponse, PlanningActionResponse


@pytest.mark.asyncio
async def test_openai_provider_sends_schema_and_parses_action() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer secret-value"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "ask_user",
                                    "question": "Which definition?",
                                    "reason": "Metric is ambiguous",
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "secret-value", "test-model", client=client
        )
        result = await provider.structured_chat(
            [{"role": "user", "content": "hello"}], PlanningActionResponse
        )

    assert result.root.action == "ask_user"
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["max_tokens"] == 8192
    assert "thinking" not in captured


@pytest.mark.asyncio
async def test_openai_provider_can_disable_model_thinking() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1",
            "key",
            "model",
            client=client,
            thinking_enabled=False,
        )
        assert await provider.text_chat([{"role": "user", "content": "hello"}]) == "ok"

    assert captured["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_openai_provider_retries_rate_limit() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, request=request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=1, client=client
        )
        assert await provider.text_chat([{"role": "user", "content": "hello"}]) == "ok"

    assert attempts == 2


@pytest.mark.asyncio
async def test_openai_provider_retries_invalid_structured_output() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        content = (
            "not-json"
            if attempts == 1
            else json.dumps(
                {
                    "action": "ask_user",
                    "question": "Which metric?",
                    "reason": "The request is ambiguous",
                }
            )
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=1, client=client
        )
        result = await provider.structured_chat([], PlanningActionResponse)

    assert result.root.action == "ask_user"
    assert attempts == 2


@pytest.mark.asyncio
async def test_openai_provider_preserves_final_structured_validation_details() -> None:
    content = '{"action":"ask_user","question":"Which metric?"}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=0, client=client
        )
        with pytest.raises(LLMError) as caught:
            await provider.structured_chat([], PlanningActionResponse)

    assert caught.value.code == "llm_invalid_output"
    assert caught.value.details["schema"] == "PlanningActionResponse"
    assert "reason: Field required" in caught.value.details["validation"]
    assert len(caught.value.details["candidate_fingerprint"]) == 64


@pytest.mark.asyncio
async def test_openai_provider_extracts_json_from_markdown_without_retrying() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "Here is the result:\n```json\n"
                                '{"action":"ask_user","question":"Which metric?",'
                                '"reason":"Ambiguous"}\n```'
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=1, client=client
        )
        result = await provider.structured_chat([], PlanningActionResponse)

    assert result.root.action == "ask_user"
    assert attempts == 1


@pytest.mark.asyncio
async def test_openai_provider_sends_validation_details_on_repair() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        body = json.loads(request.content)
        if attempts == 1:
            content = '{"action":"ask_user","question":"Which metric?"}'
        else:
            assert "reason: Field required" in body["messages"][-1]["content"]
            content = json.dumps(
                {
                    "action": "ask_user",
                    "question": "Which metric?",
                    "reason": "Ambiguous",
                }
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=1, client=client
        )
        result = await provider.structured_chat([], PlanningActionResponse)

    assert result.root.action == "ask_user"
    assert attempts == 2


@pytest.mark.asyncio
async def test_openai_provider_reports_truncated_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"action":"ask_user"'}, "finish_reason": "length"}
                ]
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=0, client=client
        )
        with pytest.raises(LLMError, match="truncated") as caught:
            await provider.structured_chat([], PlanningActionResponse)

    assert caught.value.code == "llm_output_truncated"
    assert caught.value.details["schema"] == "PlanningActionResponse"
    assert caught.value.details["finish_reason"] == "length"
    assert len(caught.value.details["candidate_fingerprint"]) == 64


@pytest.mark.asyncio
async def test_openai_provider_treats_parseable_truncated_output_as_truncation() -> None:
    attempts = 0
    content = json.dumps(
        {
            "action": "ask_user",
            "question": "Which metric?",
            "reason": "Ambiguous",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": content}, "finish_reason": "length"}
                ],
                "usage": {"completion_tokens": 4096},
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=2, client=client
        )
        with pytest.raises(LLMError, match="truncated") as caught:
            await provider.structured_chat([], PlanningActionResponse)

    assert attempts == 1
    assert caught.value.code == "llm_output_truncated"
    assert caught.value.details["finish_reason"] == "length"
    assert caught.value.details["completion_tokens"] == 4096


@pytest.mark.asyncio
async def test_openai_provider_sends_per_call_output_budget() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "ask_user",
                                    "question": "Which metric?",
                                    "reason": "Ambiguous",
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", client=client
        )
        await provider.structured_chat(
            [], PlanningActionResponse, max_output_tokens=4096
        )

    assert captured["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_openai_provider_falls_back_when_json_schema_is_unavailable() -> None:
    response_formats: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        response_formats.append(body["response_format"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "This response_format type is unavailable now",
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )
        assert "JSON Schema" in body["messages"][-1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "ask_user",
                                    "question": "Which metric?",
                                    "reason": "The request is ambiguous",
                                }
                            )
                        }
                    }
                ]
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=0, client=client
        )
        result = await provider.structured_chat([], PlanningActionResponse)

    assert result.root.action == "ask_user"
    assert [item["type"] for item in response_formats] == ["json_schema", "json_object"]


@pytest.mark.asyncio
async def test_openai_provider_falls_back_for_invalid_relay_schema() -> None:
    response_formats: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        response_formats.append(body["response_format"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "Invalid schema for response_format 'PlanningAction': "
                            "Missing 'alt'."
                        ),
                        "type": "invalid_request_error",
                    }
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "ask_user",
                                    "question": "Which metric?",
                                    "reason": "The request is ambiguous",
                                }
                            )
                        }
                    }
                ]
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://relay.example/v1", "key", "model", max_retries=0, client=client
        )
        result = await provider.structured_chat([], PlanningActionResponse)

    assert result.root.action == "ask_user"
    assert [item["type"] for item in response_formats] == ["json_schema", "json_object"]


@pytest.mark.asyncio
async def test_openai_provider_reports_authentication_errors_without_retrying() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "bad-key", "model", max_retries=2, client=client
        )
        with pytest.raises(LLMError, match="authentication failed"):
            await provider.text_chat([])

    assert attempts == 1


@pytest.mark.asyncio
async def test_openai_provider_reports_insufficient_balance_without_retrying() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            402,
            json={
                "error": {
                    "message": "Insufficient Balance",
                    "code": "invalid_request_error",
                }
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=2, client=client
        )
        with pytest.raises(LLMError, match="insufficient balance") as caught:
            await provider.text_chat([])

    assert caught.value.code == "llm_insufficient_balance"
    assert caught.value.status_code == 503
    assert attempts == 1


@pytest.mark.asyncio
async def test_openai_provider_maps_protocol_disconnect_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("peer closed connection", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            "https://model.example/v1", "key", "model", max_retries=0, client=client
        )
        with pytest.raises(LLMError, match="temporarily unavailable") as caught:
            await provider.text_chat([])

    assert caught.value.code == "llm_unavailable"

def test_structured_feedback_adds_grouped_rate_grain_hint() -> None:
    payload = {
        "action": "execute_python",
        "task_id": "task_monthly",
        "filename": "monthly.py",
        "code": "print(1)",
        "purpose": "Create a monthly table",
        "expected_artifacts": ["data/monthly.csv"],
        "artifact_contracts": [
            {
                "artifact_path": "data/monthly.csv",
                "grain": "month",
                "fields": [
                    {"name": "month", "role": "dimension"},
                    {"name": "on_time_rate", "role": "measure", "metric_ref": "on_time_rate"},
                ],
                "metrics": [
                    {
                        "metric_id": "on_time_rate",
                        "metric_scope": "reusable_measure",
                        "label": "On-time rate",
                        "value": None,
                        "aggregation": "ratio",
                        "semantic_type": "rate",
                        "unit_family": "percentage",
                        "unit": "%",
                        "grain": None,
                        "numerator": "on_time_orders",
                        "denominator": "orders",
                        "ratio_basis": "other",
                        "ratio_value_basis": "fraction",
                        "definition": "on_time_orders / orders",
                        "source_artifact": "data/monthly.csv",
                        "source_field": "on_time_rate",
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValidationError) as caught:
        AgentActionResponse.model_validate(payload)

    feedback = OpenAICompatibleProvider._validation_feedback(caught.value)
    assert "Keep it as reusable_measure" in feedback
    assert "grain exactly to the enclosing artifact grain" in feedback
    assert "Do not move it to scalar_artifact_contracts" in feedback
    repair_messages = OpenAICompatibleProvider._repair_messages(
        [], "invalid", feedback, truncated=False
    )
    assert "Keep it as reusable_measure" in repair_messages[-1]["content"]
