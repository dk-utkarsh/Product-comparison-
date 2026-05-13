from fastapi import FastAPI

from app.routes import match as match_route

app = FastAPI(title="product-compare-api", version="0.1.0")
app.include_router(match_route.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
