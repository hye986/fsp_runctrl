"""
core/config_archiver.py

Resolves and fetches configuration files for archiving in MLflow.

Supports three archive strategies defined in run_config.yaml:
  1. "tool_config_file" — open a tool's own YAML, extract key → filename,
                          fetch that file from a base path.
  2. "direct_file"      — archive a single local file directly.
  3. host + "files"     — fetch one or more files from a remote host via SSH.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class ConfigArchiver:
    def __init__(self, ssh_runner=None):
        """
        ssh_runner: an SSHRunner instance (needed for remote configs).
        """
        self._ssh = ssh_runner
        self._tmpdir = Path(tempfile.mkdtemp(prefix="run_ctrl_configs_"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def archive_all(self, config_archive_spec: dict, mlflow_manager) -> list[str]:
        """
        Walk the config_archive section from run_config.yaml,
        resolve and fetch every file, upload to MLflow.
        Returns list of local temp paths that were uploaded.
        """
        uploaded = []
        for name, spec in config_archive_spec.items():
            logger.info(f"[Archiver] Processing: {name}")
            try:
                local_paths = self._resolve(name, spec)
                for lp in local_paths:
                    mlflow_manager.log_artifact(str(lp), artifact_subdir=f"configs/{name}")
                    uploaded.append(str(lp))
            except Exception as e:
                logger.warning(f"[Archiver] Failed to archive '{name}': {e}")
        return uploaded

    def cleanup(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Resolution strategies
    # ------------------------------------------------------------------

    def _resolve(self, name: str, spec: dict) -> list[Path]:
        if "tool_config_file" in spec:
            return self._resolve_via_tool_config(name, spec)
        elif "direct_file" in spec:
            return self._resolve_direct(name, spec)
        elif "files" in spec and "host" in spec:
            return self._resolve_remote(name, spec)
        else:
            logger.warning(f"[Archiver] Unknown spec format for '{name}', skipping.")
            return []

    def _resolve_via_tool_config(self, name: str, spec: dict) -> list[Path]:
        """
        Strategy 1: read a tool's own config YAML, extract filenames from keys,
        then copy those files into the archive.

        Example spec:
          tool_config_file: "~/fsp_ctrlapp/configs/sys_configs.yaml"
          keys: ["s7", "chip_config"]
          files_base_path: "~/software/OpenOCD/drivers/spi_testing/"
        """
        tool_cfg_path = Path(os.path.expanduser(spec["tool_config_file"]))
        if not tool_cfg_path.exists():
            raise FileNotFoundError(f"Tool config not found: {tool_cfg_path}")

        with open(tool_cfg_path) as f:
            tool_cfg = yaml.safe_load(f)

        base_path = Path(os.path.expanduser(spec.get("files_base_path", ".")))
        keys = spec.get("keys", [])
        results = []

        # Always archive the tool config itself
        dest = self._tmpdir / name / tool_cfg_path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tool_cfg_path, dest)
        results.append(dest)

        # Extract and archive each referenced file
        for key in keys:
            filename = tool_cfg.get(key)
            if not filename:
                logger.warning(f"[Archiver] Key '{key}' not found in {tool_cfg_path.name}")
                continue
            src = base_path / filename
            if not src.exists():
                logger.warning(f"[Archiver] Referenced file not found: {src}")
                continue
            dest_file = self._tmpdir / name / filename
            shutil.copy2(src, dest_file)
            results.append(dest_file)
            logger.info(f"[Archiver] Resolved {key} → {filename}")

        return results

    def _resolve_direct(self, name: str, spec: dict) -> list[Path]:
        """
        Strategy 2: archive a single local file directly.

        Example spec:
          direct_file: "/etc/ccd/pwrapp_config.json"
        """
        src = Path(os.path.expanduser(spec["direct_file"]))
        if not src.exists():
            raise FileNotFoundError(f"Direct file not found: {src}")

        dest = self._tmpdir / name / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return [dest]

    def _resolve_remote(self, name: str, spec: dict) -> list[Path]:
        """
        Strategy 3: fetch files from a remote host via SSH.

        Example spec:
          host: "sulley"
          files:
            - "/home/ccd/recorder/recorder_config.json"
            - "/home/ccd/ana/ana.prm"
        """
        if self._ssh is None:
            raise RuntimeError("SSHRunner required for remote config archival but not provided.")

        dest_dir = self._tmpdir / name
        dest_dir.mkdir(parents=True, exist_ok=True)
        results = []

        for remote_path in spec["files"]:
            filename = Path(remote_path).name
            local_dest = dest_dir / filename
            self._ssh.get_file(remote_path, str(local_dest))
            results.append(local_dest)

        return results
