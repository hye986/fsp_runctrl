"""
core/state_manager.py

Persists run state to a local JSON file on hydra.
This is the single source of truth for "is there an active run?"
and "which phase/subsystems are currently live?"
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class Phase(str, Enum):
    IDLE = "idle"
    INITIALIZED = "initialized"   # MLflow run created, configs archived
    SPUN_UP = "spun_up"           # Hardware online
    ACQUIRING = "acquiring"       # Data taking on sulley
    TORN_DOWN = "torn_down"       # Hardware powered down, run ended
    ABORTED = "aborted"


@dataclass
class RunState:
    phase: Phase = Phase.IDLE
    run_id: Optional[str] = None          # MLflow run_id
    label: Optional[str] = None
    started_at: Optional[str] = None
    phase_timestamps: dict = field(default_factory=dict)

    # Which subsystems are currently powered on
    subsystems_on: list = field(default_factory=list)

    # DAQ progress flags
    dark_done: bool = False
    source_done: bool = False

    # Optional: path to data on sulley
    data_path: Optional[str] = None


class StateManager:
    def __init__(self, state_file: str = ".run_state.json"):
        self.state_file = Path(state_file)
        self._state: RunState = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> RunState:
        if self.state_file.exists():
            with open(self.state_file) as f:
                data = json.load(f)
            data["phase"] = Phase(data.get("phase", "idle"))
            return RunState(**data)
        return RunState()

    def _save(self):
        d = asdict(self._state)
        d["phase"] = self._state.phase.value
        with open(self.state_file, "w") as f:
            json.dump(d, f, indent=2)

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def state(self) -> RunState:
        return self._state

    def is_idle(self) -> bool:
        return self._state.phase == Phase.IDLE

    def has_active_run(self) -> bool:
        return self._state.phase not in (Phase.IDLE, Phase.TORN_DOWN, Phase.ABORTED)

    def current_phase(self) -> Phase:
        return self._state.phase

    # ------------------------------------------------------------------
    # Transitions — each one validates the predecessor
    # ------------------------------------------------------------------

    def transition_to_initialized(self, run_id: str, label: str, data_path: str):
        self._assert_phase(Phase.IDLE, "init")
        self._state.run_id = run_id
        self._state.label = label
        self._state.started_at = datetime.now().isoformat()
        self._state.data_path = data_path
        self._state.phase = Phase.INITIALIZED
        self._stamp("initialized")
        self._save()

    def transition_to_spun_up(self, subsystems: list[str]):
        self._assert_phase(Phase.INITIALIZED, "spinup")
        self._state.subsystems_on = subsystems
        self._state.phase = Phase.SPUN_UP
        self._stamp("spun_up")
        self._save()

    def transition_to_acquiring(self):
        self._assert_phase(Phase.SPUN_UP, "start-daq")
        self._state.phase = Phase.ACQUIRING
        self._stamp("acquiring")
        self._save()

    def mark_dark_done(self):
        self._state.dark_done = True
        self._save()

    def mark_source_done(self):
        self._state.source_done = True
        self._save()

    def transition_to_torn_down(self):
        """Valid from ACQUIRING or SPUN_UP — hardware may still be on."""
        self._assert_phase_in(
            [Phase.ACQUIRING, Phase.SPUN_UP],
            "stop/teardown"
        )
        self._state.phase = Phase.TORN_DOWN
        self._stamp("torn_down")
        self._save()

    def transition_to_aborted(self):
        """Can be called from any phase."""
        self._state.phase = Phase.ABORTED
        self._stamp("aborted")
        self._save()

    def reset(self):
        """Clear state after a completed or aborted run."""
        self._assert_phase_in(
            [Phase.TORN_DOWN, Phase.ABORTED],
            "reset (run must be torn down or aborted first)"
        )
        self.state_file.unlink(missing_ok=True)
        self._state = RunState()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _stamp(self, label: str):
        self._state.phase_timestamps[label] = datetime.now().isoformat()

    def _assert_phase(self, expected: Phase, cmd: str):
        if self._state.phase != expected:
            raise RuntimeError(
                f"Cannot run '{cmd}': current phase is '{self._state.phase.value}', "
                f"expected '{expected.value}'."
            )

    def _assert_phase_in(self, expected: list[Phase], cmd: str):
        if self._state.phase not in expected:
            names = [p.value for p in expected]
            raise RuntimeError(
                f"Cannot run '{cmd}': current phase is '{self._state.phase.value}', "
                f"expected one of {names}."
            )

    def summary(self) -> str:
        s = self._state
        lines = [
            f"  Phase     : {s.phase.value}",
            f"  Run ID    : {s.run_id or '—'}",
            f"  Label     : {s.label or '—'}",
            f"  Started   : {s.started_at or '—'}",
            f"  Subsystems: {', '.join(s.subsystems_on) or '—'}",
            f"  Dark done : {s.dark_done}",
            f"  Src done  : {s.source_done}",
        ]
        return "\n".join(lines)
