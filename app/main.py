from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.container import Container
from app.core.logger import setup_logging
from app.core.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    container = Container(settings)
    app.state.container = container
    await container.start()
    try:
        yield
    finally:
        await container.stop()


app = FastAPI(title="NeoPulse", description="Automated trading on Kotak Neo", version="3.0.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")
