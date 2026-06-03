"""
core/mlflow_manager.py

Wraps all MLflow interactions:
  - Create / end runs
  - Log parameters and metrics
  - Upload artifact files
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MLflowManager:
    def __init__(self, tracking_uri: str, experiment_name: str):
        try:
            import mlflow
        except ImportError:
            raise ImportError("MLflow is required: pip install mlflow")

        self.mlflow = mlflow
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)
        self._run = None

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(self, run_name: str, tags: Optional[dict] = None) -> str:
        """Start a new MLflow run. Returns the run_id."""
        self._run = self.mlflow.start_run(run_name=run_name, tags=tags or {})
        run_id = self._run.info.run_id
        logger.info(f"[MLflow] Started run: {run_id}  (name={run_name})")
        return run_id

    def resume_run(self, run_id: str):
        """Resume an existing run (e.g. to attach post-processing artifacts)."""
        self._run = self.mlflow.start_run(run_id=run_id)
        logger.info(f"[MLflow] Resumed run: {run_id}")

    def end_run(self, status: str = "FINISHED"):
        """
        End the MLflow run.
        status: "FINISHED" | "FAILED" | "KILLED"
        """
        if self._run:
            self.mlflow.end_run(status=status)
            logger.info(f"[MLflow] Ended run with status: {status}")
            self._run = None

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def log_params(self, params: dict):
        """Log a flat dict of parameters."""
        self.mlflow.log_params(params)
        logger.info(f"[MLflow] Logged {len(params)} parameter(s)")

    def log_metric(self, key: str, value: float, step: Optional[int] = None):
        self.mlflow.log_metric(key, value, step=step)

    def log_artifact(self, local_path: str, artifact_subdir: Optional[str] = None):
        """Upload a local file to the MLflow artifact store."""
        path = Path(local_path)
        if not path.exists():
            logger.warning(f"[MLflow] Artifact not found, skipping: {local_path}")
            return
        self.mlflow.log_artifact(str(path), artifact_path=artifact_subdir)
        logger.info(f"[MLflow] Archived: {path.name}" +
                    (f" → {artifact_subdir}/" if artifact_subdir else ""))

    def log_artifacts_dir(self, local_dir: str, artifact_subdir: Optional[str] = None):
        """Upload an entire directory."""
        self.mlflow.log_artifacts(local_dir, artifact_path=artifact_subdir)

    def set_tag(self, key: str, value: str):
        self.mlflow.set_tag(key, value)
