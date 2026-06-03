# run_ctrl — pnCCD Detector Run Control

Python-based run control for the pnCCD detector system.
Uses **MLflow** for metadata and config tracking, **Fabric** for SSH orchestration to sulley.

---

## Installation

```bash
cd run_ctrl/
pip install -e .

# First-time SSH setup (once only):
bash setup_ssh.sh tng sulley
```

---

## Run flow

The diagram below shows the full command flow. Click any box for details.

> **Minimum steps to complete a run** (hardware already on):
> ```bash
> run_ctrl init
> run_ctrl start-daq
> run_ctrl stop
> run_ctrl reset
> ```
> `spinup` and `powerdown` are only needed when run_ctrl also manages hardware power.

### Key rules

1. **`init` is always first** — creates the MLflow run and `run_id` everything else tracks against.
2. **`spinup` is optional if hardware is already on** — common between back-to-back runs.
3. **`stop` vs `teardown`** — the main fork after data taking:
   - More runs soon → `stop` (closes MLflow, hardware stays on) → `reset` → `init` → `start-daq`
   - Done for the day → `teardown` (closes MLflow + powers everything off)
4. **`powerdown` is state-agnostic** — call it any time, with or without an active run.
5. **`postproc` and `snapshot` are always optional** — call after `stop`/`teardown`, or skip.
6. **`abort` works from any phase** — always runs HV → sensor → ASIC shutdown regardless of state.

### Typical sequences

```bash
# ── Back-to-back runs (hardware stays on) ────────────────────
run_ctrl init
run_ctrl start-daq          # blocks until all frames done
run_ctrl stop               # close MLflow, hardware untouched
run_ctrl reset
# adjust parameters / take a snapshot, then:
run_ctrl init
run_ctrl start-daq
...

# ── End of session ────────────────────────────────────────────
run_ctrl postproc -m "nominal run, beam stable"
run_ctrl powerdown          # HV down → sensor off → ASIC off

# ── First time / commissioning (step by step) ─────────────────
run_ctrl init
run_ctrl daq start          # start_s7 + start_daq
run_ctrl asic start         # start_veritas + sync_s7
run_ctrl hv up
run_ctrl sensor init
run_ctrl sensor start
run_ctrl recorder dark
run_ctrl recorder open
run_ctrl recorder source    # or: --file N to retake one file
run_ctrl recorder close
run_ctrl stop
run_ctrl reset
run_ctrl powerdown
```

---

## Command reference

### Phase commands

| Command | What it does |
|---------|--------------|
| `run_ctrl init` | Create MLflow run, archive configs, log parameters |
| `run_ctrl spinup` | DAQ → ASIC → HV up → sensor on (all at once) |
| `run_ctrl start-daq` | Dark frames → source file loop (blocking) |
| `run_ctrl stop` | Close MLflow run — hardware left running |
| `run_ctrl powerdown` | HV down → sensor off → ASIC off — no MLflow interaction |
| `run_ctrl teardown` | `stop` + `powerdown` combined |
| `run_ctrl postproc` | Post elog entry + trigger background analysis |
| `run_ctrl snapshot` | Archive current configs to MLflow — no hardware, no state change |
| `run_ctrl status` | Show current phase, run ID, subsystem state |
| `run_ctrl abort` | Emergency safe shutdown from any phase |
| `run_ctrl reset` | Clear state file after stop/teardown/abort |
| `run_ctrl run CMD` | Run a named command from `custom_commands` in config |

```bash
run_ctrl postproc --no-elog
run_ctrl postproc --no-analysis
run_ctrl postproc -m "operator comment"

run_ctrl snapshot -l "after_hv_tune"
```

### Subsystem commands

Every subsystem in `run_config.yaml` automatically gets `init / start / stop / run`,
driven by its `init_cmd`, `start_cmd`, `stop_cmd`, and `executable` entries.
These are **state-agnostic** — usable with or without an active MLflow run.

#### `run_ctrl daq`

| Command | Executes |
|---------|----------|
| `run_ctrl daq start` | `fsp-ctrl start_s7` then `fsp-ctrl start_daq` |
| `run_ctrl daq start-s7` | `fsp-ctrl start_s7` only |
| `run_ctrl daq start-daq` | `fsp-ctrl start_daq` only |
| `run_ctrl daq sync-s7` | `fsp-ctrl sync_s7` |
| `run_ctrl daq status` | `fsp-ctrl status` |
| `run_ctrl daq run <subcmd>` | any `fsp-ctrl` subcommand (passthrough) |

#### `run_ctrl asic`

| Command | Executes |
|---------|----------|
| `run_ctrl asic start` | `fsp-ctrl start_veritas` then `fsp-ctrl sync_s7` |
| `run_ctrl asic stop` | `fsp-ctrl pwr_off_veritas` |
| `run_ctrl asic run <subcmd>` | any `fsp-ctrl` subcommand |

#### `run_ctrl hv`

| Command | Executes |
|---------|----------|
| `run_ctrl hv up` | `hv_ramp.py -m UP` (blocking until target) |
| `run_ctrl hv down` | `hv_ramp.py -m DOWN` (blocking until zero) |

#### `run_ctrl sensor`

| Command | Executes |
|---------|----------|
| `run_ctrl sensor init` | `ccd-pwr init` |
| `run_ctrl sensor start` | `ccd-pwr pwr_up` |
| `run_ctrl sensor stop` | `ccd-pwr pwr_down` — ensure HV=0 first |

#### `run_ctrl recorder`

| Command | What it does |
|---------|--------------|
| `run_ctrl recorder dark` | Dark frame acquisition |
| `run_ctrl recorder source` | Full source file loop (`source_file_nr` iterations) |
| `run_ctrl recorder source --file N` | Retake a single file by 1-based index |
| `run_ctrl recorder open` | Open source shutter |
| `run_ctrl recorder close` | Close source shutter |

### Adding a new subsystem

Add an entry to `run_config.yaml` under `subsystems`, then register it in `cli.py`:

```yaml
# run_config.yaml
subsystems:
  my_new_tool:
    host: "localhost"
    executable: "my_tool_cli"
    init_cmd: "init"
    start_cmd: ["configure", "start"]
    stop_cmd: "shutdown"
```

```python
# cli.py — two lines at the bottom
cli.add_command(make_subsystem_group("my_new_tool", "My tool description."))
```

Then `run_ctrl my_new_tool init/start/stop/run` all work immediately.

---

## Configuration

All settings live in `config/run_config.yaml`.

### Server topology

```yaml
general:
  control_server:
    host: "localhost"                    # hydra — where run_ctrl runs
    venv: "~/software/fsp_system_test/vfsp"  # venv for all local commands

  daq_server:
    host: "sulley"                       # remote data acquisition PC
    user: "tng"
    ssh_key: "~/.ssh/id_ed25519_sulley"
    venv: "~/venv_sulley"               # venv activated on sulley via SSH
```

Every command is prefixed with `. {venv}/bin/activate &&` automatically.
Venv resolution — most specific wins:

```
subsystems.<name>.venv      ← per-subsystem override
    ↓ falls back to
daq_server.venv             ← default for all remote commands
control_server.venv         ← default for all local commands
```

### Config archiving

Three strategies, mix freely under `run.config_archive`. Adding entries needs no code changes.

```yaml
run:
  config_archive:

    # 1. Resolve filename from a tool's own YAML, then archive that file
    s7_config:
      tool_config_file: "~/fsp_ctrlapp/configs/sys_configs.yaml"
      keys: ["s7"]
      files_base_path: "~/drivers/spi_testing/"

    # 2. Archive a local file directly
    sensor_voltage:
      direct_file: "~/ccd_pwrapp/configs/pwr_up_seq.json"

    # 3. Fetch files from a remote host via SSH
    recorder_config:
      host: "sulley"
      files:
        - "/home/tng/fsp_ana/nrstIllumi50ms.prm"
```

### Recorder output placeholders

| Placeholder | Example | Description |
|-------------|---------|-------------|
| `{file_index}` | `3` | 1-based file number |
| `{file_index_pad}` | `003` | zero-padded to 3 digits |
| `{run_id}` | `abc123ef...` | full MLflow run ID |
| `{run_id_short}` | `abc123ef` | first 8 chars |
| `{data_path}` | `/data/fsp_data/run_abc123` | data directory |
| `{source_frame_nr}` | `100000` | frames per file |
| `{dark_frame_nr}` | `2000` | dark frame count |

### Elog (PSI ELOG)

PSI ELOG uses its own CLI tool, not a JSON REST API. Set `method: "cli"` (preferred)
or `method: "http"` (fallback). The entry body includes the MLflow run URL so teammates
can click through to the full parameter set and config archive.

```yaml
post_processing:
  elog:
    method: "cli"          # "cli" (needs elog binary) or "http"
    host: "your-elog-server"
    port: 8080
    logbook: "FSP"
    user: "tng"            # optional, for password-protected logbooks
    # password via env var: ELOG_PASSWORD
    attributes:            # must match your logbook's configured attributes
      Author: "run_ctrl"
      Type: "Run Summary"
      System: "pnCCD"
```

---

## State machine

```
idle ──► initialized ──► spun_up ──► acquiring ──► torn_down ──► reset ──► idle
                                         │
                                    stop (MLflow closed,
                                    hardware stays on)
                                         │
                                       reset

aborted  (from any phase via 'abort' — always runs safe powerdown first)
```

Phase commands enforce ordering. Subsystem commands (`daq`, `asic`, `hv`, `sensor`, `recorder`)
are state-agnostic and always available.

---

## Safety

- `teardown`, `powerdown`, and `abort` always ramp HV to zero before powering off the sensor.
- When running steps manually, `hv down` before `sensor stop` is your responsibility.
- `abort` is safe to call from any phase — use it any time something goes wrong.
- The software interlock is checked before `spinup` (disable with `safety.require_interlock: false`).
- DAQ is intentionally left running between runs (`stop_cmd: []` in config).

---

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ELOG_PASSWORD` | Password for elog CLI / HTTP auth |

---

## Project structure

```
run_ctrl/
├── config/
│   └── run_config.yaml
├── src/run_ctrl/
│   ├── cli.py                    # CLI entry point + command registration
│   ├── core/
│   │   ├── state_manager.py      # run state + phase transitions
│   │   ├── mlflow_manager.py     # MLflow lifecycle wrapper
│   │   ├── runner.py             # LocalRunner + SSHRunner (venv-aware)
│   │   └── config_archiver.py    # config resolver & MLflow uploader
│   ├── phases/
│   │   ├── phase1_init.py        # setup & MLflow init
│   │   ├── phase2_spinup.py      # hardware spin-up
│   │   ├── phase3_daq.py         # data acquisition loop
│   │   ├── phase4_teardown.py    # stop / powerdown / teardown / snapshot / abort
│   │   └── phase5_postproc.py    # elog (PSI ELOG CLI + HTTP) + analysis trigger
│   └── commands/
│       ├── cmd_subsystem.py      # generic subsystem group factory
│       ├── cmd_daq.py            # run_ctrl daq + asic
│       ├── cmd_hardware.py       # run_ctrl hv + sensor
│       └── cmd_recorder.py       # run_ctrl recorder
├── setup_ssh.sh
└── pyproject.toml
```
