# povgen — POV 第一人称渲染管线(Godot 4,headless)

把任意评测 run 的**地面真值事件日志**渲染成近真实的机器人第一人称视角视频:
仓库场景由同一张拓扑图程序化生成(货架/走廊导引线/节点标记/充电桩/受限区/禁入区,
全原创资产),相机沿真值轨迹逐 tick 运动,受阻边上的箱堆在故障注入 tick 出现,
perceive 时刻叠加 VLM 结构化观测(bbox + label + confidence,绝不返回动作)。

![受阻凝视](../docs/screenshots/pov_blocked.png)
![VLM 感知叠加](../docs/screenshots/pov_perceive.png)

## 与公开真实视频方案的取舍(为什么自制)

真实机器人 POV 数据集(SCAND 等)/仓库 AMR 宣传片:授权不清晰(demo 要可分发)、
且**无法与本项目的拓扑和事件流逐 tick 对齐**——讲不出"轨迹来自地面真值"的故事。
自制:可控、可复现、tick 级同步、零版权风险。诚实标注:渲染是风格化仿真,不是实拍。

## 运行(依赖 gamecraft-runner 容器:Godot 4.6 + Xvfb + ffmpeg)

```powershell
# 1. 从事件日志导出轨迹(受阻间奏自动重建:撞箱→僵持→倒车→绕行)
.venv\Scripts\python scripts\export_traj.py runs\nav_blocked\seed_0.jsonl <mount>\povgen\traj.json

# 2. 单帧验证(迭代镜头)     3. 全量渲帧(--fpt 5 → 30fps 下 6 tick/s)
docker exec gamecraft-runner bash /games/povgen/shots.sh
docker exec gamecraft-runner bash /games/povgen/render.sh

# 4. 合成
ffmpeg -framerate 30 -i frames\f_%05d.png -c:v libx264 -pix_fmt yuv420p clip_pov.mp4
```

本仓库的 povgen/ 是源码存档;运行时需拷到 runner 挂载的 games 目录(见 shots.sh 内路径)。

## 镜头语言(Main.gd)

- 停滞时保持"来向"朝向 → 正对障碍;感知窗口 yaw 单独转向异常物体(位置仍走真值);
- HUD:tick/电量条/事件字幕(红=故障与水位,青=导航与恢复)/REC 标记;
- VLM 框:异常物 AABB 8 角点 unproject → 屏幕外接矩形,behind-check 防背面误画。
