"""Tool Registry:typed schema + 白名单 + 幂等重试 + 超时 + 熔断 + 审批 token。

规则(README 同步说明,面试必问):
- 白名单制:未知工具名 → 拒绝并记日志;输入 schema extra='forbid',未知超参同样拒绝;
- 幂等工具才自动重试(1 次),非幂等(navigate_to / report / HITL)绝不自动重试;
- 连续失败 ≥3 次熔断该工具(门禁类拒绝不计入熔断——那是调用方错误不是工具故障);
- 受限区导航需 HITL 审批 token:一次性、限 scope、限时效,由注册表铸造与核销,planner 不能自证;
- forbidden 节点 token 也不放行;电量低于静态红线时非 dock 目标需 battery_override token;
- LLM 永远拿不到速度/力矩接口——adapter 上根本不存在这类方法。

gates_on=False 仅用于评测消融(评审 M2):跳过安全门禁(白名单语义/访问级/token/电量闸),
schema 校验保留。此时地面真值 SafetyMonitor 会记录真实违规。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from .adapter import RobotAdapter, SensorUnhealthy
from .clock import SimClock
from .events import EventLog
from .faults import FaultInjector
from .hitl import HITLPolicy
from .safety import BATTERY_FLOOR_PCT
from .world import DOCK, TopoMap, edge_key

CIRCUIT_THRESHOLD = 3          # 连续失败→熔断
TIMEOUT_PENALTY_TICKS = 2      # 注入 timeout 时消耗的虚拟时间
TOKEN_TTL_TICKS = 60


# ---- 输入 schema(extra='forbid':未知参数=拒绝) ----------------------------

class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyIn(_In):
    pass


class NavigateToIn(_In):
    node_id: str
    approval_token: Optional[str] = None
    avoid_edges: list[list[str]] = []


class GoalIdIn(_In):
    goal_id: str


class PerceiveIn(_In):
    query: str = "anomaly"


class ReportFindingIn(_In):
    image_id: str
    label: str
    node_id: str


class AskHumanIn(_In):
    message: str
    scope: str


@dataclass
class ToolResult:
    ok: bool
    data: Optional[dict] = None
    error: Optional[dict] = None  # {code, message}


@dataclass
class ToolSpec:
    name: str
    input_model: type[_In]
    handler: Callable[..., Awaitable[dict]]
    idempotent: bool
    required_output_keys: tuple[str, ...] = ()


class ToolError(Exception):
    def __init__(self, code: str, message: str = "", retriable: bool = False):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.retriable = retriable


@dataclass
class _Circuit:
    consecutive_failures: int = 0
    open: bool = False


class ToolRegistry:
    def __init__(
        self,
        adapter: RobotAdapter,
        topo: TopoMap,
        clock: SimClock,
        event_log: EventLog,
        injector: FaultInjector,
        hitl: HITLPolicy,
        gates_on: bool = True,
    ):
        self.adapter = adapter
        self.topo = topo
        self.clock = clock
        self.log = event_log
        self.injector = injector
        self.hitl = hitl
        self.gates_on = gates_on
        self._call_counter = 0
        self._token_counter = 0
        self._tokens: dict[str, dict] = {}
        self._circuits: dict[str, _Circuit] = {}
        # 证据溯源账本(codex 评审 F-09):image_id → 拍摄时机器人所在节点。
        # report_finding 只接受【真拍过、且节点相符】的证据 —— 白名单能限制"调哪个工具",
        # 但不能保证该工具提交的 evidence 有来源;这本账把"来源"也钉死。
        self._capture_ledger: dict[str, str] = {}
        self.tools: dict[str, ToolSpec] = {}
        self._register_all()

    # ---- 对外唯一入口 ------------------------------------------------------

    async def call(self, name: str, args: dict | None = None, *,
                   caller: str = "agent", poll: bool = False) -> ToolResult:
        args = args or {}
        self._call_counter += 1
        call_id = f"call-{self._call_counter}"
        self.log.emit("registry", "tool_call", call_id=call_id, tool=name,
                      args=args, caller=caller, poll=poll)

        # 白名单
        if name not in self.tools:
            if self.gates_on:
                return self._reject(call_id, name, "UNKNOWN_TOOL",
                                    f"工具 {name!r} 不在白名单")
            return self._error(call_id, name, "TOOL_NOT_FOUND",
                               f"工具 {name!r} 不存在")
        spec = self.tools[name]

        # 输入 schema(始终开启)
        try:
            parsed = spec.input_model(**args)
        except ValidationError as e:
            # 只记录 pydantic 错误类型,不记录消息文本(消息随版本变化会破坏确定性回放)
            first = e.errors()[0]
            return self._reject(call_id, name, "SCHEMA_VIOLATION",
                                f"输入校验失败: {first.get('type', '')} @ "
                                f"{'.'.join(str(x) for x in first.get('loc', ()))}")

        # 安全门禁
        self._nav_zone_auth = False
        self._nav_battery_auth = False
        if name == "navigate_to" and self.gates_on:
            gate_err = self._navigate_gate(parsed)
            if gate_err is not None:
                return self._reject(call_id, name, *gate_err)

        # 熔断
        circuit = self._circuits.setdefault(name, _Circuit())
        if circuit.open:
            return self._error(call_id, name, "CIRCUIT_OPEN",
                               f"{name} 已熔断,本 run 内不再调用")

        # 执行(幂等工具允许 1 次自动重试)
        attempts = 2 if spec.idempotent else 1
        last_err: Optional[ToolError] = None
        for attempt in range(attempts):
            try:
                data = await self._execute(spec, parsed)
                circuit.consecutive_failures = 0
                self.log.emit("registry", "tool_result", call_id=call_id,
                              tool=name, ok=True, data=data, poll=poll,
                              attempt=attempt)
                return ToolResult(ok=True, data=data)
            except ToolError as err:
                last_err = err
                circuit.consecutive_failures += 1
                self.log.emit("registry", "tool_attempt_failed", call_id=call_id,
                              tool=name, code=err.code, attempt=attempt,
                              retriable=err.retriable)
                if circuit.consecutive_failures >= CIRCUIT_THRESHOLD:
                    circuit.open = True
                    self.log.emit("registry", "circuit_open", tool=name)
                    break
                if not (err.retriable and attempt + 1 < attempts):
                    break
        assert last_err is not None
        return self._error(call_id, name, last_err.code, last_err.message)

    # ---- 门禁 --------------------------------------------------------------

    def _navigate_gate(self, parsed: NavigateToIn) -> Optional[tuple[str, str]]:
        """两阶段:先校验全部门禁,全部通过才核销 token——
        部分通过不烧 token(复审 finding:双闸场景 token 被白白消耗)。"""
        node_id = parsed.node_id
        if not self.topo.has(node_id):
            return ("NOT_IN_MAP", f"节点 {node_id!r} 不在拓扑图内")
        access = self.topo.access(node_id)
        if access == "forbidden":
            return ("FORBIDDEN", f"节点 {node_id!r} 为禁入区,审批也不放行")
        needed_scopes: list[str] = []
        zone_auth = battery_auth = False
        if access == "restricted":
            err = self._check_token(parsed.approval_token, f"navigate_to:{node_id}")
            if err:
                return err
            needed_scopes.append(f"navigate_to:{node_id}")
            zone_auth = True
        state_battery = self._last_known_battery()
        if state_battery is not None and state_battery < BATTERY_FLOOR_PCT and node_id != DOCK:
            err = self._check_token(parsed.approval_token, f"battery_override:{node_id}")
            if err:
                return ("BATTERY_FLOOR",
                        f"电量 {state_battery}% 低于红线 {BATTERY_FLOOR_PCT}%,仅允许返回 dock 或经 HITL 审批")
            needed_scopes.append(f"battery_override:{node_id}")
            battery_auth = True
        for scope in needed_scopes:
            self._consume_token(parsed.approval_token, scope)
        self._nav_zone_auth = zone_auth
        self._nav_battery_auth = battery_auth
        return None

    def _check_token(self, token: Optional[str], scope: str) -> Optional[tuple[str, str]]:
        if token is None:
            return ("APPROVAL_REQUIRED", f"动作 {scope} 需要 HITL 审批 token")
        info = self._tokens.get(token)
        if info is None or info["used"] or info["scope"] != scope \
                or self.clock.tick > info["expires_tick"]:
            return ("INVALID_TOKEN", f"token 无效/过期/scope 不符: {scope}")
        return None

    def _consume_token(self, token: str, scope: str) -> None:
        self._tokens[token]["used"] = True
        self.log.emit("registry", "token_consumed", token=token, scope=scope)

    def _last_known_battery(self) -> Optional[float]:
        # 门禁用地面真值电量(经 adapter 读取,等价于底盘 BMS 上报,不经 LLM)
        world = getattr(self.adapter, "world", None)
        return None if world is None else world.battery_pct

    # ---- 工具实现 -----------------------------------------------------------

    def _register_all(self) -> None:
        a = self.adapter

        async def navigate_to(p: NavigateToIn) -> dict:
            # 门禁核销过的授权(token 一次一用)透传给底盘/地面真值监视器;
            # zone token 的 scope 是单节点:只把目标节点加入受限白名单,不授权路过其它受限区
            authorized = self._nav_zone_auth or self._nav_battery_auth
            avoid = {edge_key(*e) for e in p.avoid_edges if len(e) == 2}
            res = await a.send_goal(
                p.node_id, authorized=authorized, avoid_edges=avoid,
                restricted_ok_nodes=({p.node_id} if self._nav_zone_auth
                                     else frozenset()),
                allow_all_restricted=not self.gates_on,
                allow_forbidden_target=not self.gates_on,
                geofence_on=self.gates_on)   # F-01:运行期围栏随门禁总开关;消融关闭以如实测违规
            if "error" in res:
                raise ToolError("NAV_BUSY", res["error"])
            return {"goal_id": res["goal_id"]}

        async def get_nav_feedback(p: GoalIdIn) -> dict:
            fb = await a.feedback(p.goal_id)
            if fb is None:
                raise ToolError("UNKNOWN_GOAL", f"goal {p.goal_id} 不存在")
            return fb

        async def cancel_navigation(p: GoalIdIn) -> dict:
            ok = await a.cancel(p.goal_id)
            return {"canceled": ok}

        async def get_robot_state(p: EmptyIn) -> dict:
            return await a.get_state()

        async def get_topological_map(p: EmptyIn) -> dict:
            return await a.get_map()

        async def perceive(p: PerceiveIn) -> dict:
            try:
                return await a.sense(p.query)  # 只返回结构化观测,绝不返回动作
            except SensorUnhealthy:
                raise ToolError("SENSOR_UNHEALTHY", "传感器异常", retriable=False)

        async def capture_image(p: EmptyIn) -> dict:
            data = await a.capture()
            # 记账:这张图是在机器人【当前所在节点】拍的(证据来源)
            state = await a.get_state()
            self._capture_ledger[data["image_id"]] = state.get("pose")
            return data

        async def report_finding(p: ReportFindingIn) -> dict:
            # F-09 证据溯源:image_id 必须真拍过、node 必须在拓扑内、且必须是拍摄时的所在节点。
            # 伪造/越拓扑/张冠李戴的证据一律拒(不进 finding_reported,不冒充有来源的发现)。
            captured_at = self._capture_ledger.get(p.image_id)
            if captured_at is None:
                raise ToolError("EVIDENCE_UNVERIFIED",
                                f"image_id {p.image_id!r} 无捕获记录", retriable=False)
            if not self.topo.has(p.node_id):
                raise ToolError("EVIDENCE_UNVERIFIED",
                                f"node {p.node_id!r} 不在拓扑", retriable=False)
            if captured_at != p.node_id:
                raise ToolError("EVIDENCE_UNVERIFIED",
                                f"证据节点不符:拍于 {captured_at!r},报为 {p.node_id!r}",
                                retriable=False)
            self.log.emit("registry", "finding_reported", image_id=p.image_id,
                          label=p.label, node_id=p.node_id)
            return {"report_id": f"report-{p.image_id}"}

        async def return_to_dock(p: EmptyIn) -> dict:
            # geofence_on 随门禁总开关,与 navigate_to 一致(dock 恒 free 故不会触发,
            # 显式传值消除"依赖回坞路径无受限节点"的隐性前提;安全审查)
            res = await a.send_goal(DOCK, authorized=True, geofence_on=self.gates_on)
            if "error" in res:
                raise ToolError("NAV_BUSY", res["error"])
            return {"goal_id": res["goal_id"]}

        async def ask_human_confirmation(p: AskHumanIn) -> dict:
            self.log.emit("hitl", "hitl_request", message=p.message, scope=p.scope)
            decision = self.hitl.decide(p.message, p.scope)
            self.log.emit("hitl", "hitl_decision", scope=p.scope, **decision)
            if decision["decision"] != "approve":  # deny 与 timeout 同义:安全停
                return {"approved": False}
            self._token_counter += 1
            token = f"tok-{self._token_counter}"
            self._tokens[token] = {
                "scope": p.scope, "used": False,
                "expires_tick": self.clock.tick + TOKEN_TTL_TICKS,
            }
            return {"approved": True, "approval_token": token}

        def reg(name: str, model: type[_In], handler, idempotent: bool,
                out_keys: tuple[str, ...]) -> None:
            self.tools[name] = ToolSpec(name, model, handler, idempotent, out_keys)

        reg("get_robot_state", EmptyIn, get_robot_state, True,
            ("pose", "battery_pct", "nav_status", "sensor_health"))
        reg("get_topological_map", EmptyIn, get_topological_map, True, ("nodes",))
        reg("navigate_to", NavigateToIn, navigate_to, False, ("goal_id",))
        reg("get_nav_feedback", GoalIdIn, get_nav_feedback, True, ("status",))
        reg("cancel_navigation", GoalIdIn, cancel_navigation, True, ("canceled",))
        reg("perceive", PerceiveIn, perceive, True, ("objects",))
        reg("capture_image", EmptyIn, capture_image, False, ("image_id",))  # F-14:非幂等,避免自动重试产生第二次捕获
        reg("report_finding", ReportFindingIn, report_finding, False, ("report_id",))
        reg("return_to_dock", EmptyIn, return_to_dock, False, ("goal_id",))
        reg("ask_human_confirmation", AskHumanIn, ask_human_confirmation, False,
            ("approved",))

    async def _execute(self, spec: ToolSpec, parsed: _In) -> dict:
        injected = self.injector.tool_intercept(spec.name)
        if injected == "timeout":
            # 超时消耗的是真实世界时间:经 adapter 推进(电量衰减/故障触发同步发生),
            # 不是只拨快时钟(复审 finding:幽灵 tick)
            await self.adapter.wait(TIMEOUT_PENALTY_TICKS)
            raise ToolError("TIMEOUT", f"{spec.name} 超时(注入)", retriable=True)
        data = await spec.handler(parsed)
        if injected == "malformed":
            data = {k: v for k, v in data.items()
                    if k not in spec.required_output_keys[:1]}
        # F-14:不只查 key 是否存在,还要求非 None(空列表 []/布尔 False 都算有值,合法通过)——
        # 挡住 battery_pct=None 这类"键在、值空"的输出流进编排层数值比较。
        missing = [k for k in spec.required_output_keys if k not in data or data[k] is None]
        if missing:
            raise ToolError("SCHEMA_VIOLATION_OUT",
                            f"{spec.name} 输出缺少/为空字段 {missing}", retriable=True)
        return data

    # ---- 结果封装 -----------------------------------------------------------

    def _reject(self, call_id: str, tool: str, code: str, message: str) -> ToolResult:
        # 门禁拦截:单独事件类型,不计入熔断(评审 minor:三类计数分离)
        self.log.emit("registry", "guardrail_rejection", call_id=call_id,
                      tool=tool, code=code, message=message)
        return ToolResult(ok=False, error={"code": code, "message": message})

    def _error(self, call_id: str, tool: str, code: str, message: str) -> ToolResult:
        self.log.emit("registry", "tool_result", call_id=call_id, tool=tool,
                      ok=False, code=code, message=message)
        return ToolResult(ok=False, error={"code": code, "message": message})
