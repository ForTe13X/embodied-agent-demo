"""LangGraph 编排图:planner → executor → observer → exception_manager → replanner → reporter。

与 PLAN"五节点"的差异(如实标注):exception_manager 独立成第 6 个节点——
恢复决策必须是确定性查表,不能混进(未来可能接 LLM 的)replanner;见 REVIEW.md M3。

拓扑:线性主干 + 执行环(executor⇄observer)+ 恢复环(observer→exception→replanner→executor),
Event Log 旁路记录一切,HITL 只挂在 exception_manager(ask_human_confirmation 工具)。

observer 的每个 tick 内部循环推进(而不是图级自环):水位检测(feedback 停滞/电量红线)
在飞可触发,这正是 goal-handle 契约(评审 B1)买来的能力。
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .planner_rules import build_queue, enumerate_substitutes
from .recovery import PRIORITY, RECOVERY_CHAINS, FaultClass, next_stage
from .runtime import (
    SKILL_POLL_PERIOD_S,
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
    mission_queue: Optional[list[dict]]  # D2:显式复合任务队列(不给则由 intent 生成)
    skill_watchdog_s: Optional[float]    # D2:skill 兜底看门狗(墙钟秒)
    active_skill_goal: Optional[str]   # D2:在飞的 VLA skill goal 句柄(与 nav 同构)
    skill_postcheck: Optional[dict]    # D2:独立后置校验证据(不采信 skill 自报)
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
        # D2:允许显式给一条复合任务队列(nav + vla_skill 混编),否则按 intent 生成巡检队列。
        queue = state.get("mission_queue") or build_queue(intent)
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
                # 按错误码分类(复审 critical:电量闸拒绝曾被误分类为 TOOL_FAILURE,
                # 恢复链在 navigate 步骤上不推进 → 零 tick 死循环)
                code = res.error["code"]
                if code == "BATTERY_FLOOR":
                    return {"fault": _fault(FaultClass.LOW_BATTERY,
                                            source="gate", code=code),
                            "route": "except"}
                return {"fault": _fault(FaultClass.TOOL_FAILURE,
                                        tool="navigate_to", code=code),
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

        if kind == "vla_skill":
            # D2:learned skill 与 nav 在【同一张图】里走同一模式——派发拿句柄,交给 observer 轮询。
            # 编排层不逐帧调 policy,只见 executing/succeeded/failed(review §七)。
            res = await reg.call("execute_vla_skill", {
                "instruction": step["instruction"],
                "skill_id": step.get("skill_id", "tabletop_pick"),
                "timeout_s": step.get("timeout_s", 8.0)}, caller="executor")
            if not res.ok:
                return {"fault": _fault(FaultClass.SKILL_FAILED,
                                        tool="execute_vla_skill",
                                        code=res.error["code"], step_idx=i),
                        "route": "except"}
            # 看门狗用墙钟(skill 是实时的),故不写 tick;每步可自带 watchdog_s 覆盖默认值。
            return {"active_skill_goal": res.data["skill_goal_id"],
                    "skill_watchdog_s": step.get("watchdog_s"), "route": "observe"}

        if kind == "wait_charged":
            return {"waiting_charge": True, "route": "observe"}

        raise ValueError(f"unknown step kind: {kind}")

    # ---- observer ---------------------------------------------------------

    async def observer(state: AgentState) -> AgentState:
        # D2:在飞 VLA skill 与 nav 同构地轮询(上层只见 executing/succeeded/failed)。
        # 终态失败 → 按 code 归类上浮:安全停走 SKILL_UNSAFE(绝不重试),其余 SKILL_FAILED。
        sid = state.get("active_skill_goal")
        if sid:
            poll_failures = 0
            # skill runtime 跑在【真实时间】上,而 mock 世界是【虚拟 tick】。若用虚拟 tick 做看门狗,
            # 观察者会在 skill 还没跑几步时就把虚拟预算烧光而误判超时 —— 故这里的兜底用墙钟,
            # 且每轮让出一点真实时间给 skill 的控制环推进。skill 自身也有 timeout,这层只是兜底。
            t_start = time.monotonic()
            # 兜底预算必须【宽于 skill 自身 timeout】,否则会在 skill 还没用完自己的预算时
            # 把它掐死并误分类成 SKILL_WATCHDOG_TIMEOUT(复审实测:timeout_s>30 必被误杀)。
            step_now = state["queue"][state["queue_idx"]]
            budget_s = float(state.get("skill_watchdog_s")
                             or (float(step_now.get("timeout_s", 8.0)) * 2 + 10.0))
            while True:
                # 只让出真实时间给 skill 的控制环,**不**按轮询频率推进虚拟世界:
                # 否则 5s 的 skill × 5ms 轮询 = 上千个虚拟 tick 的耗电,机器人会在操作中"电量耗尽"
                # —— 那是把轮询频率当成了世界时间。mock 世界建模的是导航,不含操作能耗模型
                # (与 battery 为 mock-only 的既有口径一致),故 skill 期间不推进世界时钟。
                await asyncio.sleep(SKILL_POLL_PERIOD_S)
                fb_res = await reg.call("get_skill_feedback", {"skill_goal_id": sid},
                                        caller="observer", poll=True)
                if not fb_res.ok:
                    poll_failures += 1
                    if poll_failures >= 3:
                        # 放弃轮询前必须先取消:否则 skill 变成孤儿——后台继续动、继续改 sim、
                        # 继续写日志,且在飞锁不释放(复审实测)。看门狗路径本来就 cancel,
                        # 这条路径漏了,属明显疏漏。
                        await reg.call("cancel_skill", {"skill_goal_id": sid},
                                       caller="observer")
                        return {"active_skill_goal": None,
                                "fault": _fault(FaultClass.SKILL_FAILED,
                                                tool="get_skill_feedback",
                                                code="POLL_FAILED",
                                                step_idx=state["queue_idx"]),
                                "route": "except"}
                    continue
                poll_failures = 0
                if fb_res.data["status"] == "executing":
                    if time.monotonic() - t_start > budget_s:
                        log.emit("observer", "watchdog_triggered", kind="skill_timeout")
                        await reg.call("cancel_skill", {"skill_goal_id": sid},
                                       caller="observer")
                        return {"active_skill_goal": None,
                                "fault": _fault(FaultClass.SKILL_FAILED,
                                                code="SKILL_WATCHDOG_TIMEOUT",
                                                step_idx=state["queue_idx"]),
                                "route": "except"}
                    continue

                r = await reg.call("get_skill_result", {"skill_goal_id": sid},
                                   caller="observer")
                if not r.ok:
                    return {"active_skill_goal": None,
                            "fault": _fault(FaultClass.SKILL_FAILED,
                                            tool="get_skill_result",
                                            code=r.error["code"],
                                            step_idx=state["queue_idx"]),
                            "route": "except"}
                # 【独立后置校验】不采信 skill 自报:回读末态,并记录是否与自报一致
                pc = await reg.call("verify_skill_postcondition", {"skill_goal_id": sid},
                                    caller="observer")
                evidence = pc.data if pc.ok else {"verified": False, "reason": "postcheck_unavailable"}
                log.emit("postcheck", "verify_manipulation", **evidence)
                if pc.ok and not evidence.get("agrees_with_skill", True):
                    log.emit("postcheck", "verify_disagrees_with_skill", **evidence)

                if r.data["status"] == "succeeded" and evidence.get("verified"):
                    log.emit("observer", "step_completed",
                             step={"kind": "vla_skill"}, steps=r.data.get("steps"))
                    return {"active_skill_goal": None,
                            "queue_idx": state["queue_idx"] + 1,
                            "skill_postcheck": evidence, "route": "exec"}
                fclass = (FaultClass.SKILL_UNSAFE
                          if r.data.get("code") == "VLA_UNSAFE_STOP"
                          else FaultClass.SKILL_FAILED)
                return {"active_skill_goal": None, "skill_postcheck": evidence,
                        "fault": _fault(fclass, code=r.data.get("code"),
                                        reason=r.data.get("terminal_reason"),
                                        verified=evidence.get("verified"),
                                        step_idx=state["queue_idx"]),
                        "route": "except"}

        if state.get("waiting_charge"):
            waited = 0
            while waited < MAX_WAIT_CHARGE_TICKS:
                await rt.adapter.wait(1)
                waited += 1
                st = await reg.call("get_robot_state", {}, caller="observer",
                                    poll=True)
                if st.ok and not st.data["docked"]:
                    # 防御:wait_charged 只允许在坞内执行(复审 finding)
                    return {"waiting_charge": False,
                            "outcome_hint": "charge_interrupted",
                            "route": "report"}
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

            # 终态判定先于电量水位:已经物理到达的目标要先记账,
            # 否则恢复后整段路白走(复审 finding:同一轮询里电量抢占吞掉成功)
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

            # 安全水位:低电量在飞抢占(评审 B1/M4)
            if (st["battery_pct"] < intent.battery_floor_pct
                    and target != DOCK and not state.get("battery_handled")):
                log.emit("observer", "watchdog_triggered", kind="battery",
                         battery_pct=st["battery_pct"])
                return {"fault": _fault(FaultClass.LOW_BATTERY,
                                        battery_pct=st["battery_pct"]),
                        "route": "except"}

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

    def _attempt_key(fclass: FaultClass, context: dict) -> str:
        """按故障实例计数(复审 finding:按类别累计会让第二个不可达点
        直接跳到链尾,得不到替代点机会)。实例判别:节点/边/工具;
        传感器与低电量保持类级(全局状态,升级语义正确)。"""
        instance = ""
        if fclass in (FaultClass.SKILL_FAILED, FaultClass.SKILL_UNSAFE):
            # 按队列步实例计数:不同 vla_skill 步是不同故障实例,共享计数会让第 N 个步骤
            # 一出错就 recovery_exhausted → 直接 abort_to_dock(复审实测)。
            instance = str(context.get("step_idx", ""))
        elif fclass in (FaultClass.NAV_BLOCKED, FaultClass.NAV_UNREACHABLE):
            instance = str(context.get("node") or context.get("edge") or "")
        elif fclass is FaultClass.TOOL_FAILURE:
            instance = str(context.get("tool") or "")
        return f"{fclass.value}:{instance}"

    async def exception_manager(state: AgentState) -> AgentState:
        fault = state["fault"]
        fclass = FaultClass(fault["fclass"])
        attempts = dict(state.get("attempts", {}))
        key = _attempt_key(fclass, fault["context"])
        n = attempts.get(key, 0)
        attempts[key] = n + 1
        stage = next_stage(fclass, n)
        log.emit("exception_manager", "fault_classified",
                 fclass=fclass.value, context=fault["context"],
                 attempt=n, priority=PRIORITY[fclass], stage=stage)

        updates: AgentState = {"attempts": attempts, "fault": None}

        # 兜底:同一实例恢复次数超过链长+2 → 不再原地打转,安全中止
        # (复审 critical 的防御纵深:任何未来的不推进恢复动作都终止于 reporter)
        if n > len(RECOVERY_CHAINS[fclass]) + 2:
            log.emit("exception_manager", "recovery_exhausted", key=key)
            if state.get("outcome_hint") == "hitl_abort":
                updates["outcome_hint"] = "recovery_exhausted"
                updates["route"] = "report"
                return updates
            updates["pending_action"] = {"type": "abort_to_dock",
                                         "reason": "recovery_exhausted"}
            updates["route"] = "replan"
            return updates

        # 无条件取消在飞目标:跳步/降级时底盘继续开向已放弃的目标会引发
        # 连环 NAV_BUSY 误报(复审 finding);取消失败(已终态)记日志
        gid = state.get("active_goal")
        if gid is not None:
            cancel_res = await reg.call("cancel_navigation", {"goal_id": gid},
                                        caller="exception_manager")
            if not (cancel_res.ok and cancel_res.data.get("canceled")):
                log.emit("exception_manager", "cancel_noop", goal_id=gid)
            updates["active_goal"] = None

        # 应急队列(回坞/充电/中止)的步骤受保护:不允许被跳过或替换目标,
        # 否则 wait_charged 可能在远离 dock 处执行(复审 finding)
        current_step = (state["queue"][state["queue_idx"]]
                        if state["queue_idx"] < len(state["queue"]) else {})
        if current_step.get("protected") and stage in (
                "skip_step_degraded", "substitute_target", "escalate_hitl"):
            updates["pending_action"] = {"type": "abort_to_dock",
                                         "reason": "protected_step_recovery"}
            updates["route"] = "replan"
            return updates

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
            if target == DOCK:
                # dock 永不可被替代:回坞是安全兜底(复审 finding)
                updates["pending_action"] = {"type": "abort_to_dock",
                                             "reason": "dock_unreachable"}
                updates["route"] = "replan"
                return updates
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
                attempts[key] = n + 2
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

    def _skip_current_step(queue: list[dict], i: int, reason) -> int:
        """跳过当前步;若为 navigate,连带跳过紧随其后、绑定同一目标节点的
        perceive/report 步(复审 finding:配对感知步曾在错误节点执行并被记为完成)。
        返回新的 queue_idx。"""
        step = queue[i]
        log.emit("replanner", "step_skipped", step=step, reason=reason)
        new_i = i + 1
        if step["kind"] == "navigate":
            target = step.get("target")
            while new_i < len(queue) and queue[new_i].get("at") == target:
                log.emit("replanner", "step_skipped", step=queue[new_i],
                         reason="paired_with_skipped_navigate")
                new_i += 1
        return new_i

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
            new_i = _skip_current_step(queue, i, action.get("reason"))
            updates["queue_idx"] = new_i
            updates["degraded_steps"] = (state.get("degraded_steps", [])
                                         + queue[i:new_i])
        elif atype == "degrade_sensor":
            updates["sensor_degraded"] = True
            step = queue[i] if i < len(queue) else None
            if step is not None:
                # 终链阶段必须推进:感知步跳过;非感知步(navigate 持续失败等)
                # 也按跳步处理,否则同一步无限重发(复审 critical)
                new_i = _skip_current_step(queue, i, "degrade_terminal")
                updates["queue_idx"] = new_i
                updates["degraded_steps"] = (state.get("degraded_steps", [])
                                             + queue[i:new_i])
        elif atype == "dock_resume":
            # 快照剩余任务(含当前步),队列换成 回坞+等充电(评审 M4);
            # 应急步骤受保护,不允许后续恢复跳过/替换(复审 finding)
            snapshot = [dict(s) for s in queue[i:]]
            updates["resume_queue"] = snapshot
            updates["queue"] = [
                {"kind": "navigate", "target": DOCK, "protected": True},
                {"kind": "wait_charged", "protected": True},
            ]
            updates["queue_idx"] = 0
            log.emit("replanner", "queue_snapshot", snapshot=snapshot)
        elif atype == "abort_to_dock":
            updates["queue"] = [{"kind": "navigate", "target": DOCK,
                                 "protected": True}]
            updates["queue_idx"] = 0
            updates["resume_queue"] = None
            updates["outcome_hint"] = ("hitl_abort"
                                       if "hitl" in str(action.get("reason"))
                                       else "safe_abort")
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


async def run_graph(rt: Runtime, mission_queue: Optional[list[dict]] = None) -> dict:
    """mission_queue:D2 复合任务用(nav + vla_skill 混编)。不给则走 intent 生成的巡检队列
    —— 90-run 走的正是后者,故本参数对既有评测完全中性。"""
    app = build_graph(rt)
    initial: AgentState = {"mission_queue": mission_queue} if mission_queue else {}
    final = await app.ainvoke(initial, config={"recursion_limit": RECURSION_LIMIT})
    return final
