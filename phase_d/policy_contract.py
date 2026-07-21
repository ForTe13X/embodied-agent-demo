"""版本化 Policy Contract(D1)—— learned policy 与 runtime 之间的可校验接口契约。

**为什么要有它**:runtime 监管的是一个**会变的、第三方的** policy(今天是 mock,明天可能是
SmolVLA/ACT 微调权重)。没有版本化契约,policy 换代时 runtime 只能靠"跑起来看"发现不兼容;
而 chunk 的 horizon / 观测新鲜度这类语义又恰恰是**安全相关**的(拿陈旧感知执行动作 = 拿旧世界
的判断操作新世界)。契约把这些语义**写死成可校验的数据**,并带版本号。

## 三个此前含糊、现在钉死的语义(codex 评审:action/execution horizon 与 sensor freshness)

- **action_horizon**:policy 一次推理最多吐多少个动作(chunk 长度上限)。超出即违约。
- **execution_horizon**:这批动作里**最多允许执行多少个**,然后必须换新 chunk。
  必须 ≤ action_horizon。此前代码里 `execution_horizon` 实为"queue 低于此值就提前推理"的
  **补片阈值**,和名字含义相反 —— 真正的执行边界当时**根本不存在**:一个 chunk 一旦被接收,
  它剩下的动作会一直执行到队列空,哪怕感知早已过期。
- **观测新鲜度(freshness)**:此前只在 **chunk 接收时**判一次 stale;**执行每个动作时不再判**。
  契约把它改成**逐动作**判定:动作所依据的观测 seq/时间落后超过阈值 → 丢弃该动作,不执行。

一句话:*chunk 级* 的新鲜度检查挡不住 *动作级* 的陈旧执行,这是本契约要修的核心语义漏洞。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# 契约版本:policy 与 runtime 各自声明,不兼容即拒绝装载(而不是跑起来才炸)。
# 语义化版本:major 不同 = 不兼容;minor 提升 = 向后兼容的放宽。
POLICY_CONTRACT_VERSION = "1.0"

ACTION_DIM = 7  # dx dy dz droll dpitch dyaw gripper(与 action_types.ACTION_DIM 一致)


class ContractViolation(Exception):
    """policy 输出违反契约。由 runtime 捕获并按"丢弃该 chunk"处理,不崩任务。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True)
class PolicyContract:
    """runtime 侧钉住的契约。policy 必须声明兼容的 version 才被装载。"""

    version: str = POLICY_CONTRACT_VERSION
    action_dim: int = ACTION_DIM
    action_horizon: int = 16          # 一次推理最多吐多少动作
    execution_horizon: int = 8        # 这批最多执行多少个就必须换新 chunk(≤ action_horizon)
    # —— 新鲜度分两个预算,别混用(第一版曾混用,导致 chunk 执行两步就"过期",chunking 失去意义)——
    # 【交付预算】chunk 回来时相对当前观测最多落后多少(= 推理+传输的迟到容忍),接收时判。
    max_obs_age_steps: int = 2
    max_obs_age_s: float = 0.5
    # 【执行预算】= 交付预算 + execution_horizon:执行一个 chunk 的第 k 个动作,本来就意味着
    # 依据的是 k 步之前的感知——这是 chunked control 的固有性质,不该被当成"过期"。见 execution_age_budget_steps。

    def __post_init__(self):
        if self.execution_horizon > self.action_horizon:
            raise ValueError(
                f"execution_horizon({self.execution_horizon}) 不得大于 "
                f"action_horizon({self.action_horizon}):执行数不能超过预测数")
        if self.action_dim != ACTION_DIM:
            raise ValueError(f"action_dim 必须为 {ACTION_DIM}")

    # ---- 兼容性 ----------------------------------------------------------
    def accepts_version(self, policy_version: Optional[str]) -> bool:
        """major 相同即兼容(minor 只做向后兼容的放宽)。None/畸形 → 不兼容。"""
        if not isinstance(policy_version, str) or "." not in policy_version:
            return False
        return policy_version.split(".")[0] == self.version.split(".")[0]

    def assert_policy_compatible(self, policy) -> None:
        """装载期校验:policy 必须声明 contract_version 且 major 相符。"""
        ver = getattr(policy, "contract_version", None)
        if not self.accepts_version(ver):
            raise ContractViolation(
                "POLICY_CONTRACT_MISMATCH",
                f"policy 声明契约版本 {ver!r},runtime 要求 {self.version!r}(major 必须相同)")

    # ---- chunk 级校验(接收时) --------------------------------------------
    def validate_chunk(self, chunk, *, current_seq: int, now: float,
                       mission_id: str) -> None:
        """接收 chunk 时校验。违约抛 ContractViolation,由 runtime 丢弃该 chunk。"""
        if getattr(chunk, "mission_id", None) != mission_id:
            raise ContractViolation("CHUNK_WRONG_MISSION",
                                    f"chunk 属于 {getattr(chunk, 'mission_id', None)!r},当前任务 {mission_id!r}")
        actions = getattr(chunk, "actions", None)
        if not isinstance(actions, (tuple, list)) or len(actions) == 0:
            raise ContractViolation("CHUNK_EMPTY", "chunk 不含动作")
        if len(actions) > self.action_horizon:
            raise ContractViolation(
                "CHUNK_OVER_HORIZON",
                f"chunk 含 {len(actions)} 个动作,超过 action_horizon={self.action_horizon}")
        for i, a in enumerate(actions):
            delta = getattr(a, "delta", None)
            if not isinstance(delta, (tuple, list)) or len(delta) != self.action_dim - 1:
                raise ContractViolation(
                    "ACTION_BAD_SHAPE",
                    f"第 {i} 个动作 delta 维度应为 {self.action_dim - 1},实为 {delta!r}")
        # 注:非有限值(NaN/inf)不在这里拒 —— 那是 SafetyShield 的确定性边界职责,
        # 契约只管"形状/版本/时序",安全投影独立于契约(两层不互相替代)。
        self.assert_fresh(getattr(chunk, "observation_seq", None), None,
                          current_seq=current_seq, now=now, what="chunk")

    # ---- 动作级新鲜度(执行每一步时) --------------------------------------
    def is_fresh(self, obs_seq: Optional[int], obs_t: Optional[float], *,
                 current_seq: int, now: float) -> bool:
        if not isinstance(obs_seq, int):
            return False
        if current_seq - obs_seq > self.max_obs_age_steps:
            return False
        if obs_t is not None and (now - obs_t) > self.max_obs_age_s:
            return False
        return True

    def assert_fresh(self, obs_seq: Optional[int], obs_t: Optional[float], *,
                     current_seq: int, now: float, what: str = "action") -> None:
        if not self.is_fresh(obs_seq, obs_t, current_seq=current_seq, now=now):
            raise ContractViolation(
                "OBSERVATION_STALE",
                f"{what} 依据的观测 seq={obs_seq} 相对当前 seq={current_seq} 过期 "
                f"(容忍 {self.max_obs_age_steps} 步 / {self.max_obs_age_s}s)")

    # ---- 执行期新鲜度(逐动作;预算含 chunk 固有跨度) ----------------------
    @property
    def execution_age_budget_steps(self) -> int:
        """执行第 k 个动作时依据的感知本就有 k 步之龄 —— 故执行预算 = 交付预算 + 执行边界。
        超出即说明该 chunk 已被"拖着用"太久(例如控制环被别的事阻塞),剩余动作必须丢弃。"""
        return self.max_obs_age_steps + self.execution_horizon

    def is_executable(self, obs_seq: Optional[int], *, current_seq: int) -> bool:
        """逐动作执行前判定。只用 seq(确定性、与控制周期无关);时间维度已在接收时把过关。"""
        if not isinstance(obs_seq, int):
            return False
        return (current_seq - obs_seq) <= self.execution_age_budget_steps
