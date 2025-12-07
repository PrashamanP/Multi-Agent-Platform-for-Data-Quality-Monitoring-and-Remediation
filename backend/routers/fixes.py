from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
import uuid

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
    elif pd.isna(obj):
        return None
    elif hasattr(obj, 'isoformat'):
        return obj.isoformat()
    elif hasattr(obj, '__dict__'):
        return convert_to_serializable(obj.__dict__)
    else:
        return obj


class ApplyFixesRequest(BaseModel):
    dataset_path: str
    accepted_recommendations: List[Dict[str, Any]]


@router.post("/apply")
def apply_fixes(req: ApplyFixesRequest):
    """
    Apply accepted fix recommendations to the dataset using FixExecutorAgent.
    """
    try:
        # Import the agents
        from src.agents.fix_executor_agent import FixExecutorAgent
        from src.core.data_models import FixRecommendation, FixRecommendationResults

        # Validate dataset exists
        input_path = Path(req.dataset_path)
        if not input_path.exists():
            raise HTTPException(status_code=404, detail=f"Dataset not found: {req.dataset_path}")

        # Convert accepted recommendations to FixRecommendation objects
        recommendations = []
        for rec_dict in req.accepted_recommendations:
            rec = FixRecommendation(
                recommendation_id=rec_dict.get("recommendation_id", str(uuid.uuid4())),
                issue_id=rec_dict.get("issue_id", str(uuid.uuid4())),
                dataset_name=rec_dict.get("dataset_name", input_path.stem),
                column_name=rec_dict.get("column_name", ""),
                issue_type=rec_dict.get("issue_type", ""),
                fix_strategy=rec_dict.get("fix_strategy", ""),
                fix_description=rec_dict.get("fix_description", ""),
                confidence_score=float(rec_dict.get("confidence_score", 0.5)),
                estimated_impact=rec_dict.get("estimated_impact", "medium"),
                actionable=rec_dict.get("actionable", False),
                suggested_value=rec_dict.get("suggested_value"),
                rationale=rec_dict.get("rationale", ""),
                prerequisites=rec_dict.get("prerequisites", []),
                risk_assessment=rec_dict.get("risk_assessment", "")
            )
            recommendations.append(rec)

        # Create FixRecommendationResults object
        fix_recommendation_results = FixRecommendationResults(
            dataset_name=input_path.stem,
            run_id=str(uuid.uuid4()),
            recommendations=recommendations,
            execution_metadata={
                "source": "streamlit_ui",
                "accepted_count": len(recommendations)
            }
        )

        # Generate timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Configure the executor agent
        executor_config = {
            "create_backup": True,
            "validate_after_fix": True,
            "add_fix_columns": True,
            "add_fix_flags": True,
            "add_recommendation_column": True,
            "never_modify_original_columns": False,
            "write_mode": "new_file",
            "new_file_suffix": f"_fixed_{timestamp}",  # Include timestamp
            "allowed_fix_types": ["conformity", "completeness"],
            "recommendations_only": False,
            "max_sample_details": 100
        }

        # Create executor agent
        executor = FixExecutorAgent(config=executor_config)

        # Determine output path with timestamp
        output_path = input_path.parent / f"{input_path.stem}_fixed_{timestamp}{input_path.suffix}"

        # Execute fixes
        execution_results = executor.execute(
            recommendations=fix_recommendation_results,
            dataset_path=str(req.dataset_path),
            output_path=str(output_path),
            apply_only_actionable=False
        )

        # Load the fixed dataset for preview
        fixed_df = pd.read_csv(output_path)

        # Convert fixes_applied to serializable format
        fixes_applied_list = []
        for fix in execution_results.fixes_applied:
            fixes_applied_list.append({
                "column": fix.column_name,
                "strategy": fix.fix_strategy,
                "rows_fixed": int(fix.rows_fixed),
                "rows_failed": int(fix.rows_failed),
                "success": fix.success,
                "details": fix.fix_details[:10] if fix.fix_details else []
            })

        # Prepare preview (handle NaN values)
        preview_df = fixed_df.head(20).copy()
        preview_df = preview_df.fillna("")
        preview_records = preview_df.to_dict(orient="records")

        # Build response
        result = {
            "status": "success",
            "message": f"Applied {len(recommendations)} fixes",
            "fixed_file_path": str(output_path),
            "backup_path": execution_results.backup_path,
            "total_rows_fixed": int(execution_results.total_rows_fixed),
            "fixes_applied": fixes_applied_list,
            "preview": preview_records,
            "columns": list(fixed_df.columns),
            "row_count": len(fixed_df),
            "execution_metadata": convert_to_serializable(execution_results.execution_metadata),
            "timestamp": timestamp
        }

        return result

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_detail = f"Failed to apply fixes: {str(e)}\n{traceback.format_exc()}"
        print(error_detail)
        raise HTTPException(status_code=500, detail=error_detail)


@router.get("/download/{filename}")
def download_fixed_file(filename: str):
    """
    Download the fixed dataset file.
    """
    from fastapi.responses import FileResponse

    file_path = Path("data/input") / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="text/csv"
    )