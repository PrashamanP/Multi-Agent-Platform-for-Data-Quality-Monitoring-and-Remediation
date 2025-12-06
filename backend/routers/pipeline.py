from fastapi import APIRouter
from ..services.pipeline_service import full_pipeline

router = APIRouter()

@router.post("/full")
def run_full(dataset_path: str):
    return full_pipeline(dataset_path)
