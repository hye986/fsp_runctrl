"""
phases/phase3_daq.py

Phase 3 — Data Acquisition

Dispatches to LocalRunner or SSHRunner based on recorder.host in config.

  1. Run dark frames         (single call, source shutter closed)
  2. Open source shutter
  3. Run source files loop   (one recorder call per file, blocking)
  4. Close source shutter
  5. Mark done in state

Recorder loop mirrors the manual bash pattern:
  for i in 1 2 3 ...; do
      python udp_recorder.py <args> --output run_dir/data_run{i:03d}_{hyb}.raw
  done
Each iteration is a separate SSH call so progress is visible and
a failed file doesn't silently skip the rest.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


from run_ctrl.commands._context import get_recorder_runner as _get_recorder_runner


def run_daq(cfg: dict, state_mgr, mlflow_mgr, _ssh_runner=None):
    """Execute Phase 3. Runner is resolved from recorder.host in config."""
    from run_ctrl.core.state_manager import Phase
    if state_mgr.current_phase() != Phase.SPUN_UP:
        raise RuntimeError(
            "start-daq requires phase=spun_up. Run 'run_ctrl spinup' first."
        )

    run_cfg = cfg["run"]
    rec     = cfg["subsystems"]["recorder"]
    run_id  = state_mgr.state.run_id
    data_path = state_mgr.state.data_path
    recorder_runner = _get_recorder_runner(cfg)

    state_mgr.transition_to_acquiring()

    # ------------------------------------------------------------------ #
    # 1. Dark frames  (single call)
    # ------------------------------------------------------------------ #
    mlflow_mgr.set_tag("daq_phase", "dark_frames")
    _run_dark(rec, run_cfg, run_id, data_path, recorder_runner)
    state_mgr.mark_dark_done()
    mlflow_mgr.set_tag("dark_frames_done", "true")

    # ------------------------------------------------------------------ #
    # 2. Open source shutter
    # ------------------------------------------------------------------ #
    open_cmd = rec.get("source_open_cmd", "")
    if open_cmd:
        logger.info(f"[Phase3] Opening source shutter: {open_cmd}")
        recorder_runner.run(open_cmd)
        mlflow_mgr.set_tag("shutter", "open")

    # ------------------------------------------------------------------ #
    # 3. Source files loop
    # ------------------------------------------------------------------ #
    mlflow_mgr.set_tag("daq_phase", "source_frames")
    n_files = run_cfg.get("source_file_nr", 1)
    _run_source_loop(rec, run_cfg, run_id, data_path, recorder_runner, n_files, mlflow_mgr)
    state_mgr.mark_source_done()
    mlflow_mgr.set_tag("source_frames_done", "true")

    # ------------------------------------------------------------------ #
    # 4. Close source shutter
    # ------------------------------------------------------------------ #
    close_cmd = rec.get("source_close_cmd", "")
    if close_cmd:
        logger.info(f"[Phase3] Closing source shutter: {close_cmd}")
        recorder_runner.run(close_cmd)
        mlflow_mgr.set_tag("shutter", "closed")

    logger.info("[Phase3] Data acquisition complete. Ready for teardown.")


# ------------------------------------------------------------------ #
# Dark frames
# ------------------------------------------------------------------ #

def _run_dark(rec: dict, run_cfg: dict, run_id: str, data_path: str, ssh_runner):
    """Single recorder call for dark frames."""
    dark_template = rec.get("dark_args", "")
    if not dark_template:
        logger.warning("[Phase3] dark_args not configured — skipping dark frames.")
        return

    cmd = rec["executable"] + " " + _interpolate(
        dark_template, run_cfg, run_id, data_path, file_index=0
    )
    logger.info(f"[Phase3] Dark frames: {cmd}")
    ssh_runner.run(cmd, timeout=None)
    logger.info("[Phase3] Dark frames done.")


# ------------------------------------------------------------------ #
# Source frames loop
# ------------------------------------------------------------------ #

def _run_source_loop(
    rec: dict,
    run_cfg: dict,
    run_id: str,
    data_path: str,
    ssh_runner,
    n_files: int,
    mlflow_mgr,
):
    """
    Run the recorder once per output file, matching the bash pattern:

        for i in 1 .. n_files:
            python udp_recorder.py <source_args>

    The output filename template in source_args supports:
        {file_index}     → 1-based integer          e.g. 1, 2, 3
        {file_index_pad} → zero-padded 3-digit       e.g. 001, 002, 003
        {run_id}         → MLflow run_id (short)
        {data_path}      → full data directory on sulley
        {source_frame_nr}, {dark_frame_nr}, {source_file_nr} → from run config

    Example source_args in config:
        source_args: >-
          --raw-only
          --hyb-raw-output {data_path}/data_run{file_index_pad}_{hyb_select}.raw
          --hyb-select H1
          --max {source_frame_nr}
    """
    source_template = rec.get("source_args", "")
    if not source_template:
        logger.warning("[Phase3] source_args not configured — skipping source frames.")
        return

    executable = rec["executable"]

    logger.info(f"[Phase3] Starting source acquisition: {n_files} file(s)")
    for i in range(1, n_files + 1):
        cmd = executable + " " + _interpolate(
            source_template, run_cfg, run_id, data_path, file_index=i
        )
        logger.info(f"[Phase3] File {i}/{n_files}: {cmd}")
        ssh_runner.run(cmd, timeout=None)   # blocks until this file is done
        mlflow_mgr.log_metric("source_files_done", i)
        logger.info(f"[Phase3] File {i}/{n_files} complete.")

    logger.info(f"[Phase3] All {n_files} source file(s) recorded.")


# ------------------------------------------------------------------ #
# Template interpolation
# ------------------------------------------------------------------ #

def _interpolate(
    template: str,
    run_cfg: dict,
    run_id: str,
    data_path: str,
    file_index: int = 0,
) -> str:
    """
    Substitute {placeholders} in recorder arg strings.

    Available placeholders:
        {file_index}      — 1-based integer (0 for dark frames)
        {file_index_pad}  — zero-padded to 3 digits
        {run_id}          — MLflow run_id
        {run_id_short}    — first 8 chars of run_id
        {data_path}       — data directory (includes run_id suffix)
        {dark_frame_nr}   — from run config
        {source_frame_nr} — from run config
        {source_file_nr}  — from run config
    """
    return template.format(
        file_index=file_index,
        file_index_pad=f"{file_index:03d}",
        run_id=run_id,
        run_id_short=run_id[:8],
        data_path=data_path,
        dark_frame_nr=run_cfg.get("dark_frame_nr", 0),
        source_frame_nr=run_cfg.get("source_frame_nr", 0),
        source_file_nr=run_cfg.get("source_file_nr", 1),
    )
