from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: str
    message_type: str
    created_at: datetime


class AnalysisPlanRequest(BaseModel):
    message: str
