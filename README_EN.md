# Embodied Agent Task Planner (simulation demo)

[中文](README.md)

![Demo excerpt: mission-control replay around a blocked edge, first-person view reaching the crate stack, the VLM locking onto the anomaly, and the gates-off ablation flashing ground-truth violations](docs/recording/demo.gif)

*16-second excerpt; full 4-minute narrated video: [docs/recording/demo.mp4](docs/recording/demo.mp4).*

A simulation demo for an embodied-agent orchestration layer. The language model is limited to intent parsing; navigation, recovery, safety checks, and evaluation are handled by deterministic code.

This repository focuses on the control layer around a robot-like agent: task planning, tool gating, fault recovery, replay, and repeatable evaluation. It does not claim real-robot validation. The current backend is a mock navigation server, with a `RobotAdapter` boundary prepared for future Nav2/rclpy integration. See [docs/ADAPTER_CONTRACT.md](docs/ADAPTER_CONTRACT.md).

## Included Artifacts

| Artifact | Location |
|---|---|
| Demo recording, about 4 minutes, with bilingual subtitles | [docs/recording/demo.mp4](docs/recording/demo.mp4) + [demo.srt](docs/recording/demo.srt) |
| Godot 4 POV rendering pipeline driven by ground-truth trajectories | [povgen/](povgen/) + [scripts/export_traj.py](scripts/export_traj.py) |
| Local VLM frame annotation experiment and limitation notes | [scripts/vlm_annotate.py](scripts/vlm_annotate.py) -> [annotated frame](docs/screenshots/vlm_live_annotated.png) |
| Synchronized replay viewer with POV panels | [viewer/pov/](viewer/pov/) |
| Tool API, errors, sequences, and expected outputs | [docs/API.md](docs/API.md) |
| User manual with screenshots | [docs/USER_MANUAL.md](docs/USER_MANUAL.md) |
| Test matrix and manual checks | [docs/TESTCASES.md](docs/TESTCASES.md) |
| Product scope and roadmap | [docs/PRODUCT.md](docs/PRODUCT.md) |
| Evaluation protocol, predictions, and reports | [EVAL_PREREG.md](EVAL_PREREG.md), [prereg.yaml](prereg.yaml), [RESULTS.md](RESULTS.md), [REVIEW.md](REVIEW.md) |

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

The viewer runs at `http://127.0.0.1:8777`.

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

## Fault Detection And Recovery

The mock navigation server does not return a synthetic `blocked` status. When movement stalls, feedback simply stops making progress. The observer detects this from velocity/progress watermarks, then cancels or replans through the same asynchronous goal-handle contract a real adapter would use.

| Fault | Injection | Detection | Recovery chain |
|---|---|---|---|
| Blocked route | Edge blocked around tick 4-16 | Feedback stagnation for at least 6 ticks | Retry once -> detour replan -> alternative point -> HITL |
| Unreachable point | Isolated target | `result=unreachable` | Alternative point from a closed candidate set -> degraded report |
| Sensor fault | `sensor_health=false` | Perception error | Skip/degrade -> pause + HITL |
| Low battery | Low initial charge with faster drain | Battery watermark on every tick | Snapshot queue -> dock and charge -> continue queued work |
| Tool failure | First k perceive calls return a timeout or malformed response (k = 2 or 4 per seed) | Validation failure or timeout | Skip step (degraded) -> failure report + degrade; the registry retries idempotent calls once and circuit-breaks on persistent failure |
| Combined blocked route + low battery | Simultaneous faults | Same signals as above | Safety recovery preempts task recovery |

Recovery is deterministic: classify the fault, look up the chain, and choose only from enumerated candidates. The selector interface only permits picking an index from that closed set — even a future LLM-backed selector could not invent recovery actions. The current implementation always uses the deterministic rule selector.

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

## Scope

- The project is mock-only. Battery, sensor, and tool-failure injection are simulator features.
- Combined blocked-route and low-battery cases can still drain the battery if the return path is blocked long enough.
- Route memory treats blocked edges as blocked for the rest of the run.
- A single navigation call carries one approval token, so combined approval cases stay conservative.
- Fault priority is implemented by observer check order.
- Circuit breakers do not have half-open recovery inside a run.
- Cross-run long-term memory is not part of the registered evaluation.
