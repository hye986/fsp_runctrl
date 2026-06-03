"""
core/runner.py

Unified command executor:
  - Local commands  → subprocess (shell=True, live output)
  - Remote commands → Fabric SSH connection

Virtualenv support
------------------
Both runners accept an optional `venv` path. When set, the command is
prefixed with `. {venv}/bin/activate &&` so the correct interpreter and
all installed packages are available — without needing to hardcode the
full venv python path or modify .bashrc on any host.

  local:  `. ~/vfsp/bin/activate && ccd-pwr pwr_up`
  remote: `. ~/vfsp/bin/activate && python udp_recorder.py ...`

The venv path is typically read from the subsystem config and passed
through _get_recorder_runner() / _sensor_cmd() etc. — callers don't
need to know the details.
"""

from __future__ import annotations

import os
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class RunnerError(Exception):
    """Raised when a command exits with non-zero status."""


def _with_venv(cmd: str, venv: Optional[str]) -> str:
    """Prepend venv activation to a shell command string if venv is set."""
    if not venv:
        return cmd
    venv_path = os.path.expanduser(venv)
    return f". {venv_path}/bin/activate && {cmd}"


class LocalRunner:
    """Runs commands on the local machine (hydra)."""

    def __init__(self, venv: Optional[str] = None):
        """
        venv: path to virtualenv on hydra, e.g. "~/software/fsp_system_test/vfsp"
              If set, every command is prefixed with `. {venv}/bin/activate &&`
        """
        self.venv = venv

    def run(
        self,
        cmd: str | list,
        timeout: Optional[int] = None,
        check: bool = True,
        venv: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        """
        Run a local command.

        str  → passed to shell (shell=True). ~ is expanded.
        list → joined to string, then treated as str (so venv prefix works).

        venv arg overrides the instance-level venv for a single call.
        Output streams live to the terminal.
        """
        active_venv = venv if venv is not None else self.venv

        if isinstance(cmd, list):
            cmd = " ".join(cmd)

        cmd = os.path.expanduser(cmd)
        cmd = _with_venv(cmd, active_venv)
        display = cmd

        logger.info(f"[LOCAL] $ {display}")
        result = subprocess.run(
            cmd,
            shell=True,
            timeout=timeout,
        )

        if check and result.returncode != 0:
            raise RunnerError(
                f"Command failed (exit {result.returncode}): {display}"
            )
        return result

    def run_subcommands(
        self,
        executable: str,
        subcommands: str | list,
        timeout: Optional[int] = None,
    ):
        """
        Run one or more subcommands against the same executable.
        e.g. executable='fsp-ctrl', subcommands=['start_s7', 'start_daq']
        → fsp-ctrl start_s7  then  fsp-ctrl start_daq
        Both share the instance venv.
        """
        if isinstance(subcommands, str):
            subcommands = [subcommands]
        for subcmd in subcommands:
            if not subcmd:
                logger.info(f"[LOCAL] Skipping empty subcommand for {executable}")
                continue
            self.run(f"{executable} {subcmd}", timeout=timeout)

    def check_process_running(self, pattern: str) -> bool:
        """Return True if a process matching pattern is found via pgrep."""
        result = self.run(f"pgrep -f {pattern}", check=False)
        return result.returncode == 0


class SSHRunner:
    """
    Runs commands on a remote host via SSH using Fabric.
    Connection is established lazily and reused.
    """

    def __init__(
        self,
        host: str,
        user: str,
        key_path: Optional[str] = None,
        venv: Optional[str] = None,
    ):
        """
        venv: path to virtualenv on the *remote* host.
              If set, every command is prefixed with `. {venv}/bin/activate &&`
              This handles SSH non-interactive sessions that don't source .bashrc.
        """
        self.host = host
        self.user = user
        self.key_path = key_path
        self.venv = venv
        self._conn = None

    def _connect(self):
        try:
            from fabric import Connection
        except ImportError:
            raise ImportError("Fabric is required for SSH: pip install fabric")

        key = os.path.expanduser(self.key_path) if self.key_path else None
        connect_kwargs = {"key_filename": key} if key else {}
        self._conn = Connection(
            host=self.host,
            user=self.user,
            connect_kwargs=connect_kwargs,
        )
        logger.info(f"[SSH] Connected to {self.user}@{self.host}")

    @property
    def conn(self):
        if self._conn is None:
            self._connect()
        return self._conn

    def run(
        self,
        cmd: str,
        timeout: Optional[int] = None,
        hide: bool = False,
        venv: Optional[str] = None,
    ) -> "fabric.Result":
        """
        Run a command on the remote host.
        venv arg overrides the instance-level venv for a single call.
        hide=False → output streams live to terminal (default).
        """
        active_venv = venv if venv is not None else self.venv
        cmd = _with_venv(cmd, active_venv)

        logger.info(f"[SSH:{self.host}] $ {cmd}")
        result = self.conn.run(cmd, hide=hide, warn=True, timeout=timeout)
        if result.return_code != 0:
            raise RunnerError(
                f"Remote command failed (exit {result.return_code}): {cmd}\n"
                f"stderr: {result.stderr.strip()}"
            )
        return result

    def run_background(self, cmd: str, venv: Optional[str] = None):
        """Run a long-duration command in the background (nohup) on remote."""
        active_venv = venv if venv is not None else self.venv
        cmd = _with_venv(cmd, active_venv)
        bg_cmd = f"nohup {cmd} > /tmp/run_ctrl_bg.log 2>&1 &"
        logger.info(f"[SSH:{self.host}] [BACKGROUND] $ {cmd}")
        self.conn.run(bg_cmd, hide=True, disown=True)

    def get_file(self, remote_path: str, local_path: str):
        """Download a file from the remote host."""
        logger.info(f"[SSH:{self.host}] GET {remote_path} → {local_path}")
        self.conn.get(remote_path, local=local_path)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
