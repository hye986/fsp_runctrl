#!/usr/bin/env python3
"""
run_ctrl — pnCCD Detector Run Control CLI

Usage:
  run_ctrl [-h] COMMAND [OPTIONS]

Phase commands (full automated sequences):
  init        Phase 1 — create MLflow run, archive configs
  spinup      Phase 2 — DAQ, ASIC, HV ramp, sensor on
  start-daq   Phase 3 — dark + source frames on sulley
  teardown    Phase 4 — ramp down, power off, close run
  postproc    Phase 5 — elog + analysis (optional)
  status      Show current run state
  abort       Emergency safe shutdown
  reset       Clear state after completed/aborted run
  run CMD     Execute a custom command from config

Subsystem commands (individual steps, all support: init/start/stop/run):
  daq         DAQ chain    (fsp-ctrl: start-s7, start-daq, sync-s7, status)
  asic        ASIC         (fsp-ctrl: start_veritas, sync_s7, pwr_off_veritas)
  hv          Bias voltage (up, down)
  sensor      Sensor power (init, start, stop)
  recorder    Recorder     (dark, source [--file N], open, close, convert)
"""

import logging
import sys

import click
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# -h as alias for --help, applied to every command
CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


# ------------------------------------------------------------------ #
# Shared helpers
# ------------------------------------------------------------------ #

def _load_cfg(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def _build_context(cfg: dict):
    from run_ctrl.core.state_manager import StateManager
    from run_ctrl.core.mlflow_manager import MLflowManager
    from run_ctrl.core.runner import LocalRunner, SSHRunner

    general = cfg["general"]
    state_mgr = StateManager(general.get("state_file", ".run_state.json"))
    mlflow_mgr = MLflowManager(
        tracking_uri=general["mlflow_tracking_uri"],
        experiment_name=general["experiment_name"],
    )
    local_runner = LocalRunner()

    daq_srv = general.get("daq_server", {})
    ssh_runner = SSHRunner(
        host=daq_srv.get("host", "sulley"),
        user=daq_srv.get("user", "ccd_user"),
        key_path=general.get("ssh_key", "~/.ssh/id_ed25519_sulley"),
    )
    return state_mgr, mlflow_mgr, local_runner, ssh_runner


def _resume_if_active(mlflow_mgr, state_mgr):
    if state_mgr.state.run_id:
        mlflow_mgr.resume_run(state_mgr.state.run_id)


# ------------------------------------------------------------------ #
# CLI root
# ------------------------------------------------------------------ #

@click.group(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--config", "-c",
    default="config/run_config.yaml",
    show_default=True,
    help="Path to run_config.yaml",
)
@click.pass_context
def cli(ctx, config):
    """pnCCD Detector Run Control"""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


# ------------------------------------------------------------------ #
# Commands
# ------------------------------------------------------------------ #

@cli.command(context_settings=CONTEXT_SETTINGS)
@click.pass_context
def status(ctx):
    """Show current run state and phase."""
    cfg = _load_cfg(ctx.obj["config_path"])
    from run_ctrl.core.state_manager import StateManager
    state_mgr = StateManager(cfg["general"].get("state_file", ".run_state.json"))

    click.echo("\n── Run Control Status ─────────────────────────────")
    if state_mgr.is_idle():
        click.echo("  No active run.  Use 'run_ctrl init' to start one.")
    else:
        click.echo(state_mgr.summary())
    click.echo("────────────────────────────────────────────────────\n")


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.pass_context
def init(ctx):
    """Phase 1: Create MLflow run, archive configs, log parameters."""
    cfg = _load_cfg(ctx.obj["config_path"])
    state_mgr, mlflow_mgr, local_runner, ssh_runner = _build_context(cfg)

    from run_ctrl.phases.phase1_init import run_init
    try:
        run_id = run_init(cfg, state_mgr, mlflow_mgr, ssh_runner=ssh_runner)
        click.secho(f"\n✓ Run initialized. ID: {run_id}\n", fg="green")
    except Exception as e:
        click.secho(f"\n✗ Init failed: {e}\n", fg="red")
        sys.exit(1)


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.pass_context
def spinup(ctx):
    """Phase 2: Start DAQ, ASIC, ramp HV, power on sensor."""
    cfg = _load_cfg(ctx.obj["config_path"])
    state_mgr, mlflow_mgr, local_runner, ssh_runner = _build_context(cfg)
    _resume_if_active(mlflow_mgr, state_mgr)

    from run_ctrl.phases.phase2_spinup import run_spinup
    try:
        run_spinup(cfg, state_mgr, mlflow_mgr, local_runner)
        click.secho("\n✓ Hardware spun up. Ready for data acquisition.\n", fg="green")
    except Exception as e:
        click.secho(f"\n✗ Spinup failed: {e}\n", fg="red")
        sys.exit(1)


@cli.command("start-daq", context_settings=CONTEXT_SETTINGS)
@click.pass_context
def start_daq(ctx):
    """Phase 3: Dark frames then source frames on sulley (blocking)."""
    cfg = _load_cfg(ctx.obj["config_path"])
    state_mgr, mlflow_mgr, local_runner, ssh_runner = _build_context(cfg)
    _resume_if_active(mlflow_mgr, state_mgr)

    from run_ctrl.phases.phase3_daq import run_daq
    try:
        run_daq(cfg, state_mgr, mlflow_mgr, ssh_runner)
        click.secho("\n✓ Data acquisition complete.\n", fg="green")
    except Exception as e:
        click.secho(f"\n✗ DAQ failed: {e}\n", fg="red")
        sys.exit(1)


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.pass_context
def teardown(ctx):
    """Phase 4: Power down hardware AND close MLflow run (stop + powerdown)."""
    cfg = _load_cfg(ctx.obj["config_path"])
    state_mgr, mlflow_mgr, local_runner, ssh_runner = _build_context(cfg)
    _resume_if_active(mlflow_mgr, state_mgr)

    from run_ctrl.phases.phase4_teardown import run_teardown
    try:
        run_teardown(cfg, state_mgr, mlflow_mgr, local_runner)
        click.secho("\n✓ Teardown complete. Run closed.\n", fg="green")
    except Exception as e:
        click.secho(f"\n✗ Teardown failed: {e}\n", fg="red")
        sys.exit(1)


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.pass_context
def stop(ctx):
    """Close MLflow run and reset state — hardware left running.

    \b
    Use this between runs when you want to adjust parameters and
    start a new run without powering anything off.
    Afterwards: run_ctrl reset → run_ctrl init → run_ctrl start-daq
    """
    cfg = _load_cfg(ctx.obj["config_path"])
    state_mgr, mlflow_mgr, local_runner, ssh_runner = _build_context(cfg)
    _resume_if_active(mlflow_mgr, state_mgr)

    from run_ctrl.phases.phase4_teardown import run_stop
    try:
        run_stop(cfg, state_mgr, mlflow_mgr)
        click.secho("\n✓ Run stopped. Hardware left on.\n", fg="green")
        click.echo("  Next: run_ctrl reset → run_ctrl init → run_ctrl start-daq")
    except Exception as e:
        click.secho(f"\n✗ Stop failed: {e}\n", fg="red")
        sys.exit(1)


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.pass_context
def powerdown(ctx):
    """Power off hardware (HV → sensor → ASIC). No MLflow interaction.

    \b
    Can be called at any time, independently of run state.
    DAQ is left running.
    """
    cfg = _load_cfg(ctx.obj["config_path"])
    state_mgr, mlflow_mgr, local_runner, ssh_runner = _build_context(cfg)

    from run_ctrl.phases.phase4_teardown import run_powerdown
    try:
        run_powerdown(cfg, local_runner)
        click.secho("\n✓ Hardware powered down.\n", fg="green")
    except Exception as e:
        click.secho(f"\n✗ Powerdown failed: {e}\n", fg="red")
        sys.exit(1)


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option("--label", "-l", default="snapshot", show_default=True,
              help="Label for this snapshot run in MLflow.")
@click.pass_context
def snapshot(ctx, label):
    """Archive current configs to MLflow — no hardware, no state change.

    \b
    Opens a short MLflow run, saves all config files, then closes.
    Use after adjusting parameters mid-session to track what changed,
    without starting a full data-taking run.
    """
    cfg = _load_cfg(ctx.obj["config_path"])
    state_mgr, mlflow_mgr, local_runner, ssh_runner = _build_context(cfg)

    from run_ctrl.phases.phase4_teardown import run_snapshot
    try:
        run_id = run_snapshot(cfg, mlflow_mgr, ssh_runner=ssh_runner, label=label)
        click.secho(f"\n✓ Snapshot saved. MLflow run: {run_id}\n", fg="green")
    except Exception as e:
        click.secho(f"\n✗ Snapshot failed: {e}\n", fg="red")
        sys.exit(1)


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option("--no-elog",     is_flag=True, default=False, help="Skip elog posting")
@click.option("--no-analysis", is_flag=True, default=False, help="Skip analysis trigger")
@click.option("--comment", "-m", default="", help="Operator comment for elog entry")
@click.pass_context
def postproc(ctx, no_elog, no_analysis, comment):
    """Phase 5: Post elog entry and trigger analysis on sulley."""
    cfg = _load_cfg(ctx.obj["config_path"])
    state_mgr, mlflow_mgr, local_runner, ssh_runner = _build_context(cfg)

    from run_ctrl.phases.phase5_postproc import run_postproc
    try:
        run_postproc(
            cfg, state_mgr,
            mlflow_mgr=mlflow_mgr,
            ssh_runner=ssh_runner,
            do_elog=not no_elog,
            do_analysis=not no_analysis,
            elog_comment=comment,
        )
        click.secho("\n✓ Post-processing done.\n", fg="green")
    except Exception as e:
        click.secho(f"\n✗ Post-processing failed: {e}\n", fg="red")
        sys.exit(1)


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.confirmation_option(prompt="Emergency abort: are you sure?")
@click.pass_context
def abort(ctx):
    """Emergency: safe shutdown sequence from any phase."""
    cfg = _load_cfg(ctx.obj["config_path"])
    state_mgr, mlflow_mgr, local_runner, ssh_runner = _build_context(cfg)

    try:
        _resume_if_active(mlflow_mgr, state_mgr)
    except Exception:
        pass

    from run_ctrl.phases.phase4_teardown import run_abort
    run_abort(cfg, state_mgr, mlflow_mgr, local_runner)
    click.secho("\n⚠ Abort complete. Verify hardware state manually.\n", fg="yellow")


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.confirmation_option(prompt="Clear run state?")
@click.pass_context
def reset(ctx):
    """Clear state file after a completed or aborted run."""
    cfg = _load_cfg(ctx.obj["config_path"])
    from run_ctrl.core.state_manager import StateManager
    StateManager(cfg["general"].get("state_file", ".run_state.json")).reset()
    click.secho("\n✓ State cleared. Ready for a new run.\n", fg="green")


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.argument("command_name")
@click.pass_context
def run(ctx, command_name):
    """Execute a custom command defined under custom_commands in config."""
    cfg = _load_cfg(ctx.obj["config_path"])
    custom = cfg.get("custom_commands", {})
    if command_name not in custom:
        available = ", ".join(custom.keys()) or "(none defined)"
        click.secho(f"Unknown command '{command_name}'. Available: {available}", fg="red")
        sys.exit(1)

    from run_ctrl.core.runner import LocalRunner
    cmd = custom[command_name]
    click.echo(f"Running: {cmd}")
    LocalRunner().run(cmd, check=False)


# ------------------------------------------------------------------ #
# Subsystem command groups
# ------------------------------------------------------------------ #

from run_ctrl.commands.cmd_daq import daq_group, asic_group
from run_ctrl.commands.cmd_hardware import hv_group, sensor_group
from run_ctrl.commands.cmd_recorder import recorder_group

cli.add_command(daq_group)
cli.add_command(asic_group)
cli.add_command(hv_group)
cli.add_command(sensor_group)
cli.add_command(recorder_group)
