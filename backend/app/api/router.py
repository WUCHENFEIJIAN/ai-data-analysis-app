from fastapi import APIRouter

from app.api.routes.analysis import router as analysis_router
from app.api.routes.artifacts import router as artifacts_router
from app.api.routes.files import router as files_router
from app.api.routes.health import router as health_router
from app.api.routes.planning import router as planning_router
from app.api.routes.projects import router as projects_router
from app.api.routes.settings import router as settings_router

api_router = APIRouter()
api_router.include_router(analysis_router)
api_router.include_router(artifacts_router)
api_router.include_router(health_router)
api_router.include_router(projects_router)
api_router.include_router(files_router)
api_router.include_router(planning_router)
api_router.include_router(settings_router)
