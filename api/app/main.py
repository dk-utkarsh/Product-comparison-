from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routes import compare as compare_route
from app.routes import feedback as feedback_route
from app.routes import golden as golden_route
from app.routes import match as match_route
from app.routes import test_ui as test_ui_route

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="product-compare-api", version="0.1.0")
app.include_router(match_route.router)
app.include_router(test_ui_route.router)
app.include_router(compare_route.router)
app.include_router(feedback_route.router)
app.include_router(golden_route.router)
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(_STATIC / "index.html")
