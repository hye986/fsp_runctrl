"""
commands/_context.py

Shared helpers for subsystem command groups.

Server topology in config:

  general:
    control_server:          # hydra — where run_ctrl itself runs
      host: "localhost"
      venv: "~/vfsp"         # venv for all local tool commands
    daq_server:              # sulley — remote data acquisition PC
      host: "sulley"
      user: "tng"
      ssh_key: "~/.ssh/id_ed25519_sulley"
      venv: "~/venv_sulley"  # venv activated on sulley via SSH

Each runner is initialised with the venv of the server it targets,
so commands always run in the right environment without any manual
activation.
"""
from __future__ import annotations

import yaml


def load_cfg(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_runners(cfg: dict):
    """
    Return (local_runner, ssh_runner) built from control_server and
    daq_server config respectively.
    """
    from run_ctrl.core.runner import LocalRunner, SSHRunner
    general = cfg["general"]
    ctrl_srv = general.get("control_server", {})
    daq_srv  = general.get("daq_server", {})

    local = LocalRunner(venv=ctrl_srv.get("venv"))
    ssh = SSHRunner(
        host=daq_srv.get("host", "sulley"),
        user=daq_srv.get("user", "tng"),
        key_path=daq_srv.get("ssh_key", "~/.ssh/id_ed25519_sulley"),
        venv=daq_srv.get("venv"),
    )
    return local, ssh


def get_recorder_runner(cfg: dict):
    """
    Return LocalRunner or SSHRunner for the recorder subsystem.

    Venv resolution (most specific wins):
      subsystems.recorder.venv   → per-subsystem override
      daq_server.venv            → default for remote recorders
      control_server.venv        → default for local recorders
    """
    from run_ctrl.core.runner import LocalRunner, SSHRunner
    general  = cfg["general"]
    ctrl_srv = general.get("control_server", {})
    daq_srv  = general.get("daq_server", {})
    rec      = cfg["subsystems"]["recorder"]

    host = rec.get("host", "localhost")

    if host in ("localhost", "127.0.0.1", ""):
        venv = rec.get("venv") or ctrl_srv.get("venv")
        return LocalRunner(venv=venv)

    venv = rec.get("venv") or daq_srv.get("venv")
    return SSHRunner(
        host=host,
        user=rec.get("user", daq_srv.get("user", "tng")),
        key_path=daq_srv.get("ssh_key", "~/.ssh/id_ed25519_sulley"),
        venv=venv,
    )
