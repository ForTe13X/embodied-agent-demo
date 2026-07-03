# Embodied Agent Task Planner (simulation demo)

> A simulation-only orchestration layer for embodied agents. The language model is limited to intent parsing; navigation, recovery, safety checks, and evaluation are handled by deterministic code.

This repository focuses on the control layer around a robot-like agent: task planning, tool gating, fault recovery, replay, and repeatable evaluation. It does not claim real-robot validation. The current backend is a mock navigation server with a `RobotAdapter` contract prepared for a future Nav2/rclpy integration. See [docs/ADAPTER_CONTRACT.md](docs/ADAPTER_CONTRACT.md) for the adapter boundary.

## What Is Included

| Artifact | Location |
|---|---|
| Demo recording, about 4 minutes, with bilingual subtitles | [docs/recording/demo.mp4](docs/recording/demo.mp4) + [demo.srt](docs/recording/demo.srt) |
| POV rendering pipeline, Godot 4 scene driven by ground-truth trajectories | [povgen/](povgen/) + [scripts/export_traj.py](scripts/export_traj.py) |
| Local VLM frame annotation experiment and limitation notes | [scripts/vlm_annotate.py](scripts/vlm_annotate.py) -> [annotated frame](docs/screenshots/vlm_live_annotated.png) |
| Synchronized replay viewer with POV panels | [viewer/pov/](viewer/pov/) |
| Tool API, errors, sequences, and expected outputs | [docs/API.md](docs/API.md) |
| User manual with screenshots | [docs/USER_MANUAL.md](docs/USER_MANUAL.md) |
| Test matrix and manual checks | [docs/TESTCASES.md](docs/TESTCASES.md) |
| Product scope and roadmap | [docs/PRODUCT.md](docs/PRODUCT.md) |
| Evaluation protocol, predictions, and reports | [EVAL_PREREG.md](EVAL_PREREG.md), [prereg.yaml](prereg.yaml), [RESULTS.md](RESULTS.md), [REVIEW.md](REVIEW.md) |

Media-generation helpers use the system Python environment:

```powershell
python scripts\capture_viewer.py
python scripts\capture_terminal.py
python scripts\recording\build_video.py --all
```

They require Playwright, edge-tts, Pillow, and `ffmpeg` on `PATH`. The project virtual environment is kept small for the runtime and tests.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONUTF8 = 1

.\.venv\Scripts\python -m pytest tests -q
.\.venv\Scripts\python run_demo.py --scenario blocked
.\.venv\Scripts\python run_demo.py --scenario restricted --interactive
.\.venv\Scripts\python run_demo.py --nl "去A区巡检a1和a3" --llm
.\.venv\Scripts\python run_eval.py
.\.venv\Scripts\python viewer\serve.py
.\.venv\Scripts\python -m embodied_agent.replay runs\nav_blocked\seed_0.jsonl
```

The viewer is served at `http://127.0.0.1:8777`.

## Architecture

```text
Natural language -> Intent parser -> LangGraph orchestration
                                      planner -> executor <-> replanner
                                                    |
                                                    v
                                                observer
                                                    |
                                  fault signal -> exception manager
                                                    |
                                                    v
                                                reporter

Every tool call passes through Tool Registry:
typed schema, allowlist, idempotent retry policy, circuit breaker,
HITL approval tokens, battery guard, and access-level checks.

Tool Registry -> RobotAdapter -> MockNavServer -> World
World = topology, battery, sensors, injected faults, virtual clock

Append-only event logs connect all layers and support replay.
Short-term memory lives in graph state; per-run route memory records blocked
edges and unreachable points, then resets on the next run.
```

Key implementation choices are documented in [REVIEW.md](REVIEW.md). The current graph uses a separate `exception_manager` node so recovery policy remains isolated from replanning. The tool layer exposes a small typed surface, including navigation feedback and finding reports, instead of giving the planner direct access to robot internals.

## Fault Detection And Recovery

The mock navigation server does not return a synthetic `blocked` status. When movement stalls, feedback simply stops making progress. The observer detects this from velocity/progress watermarks, then cancels or replans through the same asynchronous goal-handle contract a real adapter would use. This keeps fault detection in the orchestration layer rather than baking shortcuts into the simulator.

| Fault | Injection | Detection | Recovery chain |
|---|---|---|---|
| Blocked route | Edge blocked around tick 4-16 | Feedback stagnation for at least 6 ticks | Retry once -> detour replan -> alternative point -> HITL |
| Unreachable point | Isolated target | `result=unreachable` | Alternative point from a closed candidate set -> degraded report |
| Sensor fault | `sensor_health=false` | Perception error | Skip/degrade -> pause + HITL |
| Low battery | Low initial charge with faster drain | Battery watermark on every tick | Snapshot queue -> dock and charge -> continue queued work |
| Tool failure | Timeout or malformed response before perception | Validation failure or timeout | Retry idempotent calls -> circuit break -> degraded failure report |
| Combined blocked route + low battery | Simultaneous faults | Same signals as above | Safety recovery preempts task recovery |

Recovery is deterministic: classify the fault, look up the chain, and choose only from enumerated candidates. In optional LLM mode, the model may choose an index from that closed set; it does not invent recovery actions.

## Tool Registry Rules

| Rule | Behavior |
|---|---|
| Allowlist | Unknown tool names are rejected as `UNKNOWN_TOOL` and logged |
| Typed schema | Pydantic validation with `extra='forbid'`; unknown arguments become `SCHEMA_VIOLATION` |
| Retry policy | Idempotent read/perception calls may retry once; navigation, reports, and HITL actions do not auto-retry |
| Circuit breaker | Three consecutive failures open the breaker for that tool |
| High-risk actions | Restricted zones and low-battery continuation require scoped, single-use, time-limited HITL tokens |
| Forbidden zones | Approval tokens cannot override a forbidden target |
| Motion authority | The adapter exposes no speed or torque commands; the planner cannot bypass the navigation contract |
| Constraint source | Battery thresholds and access levels come from static config; parsed intent can only narrow permissions |

## Evaluation

The evaluation protocol is pre-registered in [EVAL_PREREG.md](EVAL_PREREG.md). Machine-readable predictions live in [prereg.yaml](prereg.yaml) and are checked before `run_eval.py` runs. Metrics are generated from append-only event logs, not from agent memory.

Current matrix:

- 9 conditions x 10 seeds = 90 runs.
- Conditions include baseline, five single faults, one combined fault, an adversarial planner stub with the gate enabled, and an ablation with the gate disabled.
- Safety violations are recorded by a ground-truth monitor under the tool registry, so they are not self-reported by the agent.
- Results separate detection, recovery, terminal state, and escalation to HITL.
- Seeds control only the mock world; the virtual clock keeps runs reproducible and fast.

## Project Layout

```text
embodied_agent/
  clock.py world.py events.py safety.py faults.py
  mock_server.py adapter.py
  registry.py hitl.py
  intent.py llm_intent.py planner_rules.py
  recovery.py memory.py graph.py runtime.py
  replay.py
  evaluation/
faults.yaml
prereg.yaml
EVAL_PREREG.md
tests/
run_demo.py
run_eval.py
docs/
```

## Review And Regression Notes

The implementation was reviewed against orchestration behavior, tool safety, evaluation validity, and user-facing reproducibility. Confirmed critical and major issues were fixed with regression tests before the current evaluation run. Rerun reasons and metric changes are recorded in [EVAL_PREREG.md](EVAL_PREREG.md).

## Known Limits

- The project is mock-only. Battery, sensor, and tool-failure injection are simulator features. Navigation-related adapter behavior is the part intended to transfer first.
- A combined blocked-route and low-battery case can still drain the battery if the return path is blocked long enough. The current policy exposes this risk instead of hiding it; a future improvement would skip retry inside emergency battery context.
- Route memory treats blocked edges as blocked for the rest of the run, so temporary obstacles may force conservative degradation.
- A single navigation call carries one approval token. A target that requires both restricted-zone and low-battery approval remains blocked.
- Fault priority is implemented by observer check order; the priority table is an audit record, not a runtime policy table.
- Circuit breakers do not have half-open recovery inside a run.
- Cross-run long-term memory is not part of the registered evaluation.
