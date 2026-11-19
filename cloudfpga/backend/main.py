import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import rtl_gen, simulator

app = FastAPI(title="CloudFPGA")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
app.include_router(rtl_gen.router)
app.include_router(simulator.router)
