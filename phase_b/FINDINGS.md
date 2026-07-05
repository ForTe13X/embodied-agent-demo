# Phase B Day 1 结果与发现(实测,2026-07-04)

在本机(Windows 11 + WSL2 + Docker Desktop 29.3.1)真实跑通了 Day 1 止损冒烟。**止损点通过**。

## 通过了什么(契约层,决定性)

| 检查 | 结果 | 证据 |
|---|---|---|
| Nav2 Jazzy 容器构建 | ✅ 5.08GB(清华镜像源) | `docker build phase_b` |
| Nav2 全栈 headless 起来 | ✅ 所有 lifecycle 节点 active | bt_navigator/planner/controller/behavior/collision_monitor/route_server 等 |
| `/navigate_to_pose` action 契约 | ✅ 目标接受 → feedback → 终态 | goal accepted;feedback 字段 = 设计文档 §3(current_pose/distance_remaining/number_of_recoveries) |
| 可达目标终态 | ✅ SUCCEEDED,error_code=0 | 目标 (0.5,0) |
| **不可达目标错误码** | ✅ ABORTED,**error_code=208**(规划器码段) | 目标 (1.5,0.5) 落在 tb3 障碍里,`compute_path_to_pose` 返 "Failed to create plan" |

**结论**:我们 mock 的 `send_goal/feedback/cancel/result` 异步 goal-handle 契约与真实 Nav2
**1:1 对得上**;错误码 208 落在规划器码段,验证了设计文档 §4 的错误码上浮机制
(不可达故障 = 规划器码经 `error_code_name_prefixes` 上浮,而非 NavigateToPose 自身的码)。

## 踩到并解决的坑(留作复现记录)

1. **apt 装 nav2 网络失败**(IPv6 超时 + packages.ros.org 500 + deb-src 404):
   换清华 TUNA 镜像源 + 强制 IPv4 + 去掉 `deb-src`。见 Dockerfile。
2. **Nav2 bringup 中止**(`global_costmap` 激活失败,`Invalid frame ID "map"`):
   loopback_simulator 要先收到 `/initialpose` 才发布 map→odom TF,而 autostart 在此之前
   就激活代价地图、10s 等不到 map 帧即中止。修复:launch 一起来就后台循环发 `/initialpose`,
   让 TF 在代价地图激活窗口内建立。
3. **BasicNavigator 卡在 amcl 等待**:loopback 无 amcl,`waitUntilNav2Active(localizer='robot_localization')`
   跳过 amcl 等待。
4. **initialpose 循环干扰导航**:导航前必须停掉循环(否则每秒把机器人瞬移回原点)。

## 未通过 / 交给 Day-2 的项(诚实清单)—— ✅ 已在 Day 2 修复

> **更新(Day 2)**:下述"几乎不平移"已定位为 **tb3_world 地图问题**并修复。换成我们自建的
> 开阔走廊地图后,机器人真实平移 15.36m、控制器满速 ~0.49 m/s。详见下方「Day 2 结果」。

**机器人在 loopback 里几乎不平移**(TF 末位移 ≈ 0,却报 SUCCEEDED)。诊断链:

- `/cmd_vel` 12s 采样全零;`/cmd_vel_nav`(控制器直出)只有 **~0.004 m/s** 的微速度;
- 不是 collision_monitor 拦的(`/cmd_vel_nav` 就已经是微速度);
- 是 tb3 默认控制器配置 + turtlebot3_world 地图在原点附近的组合导致控制器几乎不前进,
  且 goal checker(容差 0.25m)在这种配置下判成功。

**为什么不在这里深追**:Phase B 真正的世界是**我们自己从 layout 表生成的占据栅格 + 航点**
(设计文档 §3/§10 Day-2),不会用 tb3_world 这张地图,也会为它配控制器参数。在一张即将丢弃的
tb3 地图上调控制器,越过了 Day-1 止损点问的问题(「契约能否对上真实 Nav2」——已 YES)。

**Day-2 待办**(移动性属这一档):
- layout 表 → 占据栅格 PGM + `waypoints.yaml`(货架=占据、走廊=自由);
- 裸 BT xml(无 RecoveryNode,恢复归编排层);
- `RclpyAdapter` 实现契约(`BasicNavigator` 打底,已在 smoke_day1.py 验证可用);
- 配控制器 + 验证机器人真平移;keepout 滤镜做受阻边故障。

## Day 2 结果(实测,2026-07-04)

**三项全过。** 用【我们从 layout 表生成的开阔走廊地图】+【裸 BT】跑通了真实平移与完整
RobotAdapter 契约。Day1 遗留的"机器人几乎不平移"已定位并修复。

### Day2-A:layout → 世界(单一真值)
`gen_world.py` 读 `embodied_agent.world.default_map()`(拓扑真值)+ 本文件的 px 布局,纯 stdlib
产出:`world/map.pgm`+`map.yaml`(走廊自由、其余占据)、`world/waypoints.yaml`(node→米坐标)、
`world/topo.yaml`(节点 access/neighbors + 边,供容器侧 adapter 读)、`world/keepout.pgm`(f1 禁入区,
留作受阻边故障)、`world/map_preview.png`。地图 744×420 格(37.2×21.0m @ 0.05)。
**关键参数**:走廊半宽 1.0m(2m 宽通道)——减去默认 inflation 0.70m 仍留 0.30m 零代价中线,
这正是 Day1 不动的机理修复点(tb3_world 原点杂物 → MPPI 的 CostCritic 压制前进)。

### Day2-B:自定义 launch + 真平移(假设隔离实验)
`tb3_loopback_simulation.launch.py` 直接吃 `map:=` 和 `params_file:=`,无需从零写 launch。
隔离实验(只换地图、不动参数):机器人从 dock(1.2,6.0)真实平移 **15.35m** 到 c2(16.55,6.0),
距目标 0.25m,SUCCEEDED,controller 满速 **~0.49 m/s**(Day1 只有 ~0.004 m/s)。
→ **Day1 不动的真因 = tb3_world 地图,不是控制器/collision_monitor**,换开阔地图即修复。
`smoke_day2.py` 判据升级为严格四条:SUCCEEDED ∧ 位移>3m ∧ 距目标<0.6m ∧ recoveries=0。

### 裸 BT(`bt/bare_nav_to_pose.xml` + `nav2_params.yaml`)
去掉默认 BT 的 RecoveryNode/Spin/BackUp/Wait/ClearCostmap 自恢复,只留「1Hz 重规划 + 跟踪」;
并从 `bt_navigator.navigators` 移除 navigate_through_poses(否则它回落到内置含恢复的树)。
配裸 BT 重跑 day2 冒烟:15.34m 到达、SUCCEEDED、**recoveries=0**(无自恢复,符合设计:恢复归
编排层)。规划/跟踪失败即上抛 error_code → ABORTED,故障可观测、可归因。

### Day2-C:RclpyAdapter 实现 RobotAdapter 契约(`rclpy_adapter.py`)
同一异步 goal-handle 接口的真实实现,`smoke_day2c.py` 对着真实 Nav2 冒烟。**已覆盖**的契约项:

| 契约项 | 实测 |
|---|---|
| **无速度接口(结构性)** | 本【adapter 节点】publishers 仅 `/initialpose /parameter_events /rosout`,无 cmd_vel/velocity/torque(注:controller_server 当然发 `/cmd_vel_nav`;此断言是"adapter 不暴露速度接口",非"系统无速度话题") |
| send_goal 非阻塞 | 立即返回 `{goal_id}` |
| feedback 拓扑推进 | `current_node` dock→c1→c2、`edges_done` 0/2→1/2→2/2、`velocity`~0.49、`distance_remaining` 15.6→容差内(~0.26m 即判到达,非归零) |
| result 终态 succeeded | `succeeded`,末位姿 `pose=c2` |
| **cancel 成功路径** | 在飞 `cancel()`→`True`、`result.status=canceled`、cancel 后 `feedback` 也报 `canceled`(不误报 aborted) |
| 未知节点 | `send_goal("NOPE")` → `aborted/unknown_node`(不崩) |
| get_map/get_state | 形状与 mock 一致(get_map 含 `name`;get_state `battery_pct` 为 float);battery/sensor 诚实标 mock-only(loopback 无耗电/传感器模型,恒报 100/healthy) |
| `blocked` 非状态 | 语义与 mock 1:1(feedback 停滞判),**但见下方未覆盖清单** |

**未覆盖(诚实清单,留 Day-3)**:`aborted+reason=unreachable`(隔离节点/受阻边)与 `blocked`
停滞水位这两条,需先把 keepout 掩码接入运行栈(costmap_filter)才能在真实 Nav2 上触发;
本冒烟只跑了畅通路径,这两条**尚未在真实 Nav2 上验证**,当前只沿用 mock 语义。

**结论**:"同一接口 mock⇄rclpy 可换"在 **succeeded / canceled / unknown_node 三条终态 +
非阻塞 + feedback 拓扑推进 + 结构性无速度接口** 上是**在真实 Nav2 核对通过的事实**;
`unreachable`/`blocked` 分支列为 Day-3 待验。编排层接 rclpy 的前提修复见下方「Day-2 评审与修复」。

## Day 3 结果(实测,2026-07-04)

### Day3-A:keepout costmap 滤镜接入(`launch/keepout_servers.launch.py` + params)
`filter_mask_server`(发布掩码)+ `costmap_filter_info_server`(发布 `/costmap_filter_info`)+
它俩的 lifecycle_manager(autostart),须在主栈激活 global_costmap 前 active(否则 KeepoutFilter
等不到 latched filter_info 会激活失败)。`global_costmap` 加 `filters: ["keepout_filter"]`。
**掩码编码修正**(评审后自查):遵循 map_server 约定(白 255=可通行、黑 0=禁行),之前的
反向编码(黑=可通行)会把整图判致死。**对比法验证**(`smoke_day3a.py`):导航到 f1
(keepout 禁区)→ **ABORTED**;导航到 c2(自由)→ **SUCCEEDED**。f1 在底图 `map.pgm` 里本是
自由格,只有 keepout 生效才会被拒 —— 故这条对比是滤镜"选择性生效、不误伤主走廊"的决定性证据。

### Day3-B/1:unreachable 契约分支在真实 Nav2 上核对(`smoke_day3b.py`,补 Day2-C 缺口)
用 RclpyAdapter 对 keepout 里的 f1 发目标:`result = {status: aborted, reason: unreachable}`;
对照 c2 → `succeeded`,末位姿 pose=c2。这补上了评审指出的"Day2-C 只测 succeeded/canceled/
unknown_node、未在真实 Nav2 触发 aborted+unreachable"的覆盖缺口 —— `_map_result` 把 Nav2 的
`FAILED`(规划器无有效路径)上抛为契约的 `aborted/unreachable`,与 mock 语义 1:1。

### Day3-B/2:运行时受阻边注入 + 真实 Nav2 响应(`make_blocked_mask.py` + `smoke_day3b2.py`)
导航 dock→a1 途中(t=9s,机器人过了 c1),用 `filter_mask_server` 的 `load_map` 服务把 keepout
掩码**热替换**成"封死 c2-a1 中段"的掩码。实测:`distance_remaining` 在注入瞬间从 19.4m
**跳增到 28.4m(+10.7m)**,机器人改走绕行路线,最终仍到达 a1、**SUCCEEDED、recoveries=0**。

**两条诚实发现**:
1. **停滞 vs 自动改道**:mock 里受阻边=【停滞】(server 不自 replan,靠上层 observer 停滞水位
   发现)。真实 Nav2 的裸 BT 仍含 **1Hz 重规划**,只要存在绕行就【自动改道】(dist 跳增即证据),
   不停滞。这不是缺陷,而是"重规划住在哪一层"的差别——mock 的停滞语义建模的是**无自主重规划
   的底盘**;要在真实栈复现 mock 的停滞,需换成不含重规划的 BT 或封死所有绕行(→ 那就变 ABORTED)。
2. **几何重规划是 access-盲的**:改道轨迹**穿过了 r1(受限区)**——因为 r1 的"受限"是拓扑/注册表
   层规则,**不在 costmap keepout 里**。这验证了分层设计:底盘的几何规划不懂 access;要让【底盘】
   物理避开 r1 得把 r1 也涂进 keepout,否则门禁始终是编排层的职责(与 ADAPTER_CONTRACT §5 一致)。

### Day3-C:rosbag2/MCAP 审计录制(`smoke_day3c.sh`)
把整段【含受阻边故障注入】的导航录成 MCAP(Jazzy 默认存储)。SIGINT 收尾偶尔不写 metadata.yaml
(MCAP 分块写、靠 footer 收尾),用 `ros2 bag reindex -s mcap` 从 MCAP 重建元数据即可读。
实测审计包(`ros2 bag info`):storage=mcap、67s、13903 条,全链路留痕:

| topic | 条数 | 审计意义 |
|---|---|---|
| `/navigate_to_pose/_action/feedback` | 5948 | 契约级进度(需 `--include-hidden-topics`,是 _action 隐藏话题) |
| `/tf` | 6702 | 地面真值轨迹 |
| `/plan` | 59 | 规划器输出(含改道) |
| `/cmd_vel_nav` | 1189 | 控制器直出(评审指出的正确速度源) |
| `/keepout_filter_mask` | **2** | **故障注入留痕**:初始掩码 + 运行时热替换 |
| `/costmap_filter_info` | 1 | 滤镜配置 |

产出 `phase_b/audit_bag/audit_bag_0.mcap`(~5.8MB,可 `ros2 bag play` 回放)。

### Day3-D:真实栈复现 mock 的 blocked/停滞语义 + base 帧断言(`smoke_day3d.py`,评审两项做实)
Day3-B/2 里默认裸 BT(1Hz 重规划)遇受阻边【自动改道】,所以 mock 的"停滞"分支当时仍未在真实
栈触发。这里用【no-replan 裸 BT】(`bt/bare_nav_to_pose_noreplan.xml`:`Sequence` 而非
`PipelineSequence`+`RateController`,计划只算一次)+【局部代价地图也装 keepout】,导航 dock→a1
途中封死 c2-a1。实测:机器人跟着旧计划开到受阻段,velocity 从 ~0.5 掉到 **0.007**、
`distance_remaining` 冻在 7.2m、**`stall_ticks` 达 6** —— 正好越过 observer 的
`STAGNATION_THRESHOLD_TICKS=6` 水位。这就是 mock 的 blocked 表现,证明"blocked→停滞→上层水位
判定"在**无自主重规划的底盘**上 1:1 成立。

**两条真实 Nav2 受阻行为并存(诚实并列)**:默认裸 BT → 自动改道(Day3-B/2);no-replan BT → 停滞
(本项)。mock 的停滞语义建模的是后者;换哪种由 BT 决定,编排层的停滞水位对两者都是正确兜底。

**base 帧断言**:bootstrap 后显式校验 `base_link↔base_footprint` static TF 连通(实测 True)。
该连通此前隐式依赖 tb3 URDF,换机器人描述会断链;现在缺失即报错而非神秘失败(评审 PLAUSIBLE 项)。

**启动抖动**(评审 PLAUSIBLE):提供 opt-in 的 `bt/bare_nav_to_pose_retry.xml` —— 仅对
`ComputePathToPose` 加 `RetryUntilSuccessful(3)` 吸收 costmap 首帧抖动,**不引入任何物理自恢复**
(Spin/BackUp/ClearCostmap)。默认树仍是真正零容错的 `bare_nav_to_pose.xml`(recoveries=0 固化);
retry 变体供批量评测避免"首拍抖动被误判为不可达"污染统计。冒烟:retry 变体经 `smoke_bt_check.sh`
(nav2 active + c2 导航 SUCCEEDED + recoveries=0);no-replan 变体经 `smoke_day3d.sh`(上面的停滞演示)。

## Day 4 结果:编排整合(实测,2026-07-04)—— Phase B 高潮

**把【同一套 LangGraph 编排图 + Tool Registry】接到【真实 Nav2】上,编排代码一行未改,只换 adapter。**

### 做法(`real_runtime.py` + `Dockerfile.orch`)
- 镜像 `phase-b-nav2-orch` = nav2 镜像 + `pip install langgraph pydantic pyyaml rich`(清华源)。
- 三个 shim 喂给原样复用的 `embodied_agent.runtime.Runtime`(普通 dataclass,不做类型校验):
  - `RealClock` —— `.tick = 墙钟秒`(替代 SimClock 虚拟 tick;`MAX_GOAL_TICKS=120`→120s 超时);
  - `RealWorld` —— 只提供编排真正读的 `.topo` / `.robot_node`(后者取自 `adapter._cur_node()`)/ `.battery_pct`(恒 100);
  - `NoopInjector` —— 真实栈无 mock 的工具级故障注入(故障走 keepout 掩码)。
- `registry / graph / memory / intent / selector / event_log` **全部原样复用**,`adapter = RclpyAdapter`。

### 场景与结果(`run_real_mission.py`,`smoke_day4.sh`)
巡检 a2→a3;运行前用 `load_map` 把 a3 用 keepout 隔离(注入 NAV_UNREACHABLE)。实测事件流(墙钟 tick):

```
t=  0 planner            plan_built
t= 66 observer           navigate a2 succeeded  →  perceive a2
t= 67 exception_manager  candidates_enumerated  target=a3 cands=['a3_alt']
t= 67 exception_manager  candidate_chosen       chosen=a3_alt
t= 67 replanner          recovery_applied       substitute a3 → a3_alt
t= 91 observer           navigate a3_alt succeeded  →  perceive a3_alt
t=179 observer           navigate dock succeeded
t=179 reporter           run_summary  visited=[a2,a3_alt,dock] subs=[a3→a3_alt] outcome=None
```

**闭环成立**:`navigate(a3)` 被真实 Nav2 判 ABORTED/unreachable → observer 水位判 NAV_UNREACHABLE
→ exception_manager 确定性查表 `substitute_target` → 从 a3 的合法邻接闭集枚举出 `a3_alt`
→ RuleSelector 选 index 0 → replanner 改写队列 → `navigate(a3_alt)` 成功 → 归坞。
672 条事件、schema 与 mock **逐字段一致**,故同一 viewer/replay/metrics 工具直接可用
(产出 `phase_b/real_mission_events.jsonl`)。

**这证明了 Phase A 设计的核心主张**:LLM/规则编排只做高层意图与闭集选择,恢复是确定性查表,
底盘可换 —— mock 上跑通的「故障→分类→枚举→选择→重规划」在真实 Nav2 上**一行编排代码不改**即复现。

**边界(诚实)**:90 条预注册评测(`RESULTS.md`)仍是 mock adapter(见 `metrics.py` 标注);
Day-4 是**单次真实集成演示**,不是把评测搬上真机。battery/sensor 类故障仍 mock-only。

## Day-2 评审与修复(多 agent 对抗评审,2026-07-04)

Day-2 交付物过了一轮 5 维对抗评审(契约一致性 / adapter 正确性 / 世界几何 / BT+params /
冒烟+文档),26 条发现里 22 条经独立验证幸存。已修复的要点:

- **[高] get_state `battery_pct=None` → 上层电量水位 `None < 20.0` TypeError**:换 RclpyAdapter
  后 observer 首个在飞轮询即崩。改为报 float `100.0`(loopback 无耗电模型=额定,mock-only 语义),
  保住契约类型;冒烟加 `battery_pct` 是数值的断言。
- **[高] cancel() 不置终态 → 取消后 feedback 误报 aborted**:引入 `_converge()` 统一终态收敛
  (读一次 getResult→置 terminal→清 _active,幂等),cancel 显式落 `canceled`。修掉三处连带 bug:
  ①canceled 不再被误报 aborted;②终态出现即释放 `_active`(不再泄漏 busy 锁);③feedback 与
  result 的 status 恒一致。冒烟新增 cancel 成功路径用例(已验证 canceled 自洽)。
- **[中] edges_done 用忽略 access 的成本最短路 → Nav2 绕开 restricted 时进度回跳/失真**:改为
  send_goal 时按【access 规则】(与 world.route 同)算定固定路线,沿路线单调推进(不回退、不越界)。
- **[中] _cur_node 用 `min()` tie-break → 等距中点抖动污染 current_edge**:加滞回(新节点近于当前
  节点超 margin 才切)。
- **[中] stall 把到达减速段误判 + 死参数 stall_needed**:停滞仅在"距目标尚远且 dist 不降"时累加
  (近目标免判),删掉从未使用的 stall_needed。
- **[低] get_map 缺 `name` 键**:topo.yaml 增补节点名,get_map 补 `name`,与 `world.to_dict()` 逐键一致。
- **[低] w2cell 与 map_server 行列约定有系统性 -1 行偏移 + round/floor 分歧**:改为
  `col=floor(wx/RES)`、`row=(H-1)-floor(wy/RES)`,与 map_server worldToMap 逐字一致。
- **[低] navigate_through_poses 仍挂默认含恢复树**:从 `bt_navigator.navigators` 移除,使"无自恢复"
  成为结构事实(未注册→走不到内置恢复树),而非仅 navigate_to_pose 的口头承诺。
- **[低] 诚实性**:`smoke_day2.py` 把 `recoveries==0` 固化为硬回归断言;FINDINGS Day2-C 表明确
  标注 unreachable/blocked 未覆盖;`distance_remaining` 措辞由"→0"改为"→容差内(~0.26m)";
  速度指标应读 `/cmd_vel_nav`(控制器直出)而非 `/cmd_vel`(velocity_smoother+collision_monitor 后)。

被评审**证伪(未修)**的 4 条:send_goal busy 分支的 KeyError(registry 已用 `if "error" in res`
守卫)、error_code_name_prefixes 脆弱性(adapter 根本不读 error_code,只按 TaskResult 枚举映射)、
"无速度接口"措辞(表格右格已带"本节点"限定)、pipefail 无效(该行无管道,`exit $RC` 忠实)。

仍**留作 Day-3**(PLAUSIBLE,当前无实测影响):裸 BT 零容错下启动抖动可能把首拍规划失败误判为
故障(实测 recoveries=0、未出现);base_link↔base_footprint 帧连通依赖 tb3 URDF(换机器人描述需加断言)。

## 复现

```powershell
docker build -t phase-b-nav2 phase_b
# Day1 契约冒烟(tb3_world 地图)
docker run --rm -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day1.sh
# Day2 生成世界(宿主 venv)
.\.venv\Scripts\python phase_b\gen_world.py
# Day2-B 隔离实验(我们的地图 + 默认参数):证明真因是地图,~15.36m
docker run --rm -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day2.sh
# Day2-B 裸 BT(我们的地图 + nav2_params.yaml=裸 BT):~15.34m,recoveries=0
docker run --rm -e PARAMS=/hostpb/nav2_params.yaml -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day2.sh
# Day2-C RclpyAdapter 契约冒烟(含 cancel 成功路径)
docker run --rm -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day2c.sh
# Day3-A keepout 滤镜接入(f1 禁区 ABORTED、c2 自由 SUCCEEDED)
docker run --rm -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day3a.sh
# Day3-B/1 unreachable 契约分支(adapter:f1→aborted/unreachable)
docker run --rm -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day3b.sh
# Day3-B/2 运行时受阻边注入(先生成受阻掩码,再热替换观察改道)
.\.venv\Scripts\python phase_b\make_blocked_mask.py c2 a1
docker run --rm -e TEST=smoke_day3b2.py -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day3b.sh
# Day3-C MCAP 审计录制(产出 phase_b/audit_bag/*.mcap)
docker run --rm -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day3c.sh
# Day3-D 复现 mock 停滞语义(no-replan BT + 局部 keepout)+ base 帧断言
docker run --rm -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day3d.sh
# BT 变体冒烟(默认 retry;BT=bare_nav_to_pose_noreplan.xml 可换)
docker run --rm -e BT=bare_nav_to_pose_retry.xml -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_bt_check.sh
# Day4 编排整合:先建 orch 镜像,再让 LangGraph 编排驱动真实 Nav2 跑故障恢复任务
docker build -f phase_b\Dockerfile.orch -t phase-b-nav2-orch phase_b
.\.venv\Scripts\python phase_b\make_blocked_mask.py a3   # 生成隔离 a3 的 keepout 掩码
docker run --rm -v "${PWD}:/repo" -v "${PWD}\phase_b:/hostpb" phase-b-nav2-orch bash /hostpb/smoke_day4.sh
# 诊断脚本:diag.sh(规划器)/ diag3.sh(cmd_vel)/ diag4.sh(cmd_vel 链路)
```
