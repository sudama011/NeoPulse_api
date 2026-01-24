from fastapi import APIRouter
from app.api.v1.endpoints import system, control, webhooks # <--- Import webhooks

api_router = APIRouter()

# 1. System (Health, Status)
api_router.include_router(system.router, tags=["System"])

# 2. Control (Start/Stop)
api_router.include_router(control.router, prefix="/control", tags=["Control"])

# 3. Webhooks (External Signals)
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])