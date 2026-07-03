"""LangGraph 编排图:planner → executor → observer → exception_manager → replanner → reporter。

与 PLAN"五节点"的差异(如实标注):exception_manager 独立成第 6 个节点——
恢复决策必须是确定性查表,不能混进(未来可能接 LLM 的)replanner;见 REVIEW.md M3。

拓扑:线性主干 + 执行环(executor⇄observer)+ 恢复环(observer→exception→replanner→executor),
Event Log 旁路记录一切,HITL 只挂在 exception_manager(ask_human_confirmation 工具)。

observer 的每个 tick 内部循环推进(而不是图级自环):水位检测(feedback 停滞/电量红线)
在飞可触发,这正是 goal-handle 契约(评审 B1)买来的能力。
"""
from __future__ import annotations

from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .planner_rules import build_queue, enumerate_substitutes
from .recovery import PRIORITY, FaultClass, next_stage
from .runtime import (
    MAX_GOAL_TICKS,
    MAX_WAIT_CHARGE_TICKS,
    RECURSION_LIMIT,
    Runtime,
    STAGNATION_THRESHOLD_TICKS,
)
from .world import DOCK


class AgentState(TypedDict, total=False):
    queue: list[dict]
    queue_idx: int
    resume_queue: Optional[list[dict]]
    active_goal: Optional[str]
    goal_target: Optional[str]
    goal_started_tick: int
    waiting_charge: bool
    fault: Optional[dict]           # {fclass, context}
    attempts: dict[str, int]        # 每类故障累计出现次数(升级用)
    pending_action: Optional[dict]  # exception_manager → replanner 的恢复动作
    sensor_degraded: bool
    degraded_steps: list[dict]
    substitutions: list[dict]
    anomalies_reported: list[dict]
    hitl_consults: int
    battery_handled: bool
    outcome_hint: Optional[str]
    visited: list[str]
    route: str                      # 条件边路由键


def _fault(fclass: FaultClass, **context) -> dict:
    return {"fclass": fclass.value, "context": context}


def build_graph(rt: Runtime):
    reg = rt.registry
    log = rt.event_log
    intent = rt.intent

    # ---- planner ----------------------------------------------------------

    async def planner(state: AgentState) -> AgentState:
        if state.get("resume_queue"):
            queue = state["resume_queue"]
            log.emit("planner", "queue_resumed", steps=queue)
            return {"queue": queue, "queue_idx": 0, "resume_queue": None,
                    "route": "exec"}
        queue = build_queue(intent)
        log.emit("planner", "plan_built", mission=intent.mission, steps=queue)
        return {
            "queue": queue, "queue_idx": 0, "resume_queue": None,
            "active_goal": None, "fault": None, "pending_action": None,
            "attempts": {}, "sensor_degraded": False, "degraded_steps": [],
            "substitutions": [], "anomalies_reported": [], "hitl_consults": 0,
            "battery_handled": False, "outcome_hint": None, "visited": [],
            "waiting_charge": False, "route": "exec",
        }

    # ---- executor ---------------------------------------------------------

    async def executor(state: AgentState) -> AgentState:
        i = state["queue_idx"]
        queue = state["queue"]
        if i >= len(queue):
            if state.get("resume_queue"):
                return {"route": "plan"}
            return {"route": "report"}
        step = queue[i]
        kind = step["kind"]

        if kind == "navigate":
            args: dict = {"node_id": step["target"],
                          "avoid_edges": rt.memory.avoid_edge_pairs()}
            if step.get("approval_token"):
                args["approval_token"] = step["approval_token"]
            res = await reg.call("navigate_to", args, caller="executor")
            if not res.ok:
                return {"fault": _fault(FaultClass.TOOL_FAILURE,
                                        tool="navigate_to",
                                        code=res.error["code"]),
                        "route": "except"}
            return {"active_goal": res.data["goal_id"],
                    "goal_target": step["target"],
                    "goal_started_tick": rt.clock.tick, "route": "observe"}

        if kind == "perceive":
            if state.get("sensor_degraded"):
                log.emit("executor", "step_skipped", step=step,
                         reason="sensor_degraded")
                return {"queue_idx": i + 1,
                        "degraded_steps": state.get("degraded_steps", []) + [step],
                        "route": "exec"}
            res = await reg.call("perceive", {"query": "anomaly"},
                                 caller="executor")
            if not res.ok:
                fclass = (FaultClass.SENSOR_FAULT
                          if res.error["code"] == "SENSOR_UNHEALTHY"
                          else FaultClass.TOOL_FAILURE)
                return {"fault": _fault(fclass, tool="perceive",
                                        code=res.error["code"], step_idx=i),
                        "route": "except"}
            log.emit("executor", "step_completed", step=step,
                     objects=res.data["objects"])
            new_queue = queue
            if res.data["objects"] and intent.report_anomalies:
                obj = res.data["objects"][0]
                report_step = {"kind": "report", "label": obj["label"],
                               "at": res.data["at_node"]}
                new_queue = queue[: i + 1] + [report_step] + queue[i + 1:]
            return {"queue": new_queue, "queue_idx": i + 1, "route": "exec"}

        if kind == "report":
            img = await reg.call("capture_image", {}, caller="executor")
            if not img.ok:
                return {"fault": _fault(FaultClass.TOOL_FAILURE,
                                        tool="capture_image",
                                        code=img.error["code"]),
                        "route": "except"}
            rep = await reg.call("report_finding", {
                "image_id": img.data["image_id"], "label": step["label"],
                "node_id": step["at"]}, caller="executor")
            if not rep.ok:
                return {"fault": _fault(FaultClass.TOOL_FAILURE,
                                        tool="report_finding",
                                        code=rep.error["code"]),
                        "route": "except"}
            reported = state.get("anomalies_reported", []) + [
                {"label": step["label"], "node": step["at"],
                 "report_id": rep.data["report_id"]}]
            log.emit("executor", "step_completed", step=step,
                     report_id=rep.data["report_id"])
            return {"queue_idx": i + 1, "anomalies_reported": reported,
                    "route": "exec"}

        if kind == "wait_charged":
            return {"waiting_charge": True, "route": "observe"}

        raise ValueError(f"unknown step kind: {kind}")

    # ---- observer ---------------------------------------------------------

    async def observer(state: AgentState) -> AgentState:
        if state.get("waiting_charge"):
            waited = 0
            while waited < MAX_WAIT_CHARGE_TICKS:
                await rt.adapter.wait(1)
                waited += 1
                st = await reg.call("get_robot_state", {}, caller="observer",
                                    poll=True)
                if st.ok and st.data["battery_pct"] >= intent.resume_battery_pct:
                    log.emit("observer", "step_completed",
                             step={"kind": "wait_charged"},
                             battery_pct=st.data["battery_pct"])
                    return {"waiting_charge": False,
                            "queue_idx": state["queue_idx"] + 1,
                            "battery_handled": False,  # 充满后水位重新武装
                            "route": "exec"}
            return {"waiting_charge": False, "outcome_hint": "charge_timeout",
                    "route": "report"}

        gid = state.get("active_goal")
        if gid is None:  # 防御:无目标却进了 observer
            return {"route": "exec"}
        target = state.get("goal_target")
        poll_failures = 0
        while True:
            await rt.adapter.wait(1)
            fb_res = await reg.call("get_nav_feedback", {"goal_id": gid},
                                    caller="observer", poll=True)
            st_res = await reg.call("get_robot_state", {}, caller="observer",
                                    poll=True)
            if not fb_res.ok or not st_res.ok:
                poll_failures += 1
                if poll_failures >= 3:
                    return {"fault": _fault(FaultClass.TOOL_FAILURE,
                                            tool="observer_poll",
                                            code="POLL_FAILED"),
                            "route": "except"}
                continue
            poll_failures = 0
            fb, st = fb_res.data, st_res.data

            # 安全水位:低电量在飞抢占(评审 B1/M4)
            if (st["battery_pct"] < intent.battery_floor_pct
                    and target != DOCK and not state.get("battery_handled")):
                log.emit("observer", "watchdog_triggered", kind="battery",
                         battery_pct=st["battery_pct"])
                return {"fault": _fault(FaultClass.LOW_BATTERY,
                                        battery_pct=st["battery_pct"]),
                        "route": "except"}

            if fb["status"] == "succeeded":
                log.emit("observer", "step_completed",
                         step={"kind": "navigate", "target": target})
                return {"active_goal": None,
                        "queue_idx": state["queue_idx"] + 1,
                        "visited": state.get("visited", []) + [target],
                        "route": "exec"}
            if fb["status"] == "aborted":
                if fb["reason"] == "battery_dead":
                    return {"active_goal": None,
                            "outcome_hint": "battery_dead", "route": "report"}
                return {"fault": _fault(FaultClass.NAV_UNREACHABLE,
                                        node=target, reason=fb["reason"]),
                        "route": "except"}
            if fb["status"] == "canceled":
                # 没有挂起故障却被取消:视作外部中止,安全停
                return {"active_goal": None, "outcome_hint": "goal_canceled",
                        "route": "report"}

            # 受阻水位:feedback 停滞(server 不自报 blocked,这里检测才算数)
            if fb["stall_ticks"] >= STAGNATION_THRESHOLD_TICKS:
                log.emit("observer", "watchdog_triggered", kind="stagnation",
                         edge=fb["current_edge"], stall_ticks=fb["stall_ticks"])
                return {"fault": _fault(FaultClass.NAV_BLOCKED,
                                        edge=fb["current_edge"], node=target),
                        "route": "except"}
            if rt.clock.tick - state.get("goal_started_tick", 0) > MAX_GOAL_TICKS:
                log.emit("observer", "watchdog_triggered", kind="goal_timeout")
                return {"fault": _fault(FaultClass.NAV_BLOCKED,
                                        edge=fb["current_edge"], node=target,
                                        flavor="timeout"),
                        "route": "except"}

    # ---- exception manager(确定性分类 + 查表,评审 M3) -------------------

    async def exception_manager(state: AgentState) -> AgentState:
        fault = state["fault"]
        fclass = FaultClass(fault["fclass"])
        attempts = dict(state.get("attempts", {}))
        n = attempts.get(fclass.value, 0)
        attempts[fclass.value] = n + 1
        stage = next_stage(fclass, n)
        log.emit("exception_manager", "fault_classified",
                 fclass=fclass.value, context=fault["context"],
                 attempt=n, priority=PRIORITY[fclass], stage=stage)

        updates: AgentState = {"attempts": attempts, "fault": None}

        # 需要停下底盘的阶段先取消在飞目标
        gid = state.get("active_goal")
        if gid is not None and stage != "skip_step_degraded":
            await reg.call("cancel_navigation", {"goal_id": gid},
                           caller="exception_manager")
            updates["active_goal"] = None

        if stage == "escalate_hitl":
            target = state["queue"][state["queue_idx"]].get("target", "?")
            res = await reg.call("ask_human_confirmation", {
                "message": f"导航到 {target} 的自动恢复已用尽,是否放弃该点、继续后续任务?",
                "scope": f"skip:{target}"}, caller="exception_manager")
            updates["hitl_consults"] = state.get("hitl_consults", 0) + 1
            approved = res.ok and res.data.get("approved")
            updates["pending_action"] = (
                {"type": "skip_step", "reason": "hitl_approved_skip"}
                if approved else {"type": "abort_to_dock", "reason": "hitl_denied"})
        elif stage == "pause_and_escalate":
            res = await reg.call("ask_human_confirmation", {
                "message": "传感器持续异常,是否降级继续(跳过所有感知步)?",
                "scope": "degrade:sensor"}, caller="exception_manager")
            updates["hitl_consults"] = state.get("hitl_consults", 0) + 1
            approved = res.ok and res.data.get("approved")
            updates["pending_action"] = (
                {"type": "degrade_sensor", "reason": "hitl_approved_degrade"}
                if approved else {"type": "abort_to_dock", "reason": "hitl_denied"})
        elif stage == "substitute_target":
            step = state["queue"][state["queue_idx"]]
            target = step.get("target") or fault["context"].get("node")
            if fclass is FaultClass.NAV_UNREACHABLE:
                rt.memory.unreachable_nodes.add(target)
            candidates = enumerate_substitutes(
                target, rt.world.topo, rt.memory,
                rt.world.robot_node, set(state.get("visited", [])))
            log.emit("exception_manager", "candidates_enumerated",
                     target=target, candidates=candidates)
            if candidates:
                idx = rt.selector.choose(candidates, context=f"substitute:{target}")
                log.emit("exception_manager", "candidate_chosen",
                         selector=rt.selector.name, index=idx,
                         chosen=candidates[idx])
                updates["pending_action"] = {"type": "substitute",
                                             "old": target,
                                             "new": candidates[idx]}
            elif fclass is FaultClass.NAV_UNREACHABLE:
                updates["pending_action"] = {"type": "skip_step",
                                             "reason": "no_substitute_degraded_report"}
            else:
                # 受阻链:无候选可替代 → 直接升级 HITL(占用一次 attempt)
                attempts[fclass.value] = n + 2
                res = await reg.call("ask_human_confirmation", {
                    "message": f"{target} 无可替代点,是否放弃该点、继续后续任务?",
                    "scope": f"skip:{target}"}, caller="exception_manager")
                updates["hitl_consults"] = state.get("hitl_consults", 0) + 1
                approved = res.ok and res.data.get("approved")
                updates["pending_action"] = (
                    {"type": "skip_step", "reason": "hitl_approved_skip"}
                    if approved else {"type": "abort_to_dock",
                                      "reason": "hitl_denied"})
        elif stage == "retry_same_route":
            updates["pending_action"] = {"type": "retry"}
        elif stage == "replan_avoid_edge":
            updates["pending_action"] = {"type": "avoid_edge",
                                         "edge": fault["context"].get("edge")}
        elif stage == "dock_recharge_resume":
            updates["pending_action"] = {"type": "dock_resume"}
            updates["battery_handled"] = True
        elif stage == "skip_step_degraded":
            updates["pending_action"] = {"type": "skip_step",
                                         "reason": "degraded"}
        elif stage == "failure_report_and_degrade":
            log.emit("exception_manager", "failure_report",
                     fclass=fclass.value, context=fault["context"])
            updates["pending_action"] = {"type": "degrade_sensor",
                                         "reason": "tool_circuit_degraded"}
        elif stage == "degraded_report":
            updates["pending_action"] = {"type": "skip_step",
                                         "reason": "degraded_report"}
        else:
            raise ValueError(f"unknown recovery stage: {stage}")

        updates["route"] = "replan"
        return updates

    # ---- replanner(把恢复动作落到任务队列上) ------------------------------

    async def replanner(state: AgentState) -> AgentState:
        action = state["pending_action"]
        assert action is not None
        i = state["queue_idx"]
        queue = state["queue"]
        log.emit("replanner", "recovery_applied", action=action)
        updates: AgentState = {"pending_action": None, "route": "exec"}

        atype = action["type"]
        if atype == "retry":
            pass  # 原步骤原样重发(executor 会重新 navigate)
        elif atype == "avoid_edge":
            edge = action.get("edge")
            if edge:
                rt.memory.blocked_edges.add(tuple(sorted(edge)))
        elif atype == "substitute":
            new_queue = [dict(s) for s in queue]
            for s in new_queue[i:]:
                if s.get("target") == action["old"]:
                    s["target"] = action["new"]
                if s.get("at") == action["old"]:
                    s["at"] = action["new"]
            updates["queue"] = new_queue
            updates["substitutions"] = state.get("substitutions", []) + [
                {"old": action["old"], "new": action["new"]}]
        elif atype == "skip_step":
            step = queue[i]
            log.emit("replanner", "step_skipped", step=step,
                     reason=action.get("reason"))
            updates["queue_idx"] = i + 1
            updates["degraded_steps"] = state.get("degraded_steps", []) + [step]
        elif atype == "degrade_sensor":
            updates["sensor_degraded"] = True
            step = queue[i]
            if step["kind"] == "perceive":
                updates["queue_idx"] = i + 1
                updates["degraded_steps"] = (state.get("degraded_steps", [])
                                             + [step])
        elif atype == "dock_resume":
            # 快照剩余任务(含当前步),队列换成 回坞+等充电(评审 M4)
            snapshot = [dict(s) for s in queue[i:]]
            updates["resume_queue"] = snapshot
            updates["queue"] = [{"kind": "navigate", "target": DOCK},
                                {"kind": "wait_charged"}]
            updates["queue_idx"] = 0
            log.emit("replanner", "queue_snapshot", snapshot=snapshot)
        elif atype == "abort_to_dock":
            updates["queue"] = [{"kind": "navigate", "target": DOCK}]
            updates["queue_idx"] = 0
            updates["resume_queue"] = None
            updates["outcome_hint"] = "hitl_abort"
            log.emit("replanner", "mission_aborted", reason=action.get("reason"))
        else:
            raise ValueError(f"unknown recovery action: {atype}")
        return updates

    # ---- reporter -----------------------------------------------------------

    async def reporter(state: AgentState) -> AgentState:
        st = await reg.call("get_robot_state", {}, caller="reporter")
        planned = [s for s in build_queue(intent)]
        log.emit("reporter", "run_summary",
                 outcome_hint=state.get("outcome_hint"),
                 planned_steps=len(planned),
                 degraded_steps=state.get("degraded_steps", []),
                 substitutions=state.get("substitutions", []),
                 anomalies_reported=state.get("anomalies_reported", []),
                 hitl_consults=state.get("hitl_consults", 0),
                 visited=state.get("visited", []),
                 final_state=st.data if st.ok else None)
        return {"route": "end"}

    # ---- 组图 ----------------------------------------------------------------

    ROUTES = {"exec": "executor", "observe": "observer",
              "except": "exception_manager", "replan": "replanner",
              "report": "reporter", "plan": "planner"}

    def router(state: AgentState) -> str:
        return state["route"]

    g = StateGraph(AgentState)
    g.add_node("planner", planner)
    g.add_node("executor", executor)
    g.add_node("observer", observer)
    g.add_node("exception_manager", exception_manager)
    g.add_node("replanner", replanner)
    g.add_node("reporter", reporter)
    g.add_edge(START, "planner")
    for node in ("planner", "executor", "observer", "exception_manager",
                 "replanner"):
        g.add_conditional_edges(node, router, ROUTES)
    g.add_edge("reporter", END)
    return g.compile()


async def run_graph(rt: Runtime) -> dict:
    app = build_graph(rt)
    final = await app.ainvoke({}, config={"recursion_limit": RECURSION_LIMIT})
    return final
