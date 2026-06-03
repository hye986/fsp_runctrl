"""
commands/cmd_daq.py

run_ctrl daq  — DAQ chain (fsp-ctrl)
run_ctrl asic — ASIC control (fsp-ctrl)

Both are built from the generic subsystem group factory, which provides:
  init / start / stop / run <subcmd>

Extra daq-specific commands are added on top:
  run_ctrl daq start-s7    — run only the start_s7 step
  run_ctrl daq start-daq   — run only the start_daq step
  run_ctrl daq sync-s7     — fsp-ctrl sync_s7
  run_ctrl daq status      — fsp-ctrl status
"""
from __future__ import annotations

import click
from run_ctrl.commands._context import load_cfg, get_runners
from run_ctrl.commands.cmd_subsystem import make_subsystem_group

CTX = dict(help_option_names=["-h", "--help"])

# ------------------------------------------------------------------ #
# DAQ group
# ------------------------------------------------------------------ #

daq_group = make_subsystem_group(
    "daq",
    "DAQ chain control (fsp-ctrl). 'start' runs all start_cmd entries in order."
)


@daq_group.command("start-s7", context_settings=CTX)
@click.pass_context
def daq_start_s7(ctx):
    """Run only the start_s7 step (fsp-ctrl start_s7)."""
    _fsp(ctx, "start_s7")


@daq_group.command("start-daq", context_settings=CTX)
@click.pass_context
def daq_start_daq(ctx):
    """Run only the start_daq step (fsp-ctrl start_daq)."""
    _fsp(ctx, "start_daq")


@daq_group.command("sync-s7", context_settings=CTX)
@click.pass_context
def daq_sync_s7(ctx):
    """Sync ASIC with S7 (fsp-ctrl sync_s7)."""
    _fsp(ctx, "sync_s7")


@daq_group.command("status", context_settings=CTX)
@click.pass_context
def daq_status(ctx):
    """DAQ chain status (fsp-ctrl status)."""
    _fsp(ctx, "status")


def _fsp(ctx, subcmd: str):
    cfg = load_cfg(ctx.obj["config_path"])
    local, _ = get_runners(cfg)
    exe = cfg["subsystems"]["daq"]["executable"]
    local.run(f"{exe} {subcmd}")


# ------------------------------------------------------------------ #
# ASIC group — separate subsystem, separate command group
# ------------------------------------------------------------------ #

asic_group = make_subsystem_group(
    "asic",
    "ASIC control (fsp-ctrl). 'start' runs start_veritas + sync_s7. 'stop' powers off."
)
