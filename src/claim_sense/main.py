from fastapi import FastAPI

from claim_sense.api.claims import router as claims_router

app = FastAPI(title="ClaimSense")
app.include_router(claims_router)


@app.get("/")
async def root() -> str:
    return "Running fine"
