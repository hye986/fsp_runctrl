"""
commands/cmd_subsystem.py

Generic subsystem command group factory.

Every subsystem entry in run_config.yaml automatically gets these commands:

  run_ctrl <subsystem> init          — run init_cmd
  run_ctrl <subsystem> start         — run start_cmd (all entries in order)
  run_ctrl <subsystem> stop          — run stop_cmd
  run_ctrl <subsystem> run <subcmd>  — run executable + any subcommand

Subsystems with special arguments (hv, recorder) add extra commands
on top via make_hv_group() and the recorder module.

Adding a new subsystem to run_config.yaml → zero code changes needed.
"""
from __future__ import annotations

import click
from run_ctrl.commands._context import load_cfg, get_runners

CTX = dict(help_option_names=["-h", "--help"])


def make_subsystem_group(name: str, description: str = "") -> click.Group:
    """
    Build a Click group for a named subsystem.
    Reads init_cmd / start_cmd / stop_cmd from config at call time.
    """

    @click.group(name, context_settings=CTX)
    def grp():
        pass

    grp.__doc__ = description or f"{name} subsystem control."

    # ------------------------------------------------------------------ #
    # init
    # ------------------------------------------------------------------ #
    @grp.command("init", context_settings=CTX)
    @click.pass_context
    def _init(ctx):
        """Run init_cmd."""
        _run_cmd_key(ctx, name, "init_cmd")

    # ------------------------------------------------------------------ #
    # start
    # ------------------------------------------------------------------ #
    @grp.command("start", context_settings=CTX)
    @click.pass_context
    def _start(ctx):
        """Run start_cmd (all subcommands in order)."""
        _run_cmd_key(ctx, name, "start_cmd")

    # ------------------------------------------------------------------ #
    # stop
    # ------------------------------------------------------------------ #
    @grp.command("stop", context_settings=CTX)
    @click.pass_context
    def _stop(ctx):
        """Run stop_cmd."""
        _run_cmd_key(ctx, name, "stop_cmd")

    # ------------------------------------------------------------------ #
    # run <subcmd> — passthrough for any subcommand not covered above
    # ------------------------------------------------------------------ #
    @grp.command("run", context_settings=CTX)
    @click.argument("subcmd")
    @click.argument("extra_args", nargs=-1)
    @click.pass_context
    def _run(ctx, subcmd, extra_args):
        """Run any subcommand against this subsystem's executable.

        \b
        Example:
          run_ctrl daq run enable_testIn
          run_ctrl daq run start_testIn_sig --option value
        """
        cfg = load_cfg(ctx.obj["config_path"])
        local, _ = get_runners(cfg)
        sub = cfg["subsystems"].get(name, {})
        executable = sub.get("executable", "")
        if not executable:
            click.secho(f"No executable defined for subsystem '{name}'.", fg="red")
            return
        args = (" " + " ".join(extra_args)) if extra_args else ""
        local.run(f"{executable} {subcmd}{args}")

    return grp


def _run_cmd_key(ctx, subsystem_name: str, key: str):
    """
    Read config[subsystems][name][key] and run it via local runner.
    key value can be:
      str        → run as single subcommand against executable
      list[str]  → run each entry in order against executable
      ""  / []   → warn and skip
    """
    cfg = load_cfg(ctx.obj["config_path"])
    local, _ = get_runners(cfg)
    sub = cfg["subsystems"].get(subsystem_name, {})
    executable = sub.get("executable", "")
    value = sub.get(key)

    if not value:
        click.secho(
            f"'{key}' not defined (or empty) for subsystem '{subsystem_name}' — skipping.",
            fg="yellow"
        )
        return

    if isinstance(value, list):
        cmds = [v for v in value if v]   # skip empty strings
    else:
        cmds = [value]

    for subcmd in cmds:
        local.run(f"{executable} {subcmd}")

    click.secho(f"✓ {subsystem_name} {key} done.", fg="green")
