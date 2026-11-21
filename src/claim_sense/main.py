from contextlib import asynccontextmanager

from fastapi import FastAPI

from claim_sense.api.claims import router as claims_router
from claim_sense.db.session import engine, init_db
from claim_sense.workflows.graph import init_checkpoints


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        init_checkpoints()
        yield
    finally:
        engine.dispose()


app = FastAPI(title="ClaimSense", lifespan=lifespan)
app.include_router(claims_router)


@app.get("/")
async def root() -> str:
    return "Running fine"
