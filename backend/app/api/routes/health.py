from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    with request.app.state.database.session() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
