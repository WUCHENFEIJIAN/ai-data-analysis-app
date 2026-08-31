from collections import deque
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.errors import LLMError
from app.llm.base import LLMProvider

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class MockLLMProvider(LLMProvider):
    def __init__(self, responses: list[dict[str, Any] | str]) -> None:
        self.responses = deque(responses)
        self.requests: list[list[dict[str, str]]] = []
        self.schemas: list[type[BaseModel]] = []

    async def structured_chat(
        self, messages: list[dict[str, str]], schema: type[SchemaT], **kwargs: Any
    ) -> SchemaT:
        self.requests.append(messages)
        self.schemas.append(schema)
        response = self._next()
        return (
            schema.model_validate_json(response)
            if isinstance(response, str)
            else schema.model_validate(response)
        )

    async def text_chat(self, messages: list[dict[str, str]]) -> str:
        self.requests.append(messages)
        response = self._next()
        return response if isinstance(response, str) else str(response)

    def _next(self) -> dict[str, Any] | str:
        if not self.responses:
            raise LLMError("Mock provider has no queued response", "mock_exhausted")
        return self.responses.popleft()
