from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMProvider(ABC):
    @abstractmethod
    async def structured_chat(
        self, messages: list[dict[str, str]], schema: type[SchemaT], **kwargs: Any
    ) -> SchemaT:
        raise NotImplementedError

    @abstractmethod
    async def text_chat(self, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError
