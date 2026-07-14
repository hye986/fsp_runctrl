"""
commands/cmd_recorder.py

Individual recorder commands. Dispatches to LocalRunner or SSHRunner
depending on recorder.host in config ("localhost" → local, else SSH).

Re-run behaviour
----------------
Running recorder dark or source multiple times is safe — existing files
are detected and new files continue from the next available index:

  First run:   dark_000.raw
  Second run:  dark_001.raw   (not an overwrite)

  source first run:   data_run001.raw ... data_run005.raw
  source second run:  data_run006.raw ... data_run010.raw  (continues)

  recorder source --file 3  always writes exactly file 003 (explicit override)

State integration
-----------------
When an active MLflow run exists (state phase >= INITIALIZED):
  - recorder dark   → advances state to ACQUIRING, marks dark_done
  - recorder source → marks source_done on full loop completion
  - Output directory is created before recording starts

When no active run (commissioning / manual mode):
  - Commands run using run_id="manual" and raw data_path from config
  - State is not touched
"""
from __future__ import annotations

import logging
import re
import os
from pathlib import Path

import click
from run_ctrl.commands._context import load_cfg, get_runners, get_recorder_runner
from run_ctrl.phases.phase3_daq import _interpolate

logger = logging.getLogger(__name__)
CTX = dict(help_option_names=["-h", "--help"])


@click.group("recorder", context_settings=CTX)
def recorder_group():
    """Recorder control (local or SSH depending on config)."""
    pass


# ================================================================== #
# dark
# ================================================================== #

@recorder_group.command("dark", context_settings=CTX)
@click.option("--index", default=None, type=int,
              help="Force a specific file index. Default: auto (next available).")
@click.pass_context
def recorder_dark(ctx, index):
    """Run dark frame acquisition.

    \b
    Automatically continues from the last existing dark file index.
    Run twice → dark_000.raw then dark_001.raw (no overwrite).
    Use --index N to force a specific index.
    """
    cfg     = load_cfg(ctx.obj["config_path"])
    runner  = get_recorder_runner(cfg)
    rec     = cfg["subsystems"]["recorder"]
    run_cfg = cfg["run"]
    state_mgr, run_id, data_path = _get_state(cfg)

    dark_template = rec.get("dark_args", "")
    if not dark_template:
        click.secho("dark_args not configured in recorder subsystem.", fg="red")
        return

    _ensure_dir(runner, data_path)

    # Resolve file index
    if index is not None:
        file_index = index
        click.echo(f"  dark index: {file_index:03d}  (forced)")
    else:
        file_index = _next_index(runner, data_path, dark_template, run_cfg,
                                 run_id, is_dark=True)
        click.echo(f"  dark index: {file_index:03d}  "
                   f"({'first file' if file_index == 0 else f'continuing from {file_index}'})")

    # Advance state
    if state_mgr and _is_active(state_mgr):
        from run_ctrl.core.state_manager import Phase
        if state_mgr.current_phase() == Phase.INITIALIZED:
            state_mgr.transition_to_spun_up([])
        if state_mgr.current_phase() == Phase.SPUN_UP:
            state_mgr.transition_to_acquiring()

    cmd = rec["executable"] + " " + _interpolate(
        dark_template, run_cfg, run_id, data_path, file_index=file_index
    )
    click.echo(f"[recorder] dark: {cmd}")
    runner.run(cmd, timeout=None)

    if state_mgr and _is_active(state_mgr):
        state_mgr.mark_dark_done()

    click.secho("✓ Dark frames done.", fg="green")


# ================================================================== #
# source
# ================================================================== #

@recorder_group.command("source", context_settings=CTX)
@click.option("--file", "file_index", default=None, type=int,
              help="Run a single file at this exact 1-based index.")
@click.option("--count", default=None, type=int,
              help="Number of files to record. Default: source_file_nr from config.")
@click.pass_context
def recorder_source(ctx, file_index, count):
    """Run source frame acquisition.

    \b
    Default: records source_file_nr files, starting after any existing files.

      First run (source_file_nr=5):   001 002 003 004 005
      Second run:                      006 007 008 009 010

    --file N   record exactly file N (explicit, no auto-detection)
    --count N  record N files from the next available index
    """
    cfg     = load_cfg(ctx.obj["config_path"])
    runner  = get_recorder_runner(cfg)
    rec     = cfg["subsystems"]["recorder"]
    run_cfg = cfg["run"]
    state_mgr, run_id, data_path = _get_state(cfg)

    source_template = rec.get("source_args", "")
    if not source_template:
        click.secho("source_args not configured in recorder subsystem.", fg="red")
        return

    _ensure_dir(runner, data_path)

    if file_index is not None:
        # Explicit single file — no auto-detection
        click.echo(f"  source index: {file_index:03d}  (forced)")
        _run_one_file(runner, rec, run_cfg, run_id, data_path,
                      source_template, file_index)
    else:
        # Auto-detect start index, then run `count` files
        n       = count if count is not None else run_cfg.get("source_file_nr", 1)
        start   = _next_index(runner, data_path, source_template, run_cfg,
                               run_id, is_dark=False)
        end     = start + n - 1

        if start > 1:
            click.echo(f"  existing files detected — continuing from {start:03d} to {end:03d}")
        else:
            click.echo(f"  source files: {start:03d} → {end:03d}")

        for i in range(start, start + n):
            _run_one_file(runner, rec, run_cfg, run_id, data_path,
                          source_template, i, total=n, start=start)

        if state_mgr and _is_active(state_mgr):
            state_mgr.mark_source_done()

        click.secho(f"✓ {n} source file(s) done ({start:03d}–{end:03d}).", fg="green")


# ================================================================== #
# shutter
# ================================================================== #

@recorder_group.command("open", context_settings=CTX)
@click.pass_context
def recorder_open(ctx):
    """Open the source shutter."""
    cfg    = load_cfg(ctx.obj["config_path"])
    runner = get_recorder_runner(cfg)
    cmd    = cfg["subsystems"]["recorder"].get("source_open_cmd", "")
    if not cmd:
        click.secho("source_open_cmd not configured.", fg="yellow")
        return
    runner.run(cmd)
    click.secho("✓ Shutter open.", fg="green")


@recorder_group.command("close", context_settings=CTX)
@click.pass_context
def recorder_close(ctx):
    """Close the source shutter."""
    cfg    = load_cfg(ctx.obj["config_path"])
    runner = get_recorder_runner(cfg)
    cmd    = cfg["subsystems"]["recorder"].get("source_close_cmd", "")
    if not cmd:
        click.secho("source_close_cmd not configured.", fg="yellow")
        return
    runner.run(cmd)
    click.secho("✓ Shutter closed.", fg="green")


# ================================================================== #
# convert
# ================================================================== #

@recorder_group.command("convert", context_settings=CTX)
@click.pass_context
def recorder_convert(ctx):
    """Run data conversion on recorded frames.

    \b
    Uses convert_args template from config.
    Supports {data_path} and all other interpolation placeholders.
    """
    cfg     = load_cfg(ctx.obj["config_path"])
    runner  = get_recorder_runner(cfg)
    rec     = cfg["subsystems"]["recorder"]
    run_cfg = cfg["run"]
    state_mgr, run_id, data_path = _get_state(cfg)

    convert_template = rec.get("convert_args", "")
    if not convert_template:
        click.secho("convert_args not configured in recorder subsystem.", fg="red")
        return

    cmd = rec["executable"] + " " + _interpolate(
        convert_template, run_cfg, run_id, data_path
    )
    click.echo(f"[recorder] convert: {cmd}")
    runner.run(cmd, timeout=None)
    click.secho("✓ Conversion done.", fg="green")


# ================================================================== #
# convert_clean
# ================================================================== #

@recorder_group.command("convert_clean", context_settings=CTX)
@click.pass_context
def recorder_convert_clean(ctx):
    """Run alternative data conversion using a different executable.

    \b
    Uses convert_clean_executable and convert_clean_args from config.
    Supports {data_path} and all other interpolation placeholders.
    """
    cfg     = load_cfg(ctx.obj["config_path"])
    runner  = get_recorder_runner(cfg)
    rec     = cfg["subsystems"]["recorder"]
    run_cfg = cfg["run"]
    state_mgr, run_id, data_path = _get_state(cfg)

    convert_clean_executable = rec.get("convert_clean_executable", "")
    convert_clean_template    = rec.get("convert_clean_args", "")

    if not convert_clean_executable:
        click.secho("convert_clean_executable not configured in recorder subsystem.", fg="red")
        return
    if not convert_clean_template:
        click.secho("convert_clean_args not configured in recorder subsystem.", fg="red")
        return

    cmd = convert_clean_executable + " " + _interpolate(
        convert_clean_template, run_cfg, run_id, data_path
    )
    click.echo(f"[recorder] convert_clean: {cmd}")
    runner.run(cmd, timeout=None)
    click.secho("✓ Alternative conversion done.", fg="green")


# ================================================================== #
# Helpers
# ================================================================== #

def _next_index(runner, data_path: str, template: str, run_cfg: dict,
                run_id: str, is_dark: bool) -> int:
    """
    Scan the output directory for existing files matching the template pattern
    and return the next available index (max_existing + 1, or 0 for dark).

    Strategy: render the template with a sentinel index, extract the
    filename prefix up to {file_index_pad}, then use ls/find to list
    matching files and parse the highest index present.
    """
    try:
        # Render template with index=9999 to get the full filename shape,
        # then extract the part before '9999' as a prefix to glob on.
        sample = _interpolate(template, run_cfg, run_id, data_path, file_index=9999)
        # Find the token containing data_path (the output file argument)
        tokens = sample.split()
        sample_filename = next(
            (t for t in tokens if data_path.rstrip("/") in t),
            None
        )
        if sample_filename is None:
            logger.warning("[recorder] Could not locate output path in template args")
            return 0 if is_dark else 1
        # Remove data_path prefix to get relative filename
        rel = sample_filename.replace(data_path.rstrip("/") + "/", "")
        prefix = rel.split("9999")[0]          # everything before the index digits

        # List files on target host
        files = _list_files(runner, data_path)

        # Find all files matching our prefix pattern
        indices = []
        for f in files:
            fname = Path(f).name
            if fname.startswith(prefix):
                # Extract the numeric part right after prefix
                rest = fname[len(prefix):]
                m = re.match(r"(\d+)", rest)
                if m:
                    indices.append(int(m.group(1)))

        if not indices:
            return 0 if is_dark else 1

        next_idx = max(indices) + 1
        return next_idx

    except Exception as e:
        logger.warning(f"[recorder] Could not scan existing files: {e} — starting from default")
        return 0 if is_dark else 1


def _list_files(runner, data_path: str) -> list[str]:
    """List filenames in data_path on the target host."""
    try:
        if hasattr(runner, 'conn'):
            # SSHRunner — run ls remotely
            result = runner.conn.run(
                f"ls {data_path}/ 2>/dev/null", hide=True, warn=True
            )
            return result.stdout.strip().splitlines()
        else:
            # LocalRunner — use pathlib
            p = Path(os.path.expanduser(data_path))
            if p.exists():
                return [f.name for f in p.iterdir() if f.is_file()]
            return []
    except Exception as e:
        logger.warning(f"[recorder] Could not list {data_path}: {e}")
        return []


def _get_state(cfg: dict):
    """
    Return (state_mgr, run_id, data_path).
    If no active run: state_mgr=None, run_id="manual", data_path from config.
    """
    from run_ctrl.core.state_manager import StateManager
    from run_ctrl.cli import _find_config

    config_path = _find_config("__auto__")
    state_file  = cfg["general"].get("state_file", ".run_state.json")
    if not Path(state_file).is_absolute():
        state_file = str(Path(config_path).parent / state_file)

    sm = StateManager(state_file)
    if sm.state.run_id:
        data_path = sm.state.data_path or cfg["run"]["data_path"]
        return sm, sm.state.run_id, data_path

    click.echo("  (no active run — manual mode, state not updated)", err=True)
    return None, "manual", cfg["run"]["data_path"]


def _is_active(state_mgr) -> bool:
    from run_ctrl.core.state_manager import Phase
    return state_mgr.current_phase() not in (
        Phase.IDLE, Phase.TORN_DOWN, Phase.ABORTED
    )


def _ensure_dir(runner, data_path: str):
    """Create the output directory on the target host if it doesn't exist."""
    if not data_path or data_path == "manual":
        return
    try:
        runner.run(f"mkdir -p {data_path}", check=False)
        logger.info(f"[recorder] Output dir ready: {data_path}")
    except Exception as e:
        logger.warning(f"[recorder] Could not create output dir {data_path}: {e}")


def _run_one_file(runner, rec, run_cfg, run_id, data_path, template,
                  i, total=None, start=None):
    if total is not None and start is not None:
        done = i - start + 1
        label = f"file {i:03d}  ({done}/{total})"
    elif total is not None:
        label = f"file {i:03d}  ({i}/{total})"
    else:
        label = f"file {i:03d}"

    cmd = rec["executable"] + " " + _interpolate(
        template, run_cfg, run_id, data_path, file_index=i
    )
    click.echo(f"[recorder] {label}: {cmd}")
    runner.run(cmd, timeout=None)
    click.secho(f"✓ {label} done.", fg="green")
