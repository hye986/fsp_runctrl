"""
commands/cmd_recorder.py

run_ctrl recorder <subcommand>

Individual recorder commands. Dispatches to LocalRunner or SSHRunner
depending on recorder.host in config ("localhost" → local, anything else → SSH).

Usage:
  run_ctrl recorder dark
  run_ctrl recorder source              # full loop (all files)
  run_ctrl recorder source --file 2     # single file, 1-based index
  run_ctrl recorder open                # open source shutter
  run_ctrl recorder close               # close source shutter
"""
from __future__ import annotations

import click
from run_ctrl.commands._context import load_cfg, get_runners, get_recorder_runner
from run_ctrl.phases.phase3_daq import _interpolate

CTX = dict(help_option_names=["-h", "--help"])


@click.group("recorder", context_settings=CTX)
def recorder_group():
    """Recorder control (local or SSH depending on config)."""
    pass


@recorder_group.command("dark", context_settings=CTX)
@click.pass_context
def recorder_dark(ctx):
    """Run dark frame acquisition."""
    cfg = load_cfg(ctx.obj["config_path"])
    runner = get_recorder_runner(cfg)
    rec = cfg["subsystems"]["recorder"]
    run_cfg = cfg["run"]
    run_id, data_path = _get_run_context(cfg)

    dark_template = rec.get("dark_args", "")
    if not dark_template:
        click.secho("dark_args not configured in recorder subsystem.", fg="red")
        return

    cmd = rec["executable"] + " " + _interpolate(dark_template, run_cfg, run_id, data_path, file_index=0)
    click.echo(f"[recorder] dark: {cmd}")
    runner.run(cmd, timeout=None)
    click.secho("✓ Dark frames done.", fg="green")


@recorder_group.command("source", context_settings=CTX)
@click.option(
    "--file", "file_index",
    default=None, type=int,
    help="Run a single file by 1-based index. Omit to run the full loop."
)
@click.pass_context
def recorder_source(ctx, file_index):
    """Run source frame acquisition.

    \b
    Without --file: runs the full loop (source_file_nr iterations).
    With --file N:  runs only file N (useful for retaking a single file).

    Example:
      run_ctrl recorder source           # all 5 files
      run_ctrl recorder source --file 3  # only file 003
    """
    cfg = load_cfg(ctx.obj["config_path"])
    runner = get_recorder_runner(cfg)
    rec = cfg["subsystems"]["recorder"]
    run_cfg = cfg["run"]
    run_id, data_path = _get_run_context(cfg)

    source_template = rec.get("source_args", "")
    if not source_template:
        click.secho("source_args not configured in recorder subsystem.", fg="red")
        return

    if file_index is not None:
        _run_one_file(runner, rec, run_cfg, run_id, data_path, source_template, file_index)
    else:
        n = run_cfg.get("source_file_nr", 1)
        for i in range(1, n + 1):
            _run_one_file(runner, rec, run_cfg, run_id, data_path, source_template, i, total=n)
        click.secho(f"✓ All {n} source file(s) done.", fg="green")


@recorder_group.command("open", context_settings=CTX)
@click.pass_context
def recorder_open(ctx):
    """Open the source shutter."""
    cfg = load_cfg(ctx.obj["config_path"])
    runner = get_recorder_runner(cfg)
    cmd = cfg["subsystems"]["recorder"].get("source_open_cmd", "")
    if not cmd:
        click.secho("source_open_cmd not configured.", fg="yellow")
        return
    runner.run(cmd)
    click.secho("✓ Shutter open.", fg="green")


@recorder_group.command("close", context_settings=CTX)
@click.pass_context
def recorder_close(ctx):
    """Close the source shutter."""
    cfg = load_cfg(ctx.obj["config_path"])
    runner = get_recorder_runner(cfg)
    cmd = cfg["subsystems"]["recorder"].get("source_close_cmd", "")
    if not cmd:
        click.secho("source_close_cmd not configured.", fg="yellow")
        return
    runner.run(cmd)
    click.secho("✓ Shutter closed.", fg="green")


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _run_one_file(runner, rec, run_cfg, run_id, data_path, template, i, total=None):
    label = f"file {i}" if total is None else f"file {i}/{total}"
    cmd = rec["executable"] + " " + _interpolate(template, run_cfg, run_id, data_path, file_index=i)
    click.echo(f"[recorder] {label}: {cmd}")
    runner.run(cmd, timeout=None)
    click.secho(f"✓ {label} done.", fg="green")


def _get_run_context(cfg: dict) -> tuple[str, str]:
    """
    Load run_id and data_path from active state file.
    Falls back to placeholders if no active run (commissioning mode).
    """
    from run_ctrl.core.state_manager import StateManager
    state_file = cfg["general"].get("state_file", ".run_state.json")
    sm = StateManager(state_file)
    if sm.state.run_id:
        return sm.state.run_id, sm.state.data_path or cfg["run"]["data_path"]
    return "manual", cfg["run"]["data_path"]
