from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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

# Synthetic CCTV clips are first-class demo assets. This project-relative
# mount works in local development and in the Docker image's /app/data volume.
data_dir = Path(__file__).resolve().parents[2] / "data"
app.mount("/static", StaticFiles(directory=str(data_dir)), name="static")


@app.get("/")
def root():
    return {"service": "hydroloop-backend", "docs": "/docs", "api": "/api"}
