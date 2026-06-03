"""
commands/cmd_hardware.py

run_ctrl hv     — bias voltage ramp (special: up/down args, not subcommands)
run_ctrl sensor — sensor power (built from generic factory + init/start/stop)

HV is the only subsystem that doesn't fit the init/start/stop/run pattern
because it takes ramp_up_args / ramp_down_args rather than subcommands.
Everything else uses the generic factory.
"""
from __future__ import annotations

import click
from run_ctrl.commands._context import load_cfg, get_runners
from run_ctrl.commands.cmd_subsystem import make_subsystem_group

CTX = dict(help_option_names=["-h", "--help"])


# ================================================================== #
# HV — custom group (ramp_up_args / ramp_down_args, not subcommands)
# ================================================================== #

@click.group("hv", context_settings=CTX)
def hv_group():
    """Bias voltage ramp control."""
    pass


@hv_group.command("up", context_settings=CTX)
@click.pass_context
def hv_up(ctx):
    """Ramp HV up to target (blocking until done)."""
    cfg = load_cfg(ctx.obj["config_path"])
    local, _ = get_runners(cfg)
    hv = cfg["subsystems"]["hv"]
    cmd = f"{hv['executable']} {hv.get('ramp_up_args', '')}"
    click.echo(f"Ramping HV up: {cmd}")
    local.run(cmd, timeout=600)
    click.secho("✓ HV at target.", fg="green")


@hv_group.command("down", context_settings=CTX)
@click.pass_context
def hv_down(ctx):
    """Ramp HV down to zero (blocking until done)."""
    cfg = load_cfg(ctx.obj["config_path"])
    local, _ = get_runners(cfg)
    hv = cfg["subsystems"]["hv"]
    cmd = f"{hv['executable']} {hv.get('ramp_down_args', '')}"
    click.echo(f"Ramping HV down: {cmd}")
    local.run(cmd, timeout=600)
    click.secho("✓ HV at zero.", fg="green")


# ================================================================== #
# Sensor — generic factory (init / start / stop / run)
# ================================================================== #

sensor_group = make_subsystem_group(
    "sensor",
    "Sensor power control (ccd-pwr). 'stop': ensure HV=0 first!"
)
