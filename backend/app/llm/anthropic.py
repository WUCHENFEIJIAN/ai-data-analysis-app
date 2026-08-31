import asyncio
import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import LLMError
from app.llm.base import LLMProvider

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        max_tokens: int = 8192,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_tokens = max_tokens

    async def structured_chat(
        self, messages: list[dict[str, str]], schema: type[SchemaT], **kwargs: Any
    ) -> SchemaT:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        system = (
            "Return only one valid JSON object matching this JSON Schema. "
            "Do not use Markdown or add fields outside the schema.\n"
            f"{schema_json}"
        )
        for attempt in range(self.max_retries + 1):
            content = await self._request(messages, system=system, max_tokens=kwargs.get("max_output_tokens"))
            try:
                return schema.model_validate_json(self._clean_json(content))
            except (PydanticValidationError, json.JSONDecodeError) as exc:
                if attempt >= self.max_retries:
                    raise LLMError("Model repeatedly returned invalid structured output", "llm_invalid_output") from exc
                system += "\nPrevious output was invalid. Return a complete shorter JSON object."
        raise LLMError("Model service is temporarily unavailable")

    async def text_chat(self, messages: list[dict[str, str]]) -> str:
        return await self._request(messages, system=None, max_tokens=None)

    async def _request(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None,
        max_tokens: int | None,
    ) -> str:
        if not self.api_base or not self.api_key or not self.model:
            raise LLMError("LLM provider is not configured", "llm_not_configured", 503)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(max_tokens or self.max_tokens),
            "messages": [
                {"role": item["role"], "content": item["content"]}
                for item in messages
                if item.get("role") in {"user", "assistant"}
            ],
        }
        if system:
            body["system"] = system
        endpoint = self.api_base
        if not endpoint.endswith("/messages"):
            endpoint = f"{endpoint}/v1/messages" if not endpoint.endswith("/v1") else f"{endpoint}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(endpoint, json=body, headers=headers)
                if response.status_code in {401, 403}:
                    raise LLMError("Model service authentication failed", "llm_authentication_failed")
                if response.status_code == 404:
                    raise LLMError("Configured model or API endpoint was not found", "llm_not_found")
                if response.status_code >= 400:
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                    raise LLMError("Model service rejected the request", "llm_bad_request")
                payload = response.json()
                content = payload.get("content", [])
                text = content[0].get("text") if content and isinstance(content[0], dict) else None
                if not isinstance(text, str):
                    raise LLMError("Model response did not contain content")
                return text
            except (httpx.RequestError, ValueError, KeyError, TypeError) as exc:
                if attempt >= self.max_retries:
                    raise LLMError("Model service is temporarily unavailable", "llm_unavailable") from exc
                await asyncio.sleep(0.05 * (attempt + 1))
        raise LLMError("Model service is temporarily unavailable")

    @staticmethod
    def _clean_json(content: str) -> str:
        text = content.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return text
