import asyncio
import hashlib
import json
import logging
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import LLMError
from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
        max_tokens: int = 8192,
        thinking_enabled: bool | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.client = client
        self.max_tokens = max_tokens
        self.thinking_enabled = thinking_enabled
        self._supports_json_schema = True

    async def structured_chat(
        self,
        messages: list[dict[str, str]],
        schema: type[SchemaT],
        **kwargs: Any,
    ) -> SchemaT:
        schema_json = schema.model_json_schema()
        response_format = self._response_format(schema, schema_json)
        if not self._supports_json_schema:
            messages = self._json_object_messages(messages, schema_json)
        max_output_tokens = kwargs.get("max_output_tokens")
        if max_output_tokens is not None:
            max_output_tokens = int(max_output_tokens)
        for attempt in range(self.max_retries + 1):
            try:
                payload = await self._request(
                    messages,
                    response_format=response_format,
                    max_tokens=max_output_tokens,
                )
            except LLMError as exc:
                if exc.code != "llm_response_format_unsupported":
                    raise
                self._supports_json_schema = False
                response_format = {"type": "json_object"}
                messages = self._json_object_messages(messages, schema_json)
                payload = await self._request(
                    messages,
                    response_format=response_format,
                    max_tokens=max_output_tokens,
                )
            content = self._content(payload)
            finish_reason = self._finish_reason(payload)
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            logger.info(
                "llm_structured_output model=%s schema=%s attempt=%d finish_reason=%s "
                "completion_tokens=%s content_chars=%d",
                self.model,
                schema.__name__,
                attempt + 1,
                finish_reason or "unknown",
                usage.get("completion_tokens"),
                len(content),
            )
            if finish_reason == "length":
                raise LLMError(
                    "Model structured output was truncated",
                    "llm_output_truncated",
                    details={
                        "schema": schema.__name__,
                        "attempt": attempt + 1,
                        "finish_reason": finish_reason,
                        "completion_tokens": usage.get("completion_tokens"),
                        "max_output_tokens": max_output_tokens or self.max_tokens,
                        "candidate_fingerprint": hashlib.sha256(
                            content.encode("utf-8")
                        ).hexdigest(),
                    },
                )
            try:
                return self._parse_structured_content(content, schema)
            except (PydanticValidationError, json.JSONDecodeError) as exc:
                feedback = self._validation_feedback(exc)
                logger.warning(
                    "llm_invalid_output model=%s schema=%s attempt=%d finish_reason=%s "
                    "content_chars=%d validation=%s",
                    self.model,
                    schema.__name__,
                    attempt + 1,
                    finish_reason or "unknown",
                    len(content),
                    feedback,
                )
                if attempt >= self.max_retries:
                    if finish_reason == "length":
                        raise LLMError(
                            "Model structured output was truncated after repeated attempts",
                            "llm_output_truncated",
                            details={
                                "schema": schema.__name__,
                                "validation": feedback,
                                "candidate_fingerprint": hashlib.sha256(
                                    content.encode("utf-8")
                                ).hexdigest(),
                                "finish_reason": finish_reason,
                            },
                        ) from exc
                    raise LLMError(
                        f"Model repeatedly returned invalid structured output: {feedback}",
                        "llm_invalid_output",
                        details={
                            "schema": schema.__name__,
                            "validation": feedback,
                            "candidate_fingerprint": hashlib.sha256(
                                content.encode("utf-8")
                            ).hexdigest(),
                            "finish_reason": finish_reason,
                        },
                    ) from exc
                messages = self._repair_messages(
                    messages,
                    content,
                    feedback,
                    truncated=finish_reason == "length",
                )
        raise LLMError("Model returned invalid structured output", "llm_invalid_output")

    @staticmethod
    def _parse_structured_content(content: str, schema: type[SchemaT]) -> SchemaT:
        stripped = content.strip()
        try:
            return schema.model_validate_json(stripped)
        except PydanticValidationError as direct_error:
            decoder = json.JSONDecoder()
            for index, character in enumerate(stripped):
                if character != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(stripped[index:])
                except json.JSONDecodeError:
                    continue
                if not isinstance(value, dict):
                    continue
                try:
                    return schema.model_validate(value)
                except PydanticValidationError:
                    continue
            raise direct_error

    @staticmethod
    def _validation_feedback(exc: PydanticValidationError | json.JSONDecodeError) -> str:
        if isinstance(exc, PydanticValidationError):
            issues = []
            for error in exc.errors(include_url=False)[:6]:
                location = ".".join(str(item) for item in error.get("loc", ())) or "root"
                issues.append(f"{location}: {error.get('msg', 'invalid value')}")
            feedback = "; ".join(issues) or "response did not match the required schema"
            if "only valid" in feedback:
                feedback += (
                    ". Remove each conditionally invalid field from every object where its "
                    "condition is false; do not repeat it with a placeholder value"
                )
            if "grain None must match artifact grain" in feedback:
                feedback += (
                    ". This metric is inside artifact_contracts. Keep it as reusable_measure "
                    "and set its grain exactly to the enclosing artifact grain. Do not move it "
                    "to scalar_artifact_contracts unless it is truly one dataset-level "
                    "materialized scalar"
                )
            return feedback
        return f"invalid JSON near character {exc.pos}: {exc.msg}"

    @staticmethod
    def _repair_messages(
        messages: list[dict[str, str]],
        content: str,
        feedback: str,
        *,
        truncated: bool,
    ) -> list[dict[str, str]]:
        previous = [] if truncated else [{"role": "assistant", "content": content[:4000]}]
        truncation_note = (
            "The previous response was cut off. Produce a substantially shorter action. "
            if truncated
            else ""
        )
        return [
            *messages,
            *previous,
            {
                "role": "user",
                "content": (
                    f"{truncation_note}The previous action failed validation: {feedback}. "
                    "Return one complete JSON object matching the supplied schema. "
                    "Do not use Markdown or add fields outside the schema. "
                    "For execute_python, keep code concise and under 6000 characters."
                ),
            },
        ]

    def _response_format(
        self, schema: type[SchemaT], schema_json: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._supports_json_schema:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": schema_json,
            },
        }

    def _json_object_messages(
        self, messages: list[dict[str, str]], schema_json: dict[str, Any]
    ) -> list[dict[str, str]]:
        schema_instruction = json.dumps(schema_json, ensure_ascii=False, separators=(",", ":"))
        return [
            *messages,
            {
                "role": "system",
                "content": (
                    "Return only one valid JSON object matching this JSON Schema: "
                    f"{schema_instruction}"
                ),
            },
        ]

    async def text_chat(self, messages: list[dict[str, str]]) -> str:
        return self._content(await self._request(messages))

    async def _request(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        if not self.api_base or not self.api_key or not self.model:
            raise LLMError("LLM provider is not configured", "llm_not_configured", 503)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if self.thinking_enabled is not None:
            body["thinking"] = {"type": "enabled" if self.thinking_enabled else "disabled"}
        if response_format:
            body["response_format"] = response_format
        started = time.perf_counter()
        for attempt in range(self.max_retries + 1):
            try:
                if self.client:
                    response = await self.client.post(
                        f"{self.api_base}/chat/completions",
                        json=body,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        timeout=self.timeout_seconds,
                    )
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.post(
                            f"{self.api_base}/chat/completions",
                            json=body,
                            headers={"Authorization": f"Bearer {self.api_key}"},
                        )
                if (
                    response.status_code == 400
                    and response_format
                    and response_format.get("type") == "json_schema"
                    and self._response_format_is_unavailable(response)
                ):
                    raise LLMError(
                        "Model service does not support JSON Schema response format",
                        "llm_response_format_unsupported",
                    )
                if response.status_code in {401, 403}:
                    raise LLMError(
                        "Model service authentication failed", "llm_authentication_failed"
                    )
                if response.status_code == 404:
                    raise LLMError(
                        "Configured model or API endpoint was not found", "llm_not_found"
                    )
                if response.status_code == 402:
                    raise LLMError(
                        "Model service account has insufficient balance",
                        "llm_insufficient_balance",
                        503,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "retryable LLM response", request=response.request, response=response
                    )
                if response.status_code >= 400:
                    provider_message = self._provider_error_message(response)
                    logger.warning(
                        "llm_bad_request model=%s status=%s response_format=%s provider_message=%s",
                        self.model,
                        response.status_code,
                        response_format.get("type") if response_format else None,
                        provider_message,
                    )
                    raise LLMError(
                        "Model service rejected the request",
                        "llm_bad_request",
                        details={"provider_message": provider_message} if provider_message else {},
                    )
                response.raise_for_status()
                payload = response.json()
                usage = payload.get("usage", {})
                logger.info(
                    "llm_request model=%s duration_ms=%d prompt_tokens=%s completion_tokens=%s",
                    self.model,
                    int((time.perf_counter() - started) * 1000),
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                )
                return payload
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                if attempt >= self.max_retries:
                    status = (
                        exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    )
                    code = "llm_rate_limited" if status == 429 else "llm_unavailable"
                    raise LLMError("Model service is temporarily unavailable", code) from exc
                await asyncio.sleep(0.05 * (attempt + 1))
            except (ValueError, KeyError, TypeError) as exc:
                raise LLMError("Model service returned an unreadable response") from exc
        raise LLMError("Model service is temporarily unavailable")

    @staticmethod
    def _response_format_is_unavailable(response: httpx.Response) -> bool:
        message = OpenAICompatibleProvider._provider_error_message(response)
        normalized = message.lower()
        return "response_format" in normalized and any(
            marker in normalized
            for marker in (
                "unavailable",
                "unsupported",
                "not support",
                "invalid schema",
                "schema for response_format",
                "required is required",
            )
        )

    @staticmethod
    def _provider_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    return error["message"][:1000]
                if isinstance(payload.get("message"), str):
                    return payload["message"][:1000]
        except (ValueError, TypeError, AttributeError):
            pass
        return response.text[:1000]

    @staticmethod
    def _content(payload: dict[str, Any]) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Model response did not contain content") from exc
        if not isinstance(content, str):
            raise LLMError("Model response content was not text")
        return content

    @staticmethod
    def _finish_reason(payload: dict[str, Any]) -> str | None:
        try:
            reason = payload["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError, AttributeError):
            return None
        return reason if isinstance(reason, str) else None
