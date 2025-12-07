from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import numpy as np

router = APIRouter()


def convert_to_serializable(obj):
    """Recursively convert objects to JSON-serializable format."""
    if isinstance(obj, dict):
        return {key: convert_to_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(item) for item in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif hasattr(obj, '__dict__'):
        return convert_to_serializable(obj.__dict__)
    else:
        return obj


class RecommendationRequest(BaseModel):
    dataset_path: str


@router.post("/generate")
def generate_recommendations(req: RecommendationRequest):
    """
    Generate fix recommendations using the FixRecommendationAgent.
    """
    from src.agents.fix_recommendation_agent import FixRecommendationAgent
    from scripts.run_pipeline_api import run_profiling_only

    try:
        # Run profiling to get issues
        profile_results = run_profiling_only(req.dataset_path)

        # Create the recommendation agent
        agent = FixRecommendationAgent()

        # Execute the agent
        recommendations = agent.execute(
            profile_results=profile_results,
            dataset_path=req.dataset_path
        )

        # Convert to serializable format
        result = convert_to_serializable(recommendations)

        return result

    except Exception as e:
        import traceback
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate recommendations: {str(e)}\n{traceback.format_exc()}"
        )