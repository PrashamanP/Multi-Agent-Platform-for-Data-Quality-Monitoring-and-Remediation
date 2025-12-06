from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class AnomalyDetectionRequest(BaseModel):
    dataset_path: str
    run_id: Optional[str] = None
    min_history_points: Optional[int] = 3
    delta_threshold: Optional[float] = 10.0
    zscore_threshold: Optional[float] = 2.5
    flag_only_degradations: Optional[bool] = True


@router.post("/detect")
def run_anomaly_detection(req: AnomalyDetectionRequest):
    """
    Run anomaly detection on a dataset against historical baselines.
    """
    from ..services.pipeline_service import anomaly_detection_with_config

    try:
        config = {
            "min_history_points": req.min_history_points,
            "delta_thresholds": {
                "completeness": req.delta_threshold,
                "conformity": req.delta_threshold,
                "uniqueness": req.delta_threshold / 2
            },
            "zscore_threshold": req.zscore_threshold,
            "flag_only_degradations": req.flag_only_degradations
        }

        return anomaly_detection_with_config(req.dataset_path, config, req.run_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))