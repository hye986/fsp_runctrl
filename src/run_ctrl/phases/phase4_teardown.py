"""
phases/phase4_teardown.py

Teardown functions — separated into independent concerns:

  run_stop()      — Close MLflow run, reset state. Hardware untouched.
                    Call this between runs when you want to adjust parameters
                    and start fresh without powering anything off.

  run_powerdown() — HV ramp down → sensor off → ASIC off.
                    Hardware-only, no MLflow interaction.
                    Safe to call independently at any time.

  run_teardown()  — run_stop() + run_powerdown() combined.
                    Original full teardown, still available.

  run_snapshot()  — Open MLflow run, archive configs, close immediately.
                    No hardware involved. Use after adjusting parameters
                    mid-session to track what changed without starting a
                    full data-taking run.

  run_abort()     — Emergency: safe powerdown from any phase, MLflow KILLED.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ================================================================== #
# run_stop — end MLflow run, reset state, leave hardware on
# ================================================================== #

def run_stop(cfg: dict, state_mgr, mlflow_mgr):
    """
    Close the current MLflow run and reset state to IDLE.
    Hardware (HV, sensor, ASIC, DAQ) is left exactly as-is.

    Valid from: ACQUIRING or SPUN_UP
    Use this when you want to adjust parameters and start a new run
    without powering anything off.
    """
    from run_ctrl.core.state_manager import Phase
    _assert_phase_in(
        state_mgr, [Phase.ACQUIRING, Phase.SPUN_UP], "stop"
    )

    run_id = state_mgr.state.run_id
    mlflow_mgr.set_tag("stopped_without_powerdown", "true")
    mlflow_mgr.end_run(status="FINISHED")
    logger.info(f"[stop] MLflow run {run_id} closed.")

    state_mgr.transition_to_torn_down()
    logger.info("[stop] State reset. Hardware left running.")
    logger.info("[stop] Run 'run_ctrl reset' then 'run_ctrl init' to start a new run.")
    logger.info("[stop] Run 'run_ctrl powerdown' when you want to switch hardware off.")


# ================================================================== #
# run_powerdown — switch off hardware, no MLflow interaction
# ================================================================== #

def run_powerdown(cfg: dict, local_runner, mlflow_mgr=None):
    """
    Power down hardware in safe order: HV → sensor → ASIC.
    DAQ is left running (stop_cmd is empty in config).

    Can be called independently of the run state — does not require
    an active MLflow run and does not change the state file.
    """
    subsystems = cfg.get("subsystems", {})

    # ------------------------------------------------------------------ #
    # 1. HV ramp down  (ALWAYS before sensor off)
    # ------------------------------------------------------------------ #
    hv = subsystems.get("hv", {})
    hv_exec = hv.get("executable", "")
    ramp_down = hv.get("ramp_down_args", "")
    if hv_exec and ramp_down:
        logger.info(f"[powerdown] Ramping HV down: {hv_exec} {ramp_down}")
        local_runner.run(f"{hv_exec} {ramp_down}", timeout=600)
        if mlflow_mgr:
            mlflow_mgr.set_tag("hv_ramped_down", "true")
        logger.info("[powerdown] HV at zero.")
    else:
        logger.warning("[powerdown] HV config incomplete — SKIPPING ramp down!")
        logger.warning("[powerdown] Manually verify HV is safe before continuing!")

    # ------------------------------------------------------------------ #
    # 2. Sensor off
    # ------------------------------------------------------------------ #
    sensor = subsystems.get("sensor", {})
    sensor_exec = sensor.get("executable", "")
    stop = sensor.get("stop_cmd", "")
    if sensor_exec and stop:
        logger.info("[powerdown] Powering off sensor...")
        local_runner.run(f"{sensor_exec} {stop}")
        if mlflow_mgr:
            mlflow_mgr.set_tag("sensor_on", "false")
        logger.info("[powerdown] Sensor off.")

    # ------------------------------------------------------------------ #
    # 3. ASIC off
    # ------------------------------------------------------------------ #
    asic = subsystems.get("asic", {})
    asic_stop = asic.get("stop_cmd", [])
    if asic_stop:
        logger.info("[powerdown] Powering off ASIC...")
        local_runner.run_subcommands(asic["executable"], asic_stop)
        if mlflow_mgr:
            mlflow_mgr.set_tag("asic_started", "false")
        logger.info("[powerdown] ASIC off.")

    # ------------------------------------------------------------------ #
    # 4. DAQ — leave running
    # ------------------------------------------------------------------ #
    daq_stop = subsystems.get("daq", {}).get("stop_cmd", [])
    if daq_stop:
        local_runner.run_subcommands(subsystems["daq"]["executable"], daq_stop)
    else:
        logger.info("[powerdown] DAQ left running (stop_cmd is empty).")

    logger.info("[powerdown] Hardware powered down.")


# ================================================================== #
# run_teardown — stop + powerdown combined (original behaviour)
# ================================================================== #

def run_teardown(cfg: dict, state_mgr, mlflow_mgr, local_runner):
    """
    Full teardown: close MLflow run AND power down hardware.
    Equivalent to 'run_ctrl stop' followed by 'run_ctrl powerdown'.

    Valid from: ACQUIRING or SPUN_UP
    """
    from run_ctrl.core.state_manager import Phase
    _assert_phase_in(
        state_mgr, [Phase.ACQUIRING, Phase.SPUN_UP], "teardown"
    )
    run_powerdown(cfg, local_runner, mlflow_mgr=mlflow_mgr)
    run_stop(cfg, state_mgr, mlflow_mgr=None)   # MLflow already ended in powerdown


# ================================================================== #
# run_snapshot — config tracking only, no hardware
# ================================================================== #

def run_snapshot(cfg: dict, mlflow_mgr, ssh_runner=None, label: str = "snapshot"):
    """
    Open a new MLflow run, archive all configs, then close immediately.
    No hardware commands. No state file changes.

    Use this after tweaking parameters mid-session to record what the
    current configuration looks like, without starting a full run.
    """
    from run_ctrl.core.config_archiver import ConfigArchiver

    run_id = mlflow_mgr.start_run(
        run_name=label,
        tags={"type": "snapshot", "experiment": cfg["general"]["experiment_name"]},
    )
    logger.info(f"[snapshot] MLflow run: {run_id}")

    # Log current run parameters as context
    run_cfg = cfg.get("run", {})
    mlflow_mgr.log_params({
        "label":           run_cfg.get("label", ""),
        "dark_frame_nr":   run_cfg.get("dark_frame_nr", 0),
        "source_frame_nr": run_cfg.get("source_frame_nr", 0),
        "source_file_nr":  run_cfg.get("source_file_nr", 1),
    })

    # Archive configs
    archive_spec = run_cfg.get("config_archive", {})
    if archive_spec:
        archiver = ConfigArchiver(ssh_runner=ssh_runner)
        try:
            uploaded = archiver.archive_all(archive_spec, mlflow_mgr)
            logger.info(f"[snapshot] Archived {len(uploaded)} config file(s).")
        finally:
            archiver.cleanup()

    mlflow_mgr.end_run(status="FINISHED")
    logger.info(f"[snapshot] Done. Run ID: {run_id}")
    return run_id


# ================================================================== #
# run_abort — emergency, callable from any phase
# ================================================================== #

def run_abort(cfg: dict, state_mgr, mlflow_mgr, local_runner):
    """
    Emergency shutdown. Safe powerdown regardless of current phase.
    Ends MLflow run as KILLED.
    """
    logger.warning("[ABORT] Emergency shutdown initiated!")
    subsystems = cfg.get("subsystems", {})

    _safe_run(local_runner, subsystems, "hv",     "ramp_down_args", "HV ramp down", timeout=600)
    _safe_run(local_runner, subsystems, "sensor",  "stop_cmd",      "Sensor off")
    _safe_subrun(local_runner, subsystems, "asic", "stop_cmd",      "ASIC off")

    try:
        mlflow_mgr.end_run(status="KILLED")
    except Exception as e:
        logger.warning(f"[ABORT] Could not end MLflow run: {e}")

    state_mgr.transition_to_aborted()
    logger.warning("[ABORT] Done. Verify hardware state manually.")


# ================================================================== #
# Internal helpers
# ================================================================== #

def _assert_phase_in(state_mgr, allowed, cmd):
    from run_ctrl.core.state_manager import Phase
    if state_mgr.current_phase() not in allowed:
        names = [p.value for p in allowed]
        raise RuntimeError(
            f"'{cmd}' requires phase in {names}, "
            f"current phase is '{state_mgr.current_phase().value}'."
        )


def _safe_run(local_runner, subsystems, key, arg_key, label, timeout=60):
    try:
        sub = subsystems.get(key, {})
        exc = sub.get("executable", "")
        args = sub.get(arg_key, "")
        if exc and args:
            logger.warning(f"[ABORT] {label}...")
            local_runner.run(f"{exc} {args}", timeout=timeout, check=False)
    except Exception as e:
        logger.warning(f"[ABORT] {label} failed: {e}")


def _safe_subrun(local_runner, subsystems, key, cmd_key, label):
    try:
        sub = subsystems.get(key, {})
        cmds = sub.get(cmd_key, [])
        exc = sub.get("executable", "")
        if exc and cmds:
            logger.warning(f"[ABORT] {label}...")
            local_runner.run_subcommands(exc, cmds)
    except Exception as e:
        logger.warning(f"[ABORT] {label} failed: {e}")
