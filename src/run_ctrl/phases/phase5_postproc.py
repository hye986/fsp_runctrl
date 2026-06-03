"""
phases/phase5_postproc.py

Phase 5 — Post-processing & Logging (optional, async)

  - Post an entry to Elog via REST API
  - Trigger long-duration analysis on sulley (non-blocking background SSH)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


def run_postproc(
    cfg: dict,
    state_mgr,
    mlflow_mgr=None,
    ssh_runner=None,
    do_elog: bool = True,
    do_analysis: bool = True,
    elog_comment: str = "",
):
    """
    Execute Phase 5.

    Callable after teardown. Does NOT require a specific phase —
    you can re-run postproc independently.
    """
    post = cfg.get("subsystems", {}).get("post_processing", {})
    state = state_mgr.state
    run_id = state.run_id

    if not run_id:
        raise RuntimeError("No run_id in state. Run init first.")

    # ------------------------------------------------------------------ #
    # Elog
    # ------------------------------------------------------------------ #
    if do_elog:
        elog_cfg = post.get("elog", {})
        _post_elog(elog_cfg, state, cfg, elog_comment)
    else:
        logger.info("[Phase5] Elog skipped.")

    # ------------------------------------------------------------------ #
    # Analysis (non-blocking on sulley)
    # ------------------------------------------------------------------ #
    if do_analysis and ssh_runner:
        ana_cfg = post.get("analysis", {})
        _trigger_analysis(ana_cfg, state, ssh_runner)
    elif do_analysis:
        logger.warning("[Phase5] Analysis requested but no SSH runner available.")
    else:
        logger.info("[Phase5] Analysis skipped.")


# ------------------------------------------------------------------ #
# Elog helper
# ------------------------------------------------------------------ #

def _post_elog(elog_cfg: dict, state, cfg: dict, extra_comment: str):
    import requests

    url = elog_cfg.get("url", "")
    logbook = elog_cfg.get("logbook", "FSP")
    token = os.environ.get("ELOG_TOKEN", "")

    if not url:
        logger.warning("[Phase5] Elog URL not configured — skipping.")
        return

    run_cfg = cfg.get("run", {})
    body = _build_elog_message(state, run_cfg, extra_comment)

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "logbook": logbook,
        "title": f"Run {state.label} [{state.run_id[:8]}]",
        "body": body,
        "tags": ["run_ctrl", "automated"],
    }

    logger.info(f"[Phase5] Posting to Elog: {url}")
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        logger.info(f"[Phase5] Elog entry posted (status {resp.status_code}).")
    except requests.RequestException as e:
        logger.warning(f"[Phase5] Elog post failed: {e}")


def _build_elog_message(state, run_cfg: dict, extra_comment: str) -> str:
    lines = [
        f"**Automated run summary**",
        f"",
        f"- Run ID      : `{state.run_id}`",
        f"- Label       : {state.label}",
        f"- Started     : {state.started_at}",
        f"- Completed   : {datetime.now().isoformat(timespec='seconds')}",
        f"- Dark frames : {run_cfg.get('dark_frame_nr', '?')}  (done: {state.dark_done})",
        f"- Source frames: {run_cfg.get('source_frame_nr', '?')}  (done: {state.source_done})",
        f"- Data path   : {state.data_path}",
        f"",
        f"MLflow: see run `{state.run_id}` for full parameter and config archive.",
    ]
    if extra_comment:
        lines += ["", "**Operator comment:**", extra_comment]
    return "\n".join(lines)


# ------------------------------------------------------------------ #
# Analysis helper
# ------------------------------------------------------------------ #

def _trigger_analysis(ana_cfg: dict, state, ssh_runner):
    script = ana_cfg.get("script", "")
    args_template = ana_cfg.get("args", "")

    if not script:
        logger.warning("[Phase5] Analysis script not configured — skipping.")
        return

    args = args_template.format(
        run_id=state.run_id,
        data_path=state.data_path or "",
    )
    cmd = f"python {script} {args}"

    logger.info(f"[Phase5] Triggering analysis on sulley (non-blocking): {cmd}")
    ssh_runner.run_background(cmd)
    logger.info(
        "[Phase5] Analysis running in background on sulley.\n"
        "         Log: /tmp/run_ctrl_bg.log on sulley\n"
        "         The analysis script should attach its outputs to MLflow run "
        f"{state.run_id} when done."
    )
