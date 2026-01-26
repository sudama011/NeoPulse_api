# app/api/v1/router.py
from fastapi import APIRouter

from app.api.v1.endpoints import engine, health, webhooks  # engine is the new file

api_router = APIRouter()

api_router.include_router(engine.router, prefix="/engine", tags=["Engine Control"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])
api_router.include_router(health.router, prefix="/system", tags=["System"])
