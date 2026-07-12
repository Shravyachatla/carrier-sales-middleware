import logging

from fastapi import FastAPI

from app.routers import calls, carriers, loads, negotiations

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Carrier Sales Middleware", version="0.1.0")

app.include_router(carriers.router)
app.include_router(loads.router)
app.include_router(negotiations.router)
app.include_router(calls.router)


@app.get("/health")
async def health() -> dict:
    # TODO: extend with real TMS/FMCSA reachability checks once those are wired in
    return {"status": "ok"}
