from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routers import profiling, anomaly_detection, recommendations, fixes

app = FastAPI(title="NPI Data Quality Management System", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(profiling.router, prefix="/profiling", tags=["profiling"])
app.include_router(anomaly_detection.router, prefix="/anomaly", tags=["anomaly"])
app.include_router(recommendations.router, prefix="/recommendations", tags=["recommendations"])
app.include_router(fixes.router, prefix="/fixes", tags=["fixes"])