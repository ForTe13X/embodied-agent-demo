# Embodied Agent Task Planner (simulation demo)

[中文](README.md)

![Demo excerpt: the control dashboard replays the robot detouring around a blocked path, a first-person view approaching the crate stack, the vision AI locking onto an anomaly, and — with safety guards switched off — the system flashing red as it flags rule violations](docs/recording/demo.gif)

*16-second excerpt; full 4-minute narrated video: [docs/recording/demo.mp4](docs/recording/demo.mp4).*

A simulation demo for an embodied-agent orchestration layer. The language model is limited to intent parsing; navigation, recovery, safety checks, and evaluation are handled by deterministic code.

This repository focuses on the control layer around a robot-like agent: task planning, tool gating, fault recovery, replay, and repeatable evaluation. The main demo and the 90-run pre-registered evaluation run on a mock navigation server (simulation, no physical robot). See the `RobotAdapter` boundary in [docs/ADAPTER_CONTRACT.md](docs/ADAPTER_CONTRACT.md).

> **First time here?** Start with the [terminology & codename cheat-sheet (docs/GLOSSARY.md)](docs/GLOSSARY.md) — one page covering mock⇄real, nav2_loopback_sim, the topology codenames, and the system layers that the other docs use liberally.
>
> **Want the positioning (and how VLA fits)?** Read [docs/POSITIONING.md](docs/POSITIONING.md) — this is a task-level orchestration + safety runtime; VLA is a future learned *skill* that hangs *under* it. See also the [Recovery Ownership Matrix (docs/RECOVERY_OWNERSHIP.md)](docs/RECOVERY_OWNERSHIP.md) — which layer owns which recovery, grounded in a Phase C empirical finding.

**Phase B (done):** swapping in one adapter connects this **same LangGraph orchestration graph** to **real ROS 2 Nav2** (Jazzy + nav2_loopback_sim, in a container) with zero orchestration-code changes. Faults are injected via a keepout costmap filter; recovery comes from the deterministic kernel's lookup table — mock ⇄ real Nav2 is interchangeable, verified 1:1 against the real stack. Measured runs and reproduction: [phase_b/FINDINGS.md](phase_b/FINDINGS.md).

![Day-4 real-robot integration excerpt: the same orchestration now drives a real navigation stack (ROS 2 Nav2); when inspection point a3 is fenced off and ruled unreachable, the system automatically reroutes to backup point a3_alt by a preset rule](docs/recording/day4_demo.gif)

*13-second excerpt (the fault-recovery moment from one real run); full 3-minute narrated bilingual version: [docs/recording/day4_demo.mp4](docs/recording/day4_demo.mp4).*

**Phase C (done):** we re-ran the pre-registered fault-injection eval on the real robot navigation stack (Nav2), not just in simulation. On the conditions that carry over, the real robot and the simulation reach the exact same final outcome. See [phase_c/PHASE_C_RESULTS.md](phase_c/PHASE_C_RESULTS.md).

## Included Artifacts

| Artifact | Location |
|---|---|
| **Terminology & codename cheat-sheet (read this first)** | [docs/GLOSSARY.md](docs/GLOSSARY.md) |
| **Positioning + how VLA fits + Recovery Ownership Matrix** | [docs/POSITIONING.md](docs/POSITIONING.md), [docs/RECOVERY_OWNERSHIP.md](docs/RECOVERY_OWNERSHIP.md) |
| Demo recording, about 4 minutes, with bilingual subtitles | [docs/recording/demo.mp4](docs/recording/demo.mp4) + [demo.srt](docs/recording/demo.srt) |
| Godot 4 tooling that renders the first-person-view (POV) footage, driven by the robot's actual path | [povgen/](povgen/) + [scripts/export_traj.py](scripts/export_traj.py) |
| Local vision AI (VLM) annotating anomalies in inspection frames — experiment plus limitations | [scripts/vlm_annotate.py](scripts/vlm_annotate.py) -> [annotated frame](docs/screenshots/vlm_live_annotated.png) |
| Synchronized replay viewer with POV panels | [viewer/pov/](viewer/pov/) |
| Tool API, errors, sequences, and expected outputs | [docs/API.md](docs/API.md) |
| User manual with screenshots | [docs/USER_MANUAL.md](docs/USER_MANUAL.md) |
| Test matrix and manual checks | [docs/TESTCASES.md](docs/TESTCASES.md) |
| Product scope and roadmap | [docs/PRODUCT.md](docs/PRODUCT.md) |
| Evaluation protocol, predictions, and reports | [EVAL_PREREG.md](EVAL_PREREG.md), [prereg.yaml](prereg.yaml), [RESULTS.md](RESULTS.md), [REVIEW.md](REVIEW.md) |
| **Phase B**: real Nav2 integration + fault injection + MCAP audit (measured) | [phase_b/FINDINGS.md](phase_b/FINDINGS.md) |
| Phase B: RclpyAdapter (real RobotAdapter implementation) + real_runtime shim | [phase_b/rclpy_adapter.py](phase_b/rclpy_adapter.py), [phase_b/real_runtime.py](phase_b/real_runtime.py) |
| Phase B: same LangGraph graph driving real Nav2 through a fault-recovery mission | [phase_b/run_real_mission.py](phase_b/run_real_mission.py) + [real_mission_events.jsonl](phase_b/real_mission_events.jsonl) |
| Phase B: Day-4 real-integration POV demo (3-min narrated bilingual) | [docs/recording/day4_demo.mp4](docs/recording/day4_demo.mp4) |
| **Phase C**: reduced real-Nav2 eval (mock⇄real comparison: 4 conditions × repeats, same terminal state) | [phase_c/PHASE_C_RESULTS.md](phase_c/PHASE_C_RESULTS.md) + [phase_c/run_real_eval.py](phase_c/run_real_eval.py) |

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

- The 90-run pre-registered evaluation is mock-only; battery, sensor, and tool-failure injection are simulator-only features. The navigation adapter and nav-class faults were migrated to real ROS 2 Nav2 in Phase B/C (see [phase_b/FINDINGS.md](phase_b/FINDINGS.md), [phase_c/PHASE_C_RESULTS.md](phase_c/PHASE_C_RESULTS.md)).
- Combined blocked-route and low-battery cases can still drain the battery if the return path is blocked long enough.
- Route memory treats blocked edges as blocked for the rest of the run.
- A single navigation call carries one approval token, so combined approval cases stay conservative.
- Fault priority is implemented by observer check order.
- Circuit breakers do not have half-open recovery inside a run.
- Cross-run long-term memory is not part of the registered evaluation.
