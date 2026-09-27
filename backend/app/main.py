from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router

app = FastAPI(
    title="HYDROLOOP",
    description="Urban flood nowcasting for SIH PS 26085 — see data_inventory.md "
                 "for exactly which data in this running instance is real vs. "
                 "clearly-labeled synthetic demo data.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo only; restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
def root():
    return {"service": "hydroloop-backend", "docs": "/docs", "api": "/api"}
