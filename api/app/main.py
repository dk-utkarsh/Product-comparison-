from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routes import compare as compare_route
from app.routes import feedback as feedback_route
from app.routes import golden as golden_route
from app.routes import insights as insights_route
from app.routes import match as match_route
from app.routes import reviews as reviews_route
from app.routes import runs as runs_route
from app.routes import serp as serp_route
from app.routes import test_ui as test_ui_route
from app.scheduler import start_scheduler, stop_scheduler

_STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()  # no-op unless scheduled_runs_enabled + api key set
    yield
    stop_scheduler()


app = FastAPI(title="product-compare-api", version="0.1.0", lifespan=lifespan)
app.include_router(match_route.router)
app.include_router(test_ui_route.router)
app.include_router(compare_route.router)
app.include_router(feedback_route.router)
app.include_router(golden_route.router)
app.include_router(runs_route.router)
app.include_router(reviews_route.router)
app.include_router(insights_route.router)
app.include_router(serp_route.router)
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(_STATIC / "index.html")
