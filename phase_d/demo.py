#!/usr/bin/env python3
"""Phase D 演示:同一个 VLASkillRuntime 面对不同(含对抗)policy 的表现汇总。

跑法:.venv\\Scripts\\python phase_d\\demo.py
证明的主张:一个会吐【越界 / NaN / 抖动 / 陈旧】动作的 policy,被 runtime + SafetyShield
异步执行、确定性约束、可取消、事后可审计 —— 纯仿真、mock policy、不训练、不碰真机。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from mock_vla_policy import MockVLAPolicy, PolicyConfig  # noqa: E402
from safety_shield import SafetyShield  # noqa: E402
from tabletop_sim import TabletopSim  # noqa: E402
from vla_skill_runtime import SkillGoal, VLASkillRuntime  # noqa: E402


async def run_one(name, cfg, *, latency=0.0, cancel_after=None, timeout_s=3.0):
    rt = VLASkillRuntime(MockVLAPolicy(cfg, seed=0), SafetyShield(), TabletopSim(),
                         inference_latency_s=latency, events=[])
    goal = SkillGoal("m", "pick up the red block", timeout_s=timeout_s)
    if cancel_after is not None:
        task = asyncio.create_task(rt.execute(goal))
        await asyncio.sleep(cancel_after)
        rt.cancel()
        res = await task
    else:
        res = await rt.execute(goal)
    box = SafetyShield().cfg.box
    return name, res, box.contains(rt.sim.ee.pos)


async def main():
    scenarios = [
        ("nominal(正常)", PolicyConfig(), {}),
        ("out_of_bounds(冲界)", PolicyConfig(inject_out_of_bounds=True), {}),
        ("nan(非有限)", PolicyConfig(inject_nan=True), {}),
        ("jitter(大抖动)", PolicyConfig(jitter=0.03), {}),
        ("stale(高延迟)", PolicyConfig(), {"latency": 0.05, "timeout_s": 0.3}),
        ("cancel(中途取消)", PolicyConfig(target_pos=(1.0, 0, 0.06)), {"cancel_after": 0.05, "timeout_s": 5.0}),
    ]
    rows = []
    for name, cfg, kw in scenarios:
        rows.append(await run_one(name, cfg, **kw))

    print(f"\n{'场景':<20}{'成功':<5}{'终态原因':<34}{'安全夹取':<9}{'过期丢弃':<9}{'步数':<6}{'末端在盒内'}")
    print("-" * 96)
    for name, r, inbox in rows:
        print(f"{name:<20}{('是' if r.success else '否'):<5}{r.terminal_reason:<34}"
              f"{r.safety_interventions:<9}{r.stale_drops:<9}{r.steps:<6}{'✓' if inbox else '✗ 越界!'}")
    print("-" * 96)
    # 关键不变量:无论 policy 多离谱,末端【永远】在 workspace 内(shield 的硬保证)
    all_inbox = all(inbox for _, _, inbox in rows)
    print(f"\n关键不变量:所有场景末端都在 workspace 内 = {all_inbox}"
          f"  —— {'✓ policy 绕不过安全投影' if all_inbox else '✗ 有越界!'}")
    return 0 if all_inbox else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
