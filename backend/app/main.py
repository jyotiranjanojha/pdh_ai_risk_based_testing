from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ai_generator, domains, execution, rules, teams
from app.config import settings
from app.db.snowflake_client import sf_client

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Zero-data-egress Snowflake + Cortex AI Risk-Based Testing Control Plane.",
)

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(teams.router)
app.include_router(domains.router)
app.include_router(ai_generator.router)
app.include_router(rules.router)
app.include_router(execution.router)


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "mock_mode": settings.SNOWFLAKE_MOCK_MODE,
        "version": settings.APP_VERSION,
        "default_cortex_model": settings.CORTEX_MODEL,
    }


@app.get("/api/v1/cortex/models")
async def list_cortex_models():
    return {
        "default_model": settings.CORTEX_MODEL,
        "models": settings.AVAILABLE_CORTEX_MODELS,
    }


@app.get("/api/v1/auth/sso-status")
async def sso_status():
    return sf_client.get_sso_status()


@app.post("/api/v1/auth/sso-start")
async def sso_start():
    return sf_client.start_sso_login()
