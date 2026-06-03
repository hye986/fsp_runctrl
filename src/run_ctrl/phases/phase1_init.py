"""
phases/phase1_init.py

Phase 1 — Setup & Initialization

  1. Check no active run exists
  2. Create MLflow run → generate run_id
  3. Archive all config files (local + remote) into MLflow
  4. Log run parameters to MLflow
  5. Update state to INITIALIZED
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def run_init(cfg: dict, state_mgr, mlflow_mgr, ssh_runner=None) -> str:
    """
    Execute Phase 1.

    Returns the MLflow run_id.
    """
    from run_ctrl.core.config_archiver import ConfigArchiver

    run_cfg = cfg["run"]
    general = cfg["general"]

    # ------------------------------------------------------------------ #
    # Guard: block if a run is already in progress
    # ------------------------------------------------------------------ #
    if state_mgr.has_active_run():
        s = state_mgr.state
        raise RuntimeError(
            f"An active run already exists!\n"
            f"  Run ID : {s.run_id}\n"
            f"  Phase  : {s.phase.value}\n"
            f"  Started: {s.started_at}\n"
            f"Run 'run_ctrl status' for details, or 'run_ctrl abort' to clean up."
        )

    label = run_cfg.get("label", "unlabeled")
    data_path = run_cfg.get("data_path", "/data/fsp_data/")

    # ------------------------------------------------------------------ #
    # Start MLflow run
    # ------------------------------------------------------------------ #
    run_id = mlflow_mgr.start_run(
        run_name=label,
        tags={"experiment": general["experiment_name"]},
    )
    logger.info(f"[Phase1] MLflow run started: {run_id}")

    # ------------------------------------------------------------------ #
    # Log run parameters
    # ------------------------------------------------------------------ #
    params = {
        "label": label,
        "dark_frame_nr": run_cfg.get("dark_frame_nr", 0),
        "dark_frame_time": run_cfg.get("dark_frame_time", 0),
        "source_frame_nr": run_cfg.get("source_frame_nr", 0),
        "source_frame_time": run_cfg.get("source_frame_time", 0),
        "source_file_nr": run_cfg.get("source_file_nr", 1),
        "data_path": f"{data_path}/run_{run_id}",
    }
    # Include HV target from hv subsystem config
    hv_cfg = cfg.get("subsystems", {}).get("hv", {})
    ramp_up = hv_cfg.get("ramp_up_args", "")
    if "--target" in ramp_up:
        # Parse "--target 350" from args string
        parts = ramp_up.split()
        try:
            idx = parts.index("--target")
            params["hv_target_V"] = parts[idx + 1]
        except (ValueError, IndexError):
            pass

    mlflow_mgr.log_params(params)
    logger.info(f"[Phase1] Logged {len(params)} parameters to MLflow")

    # ------------------------------------------------------------------ #
    # Archive configuration files
    # ------------------------------------------------------------------ #
    archive_spec = cfg.get("run", {}).get("config_archive", {})
    if archive_spec:
        archiver = ConfigArchiver(ssh_runner=ssh_runner)
        try:
            uploaded = archiver.archive_all(archive_spec, mlflow_mgr)
            logger.info(f"[Phase1] Archived {len(uploaded)} config file(s) to MLflow")
        finally:
            archiver.cleanup()
    else:
        logger.info("[Phase1] No config_archive entries defined, skipping.")

    # ------------------------------------------------------------------ #
    # Persist state
    # ------------------------------------------------------------------ #
    state_mgr.transition_to_initialized(
        run_id=run_id,
        label=label,
        data_path=f"{data_path}/run_{run_id}",
    )

    logger.info(f"[Phase1] Done. Run ID: {run_id}")
    return run_id
