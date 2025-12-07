from fastapi import APIRouter
from ..services.pipeline_service import profiling_only

router = APIRouter()

from pydantic import BaseModel

class ProfileRequest(BaseModel):
    dataset_path: str

@router.post("/profile")
def run_profile(req: ProfileRequest):
    return profiling_only(req.dataset_path)
