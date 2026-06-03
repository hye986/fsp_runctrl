"""
phases/phase2_spinup.py

Phase 2 — Hardware Spin-up (on hydra)

Order (safety-critical, do not reorder):
  1. Check interlock is alive
  2. Start DAQ chain  (fsp-ctrl start_s7, start_daq)
  3. Start ASIC       (fsp-ctrl start_veritas, sync_s7)
  4. Ramp up HV       (hv_ramp.py — blocks until done)
  5. Power on sensor  (ccd-pwr pwr_up)

Each step is logged to MLflow as a tag on completion.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SPINUP_SUBSYSTEMS = ["daq", "asic", "hv", "sensor"]


def run_spinup(cfg: dict, state_mgr, mlflow_mgr, local_runner):
    """Execute Phase 2."""
    subsystems = cfg.get("subsystems", {})

    # ------------------------------------------------------------------ #
    # Guard
    # ------------------------------------------------------------------ #
    from run_ctrl.core.state_manager import Phase
    if state_mgr.current_phase() != Phase.INITIALIZED:
        raise RuntimeError(
            "spinup requires phase=initialized. Run 'run_ctrl init' first."
        )

    # ------------------------------------------------------------------ #
    # 1. Interlock health check
    # ------------------------------------------------------------------ #
    safety = cfg.get("safety", {})
    if safety.get("require_interlock", True):
        _check_interlock(subsystems.get("interlock", {}), local_runner)

    # ------------------------------------------------------------------ #
    # 2. DAQ chain
    # ------------------------------------------------------------------ #
    daq = subsystems.get("daq", {})
    init_cmd = daq.get("init_cmd", "")
    if init_cmd:
        logger.info("[Phase2] DAQ init...")
        local_runner.run(f"{daq['executable']} {init_cmd}")
    else:
        logger.info("[Phase2] DAQ init_cmd empty — skipping (manual init assumed).")

    start_cmds = daq.get("start_cmd", [])
    if start_cmds:
        logger.info("[Phase2] Starting DAQ chain...")
        local_runner.run_subcommands(daq["executable"], start_cmds)
        mlflow_mgr.set_tag("daq_started", "true")
        logger.info("[Phase2] DAQ chain started.")

    # ------------------------------------------------------------------ #
    # 3. ASIC
    # ------------------------------------------------------------------ #
    asic = subsystems.get("asic", {})
    asic_cmds = asic.get("start_cmd", [])
    if asic_cmds:
        logger.info("[Phase2] Starting ASIC...")
        local_runner.run_subcommands(asic["executable"], asic_cmds)
        mlflow_mgr.set_tag("asic_started", "true")
        logger.info("[Phase2] ASIC started.")

    # ------------------------------------------------------------------ #
    # 4. HV ramp up (blocking — exits 0 when target reached)
    # ------------------------------------------------------------------ #
    hv = subsystems.get("hv", {})
    hv_exec = hv.get("executable", "")
    ramp_up = hv.get("ramp_up_args", "")
    if hv_exec and ramp_up:
        logger.info(f"[Phase2] Ramping HV up: {hv_exec} {ramp_up}")
        local_runner.run(f"{hv_exec} {ramp_up}", timeout=600)
        mlflow_mgr.set_tag("hv_ramped_up", "true")
        logger.info("[Phase2] HV at target.")
    else:
        logger.warning("[Phase2] HV config incomplete — skipping HV ramp.")

    # ------------------------------------------------------------------ #
    # 5. Sensor power on
    # ------------------------------------------------------------------ #
    sensor = subsystems.get("sensor", {})
    sensor_exec = sensor.get("executable", "")

    init = sensor.get("init_cmd", "")
    if init:
        logger.info("[Phase2] Sensor init...")
        local_runner.run(f"{sensor_exec} {init}")

    start = sensor.get("start_cmd", "")
    if start:
        logger.info("[Phase2] Powering on sensor...")
        local_runner.run(f"{sensor_exec} {start}")
        mlflow_mgr.set_tag("sensor_on", "true")
        logger.info("[Phase2] Sensor powered on.")

    # ------------------------------------------------------------------ #
    # Persist state
    # ------------------------------------------------------------------ #
    state_mgr.transition_to_spun_up(SPINUP_SUBSYSTEMS)
    logger.info("[Phase2] Spin-up complete. Hardware is live.")


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _check_interlock(interlock_cfg: dict, local_runner):
    """
    Verify the sw_interlock process is running.
    We check by looking for the process name in the process list.
    """
    executable = interlock_cfg.get("executable", "")
    if not executable:
        logger.warning("[Phase2] No interlock config found — skipping check.")
        return

    # Extract script name for pgrep
    script_name = executable.split()[-1].split("/")[-1]
    result = local_runner.run(f"pgrep -f {script_name}", check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Software interlock is NOT running ({script_name})!\n"
            "Start it before spinning up hardware, or disable require_interlock in config."
        )
    logger.info("[Phase2] Interlock is alive.")
