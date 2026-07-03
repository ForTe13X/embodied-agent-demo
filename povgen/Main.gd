extends Node3D
# POV 生成器:按 traj.json(事件日志导出的地面真值轨迹)驱动第一人称相机,
# 程序化仓库场景,perceive 时刻叠加 VLM 结构化观测。
# 用法(容器内):
#   godot --path . -- --traj /games/povgen/traj.json --shot /games/povgen/_s.png --tick 14
#   godot --path . -- --traj /games/povgen/traj.json --out /games/povgen/frames --fpt 5

const SCALE := 40.0          # 布局 px -> 米
const CAM_H := 1.15
const NODE_COLORS := {
	"dock": Color(0.35, 0.78, 0.42), "free": Color(0.32, 0.58, 0.92),
	"restricted": Color(0.92, 0.72, 0.25), "forbidden": Color(0.88, 0.35, 0.4),
}

var traj: Dictionary = {}
var cam: Camera3D
var obstacle: Node3D
var anomaly_box: MeshInstance3D
var fault_tick: float = 1e9
var _yaw: float = PI
var hud_tick: Label
var hud_caption: Label
var hud_rec: Label
var bat_fg: ColorRect
var vlm_rect: Panel
var vlm_label: Label
var vlm_head: Label
var scanline: ColorRect


func _ready() -> void:
	var args := {}
	var user_args := OS.get_cmdline_user_args()
	var i := 0
	while i < user_args.size():
		var a: String = user_args[i]
		if a.begins_with("--") and i + 1 < user_args.size():
			args[a.substr(2)] = user_args[i + 1]
			i += 2
		else:
			i += 1
	var traj_path: String = args.get("traj", "/games/povgen/traj.json")
	var f := FileAccess.open(traj_path, FileAccess.READ)
	traj = JSON.parse_string(f.get_as_text())
	f.close()
	if not traj["blocked"].is_empty():
		fault_tick = float(traj["blocked"][0]["tick"])

	_build_world()
	_build_hud()

	if args.has("shot"):
		await _run_shot(float(args.get("tick", "14")), String(args["shot"]))
	elif args.has("out"):
		await _run_povgen(String(args["out"]), int(args.get("fpt", "5")))


# ---------- 模式 ----------

func _run_shot(t: float, path: String) -> void:
	for k in range(50):  # 静止拍摄也要让 yaw 平滑收敛
		_apply(t)
	for k in range(8):
		await get_tree().process_frame
	get_viewport().get_texture().get_image().save_png(path)
	print("SHOT_OK ", path)
	get_tree().quit()


func _run_povgen(outdir: String, fpt: int) -> void:
	DirAccess.make_dir_recursive_absolute(outdir)
	var total := int((float(traj["max_tick"]) + 2.0) * fpt)
	for fr in range(total):
		var t := float(fr) / float(fpt) - 1.0
		_apply(t)
		await get_tree().process_frame
		await get_tree().process_frame
		get_viewport().get_texture().get_image().save_png(
			"%s/f_%05d.png" % [outdir, fr])
		if fr % 50 == 0:
			print("FRAME ", fr, "/", total)
	print("POVGEN_DONE ", total)
	get_tree().quit()


# ---------- 轨迹 ----------

func _pos_at(t: float) -> Vector2:
	var wps: Array = traj["waypoints"]
	if t <= float(wps[0]["t"]):
		return Vector2(float(wps[0]["x"]), float(wps[0]["y"])) / SCALE
	for k in range(wps.size() - 1):
		var t0 := float(wps[k]["t"])
		var t1 := float(wps[k + 1]["t"])
		if t >= t0 and t <= t1:
			var f: float = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
			var p0 := Vector2(float(wps[k]["x"]), float(wps[k]["y"]))
			var p1 := Vector2(float(wps[k + 1]["x"]), float(wps[k + 1]["y"]))
			return p0.lerp(p1, f) / SCALE
	var last: Dictionary = wps[wps.size() - 1]
	return Vector2(float(last["x"]), float(last["y"])) / SCALE


func _battery_at(t: float) -> float:
	var pts: Array = traj["battery"]
	if pts.is_empty():
		return 100.0
	var v := 100.0
	var t_prev := 0.0
	var v_prev := 100.0
	for p in pts:
		var pt := float(p["tick"])
		var pv := float(p["pct"])
		if pt <= t:
			t_prev = pt
			v_prev = pv
			v = pv
		else:
			if pt > t_prev:
				v = lerp(v_prev, pv, (t - t_prev) / (pt - t_prev))
			break
	return v


func _caption_at(t: float) -> String:
	var txt := "SYSTEM READY"
	for c in traj["captions"]:
		if float(c["tick"]) <= t:
			txt = String(c["text"])
	return txt


# ---------- 每帧状态 ----------

func _apply(t: float) -> void:
	var p := _pos_at(t)
	var dir := _pos_at(t + 0.35) - p
	if dir.length() <= 0.004:
		dir = p - _pos_at(t - 1.2)  # 停滞时保持来向(面向障碍)
	# 感知窗口:视线转向异常物体(位置仍走真值轨迹,只转头)
	var gaze := 0.14
	for pv in traj["perceives"]:
		var pt := float(pv["tick"])
		if t >= pt - 0.4 and t <= pt + 2.6:
			var ab := anomaly_box.global_position
			dir = Vector2(ab.x, ab.z) - p
			gaze = 0.22
	if dir.length() > 0.004:
		_yaw = lerp_angle(_yaw, atan2(-dir.x, -dir.y), gaze)
	var moving := (_pos_at(t + 0.35) - p).length() > 0.004
	var bob := sin(t * 7.0) * 0.018 if moving else 0.0
	cam.position = Vector3(p.x, CAM_H + bob, p.y)
	cam.rotation = Vector3(deg_to_rad(-5.0), _yaw, sin(t * 3.1) * 0.004)

	obstacle.visible = t >= fault_tick

	var bat := _battery_at(t)
	hud_tick.text = "TICK %03d   BATTERY %5.1f%%   RUN %s   TRAJ: GROUND-TRUTH EVENT LOG" % [
		int(max(t, 0.0)), bat, String(traj["run_id"])]
	bat_fg.size.x = 180.0 * clamp(bat / 100.0, 0.0, 1.0)
	bat_fg.color = Color(0.88, 0.35, 0.4) if bat < 20.0 else (
		Color(0.92, 0.72, 0.25) if bat < 40.0 else Color(0.35, 0.78, 0.42))
	var cap := _caption_at(t)
	hud_caption.text = cap
	hud_caption.modulate = Color(1.0, 0.45, 0.45) if (
		cap.begins_with("!!") or cap.begins_with("WATCHDOG")) else Color(0.5, 1.0, 0.9)
	hud_rec.visible = int(t * 2.0) % 2 == 0

	_apply_vlm(t)


func _apply_vlm(t: float) -> void:
	var active := false
	var conf := 0.0
	var label := ""
	for pv in traj["perceives"]:
		var pt := float(pv["tick"])
		if t >= pt - 0.6 and t <= pt + 2.8:
			active = true
			conf = float(pv["conf"])
			label = String(pv["label"])
	if not active or cam.is_position_behind(anomaly_box.global_position):
		vlm_rect.visible = false
		vlm_label.visible = false
		vlm_head.visible = false
		scanline.visible = false
		return
	var c := anomaly_box.global_position
	var mins := Vector2(1e9, 1e9)
	var maxs := Vector2(-1e9, -1e9)
	for dx in [-0.32, 0.32]:
		for dy in [-0.3, 0.34]:
			for dz in [-0.32, 0.32]:
				var s := cam.unproject_position(c + Vector3(dx, dy, dz))
				mins = mins.min(s)
				maxs = maxs.max(s)
	vlm_rect.visible = true
	vlm_rect.position = mins - Vector2(8, 8)
	vlm_rect.size = maxs - mins + Vector2(16, 16)
	vlm_label.visible = true
	vlm_label.position = Vector2(mins.x - 8, mins.y - 40) if mins.y > 60 \
		else Vector2(mins.x - 8, maxs.y + 14)
	vlm_label.text = "%s  conf=%.3f" % [label, conf]
	vlm_head.visible = true
	scanline.visible = true
	scanline.position.y = fmod(t * 260.0, 720.0)


# ---------- 场景搭建 ----------

func _mat(c: Color, emissive := false, energy := 1.0) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = c
	if emissive:
		m.emission_enabled = true
		m.emission = c
		m.emission_energy_multiplier = energy
	return m


func _box(size: Vector3, pos: Vector3, mat: StandardMaterial3D, parent: Node3D = self) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mi.mesh = bm
	mi.material_override = mat
	mi.position = pos
	parent.add_child(mi)
	return mi


func _node_pos(id: String) -> Vector2:
	var l: Array = traj["layout"][id]
	return Vector2(float(l[0]), float(l[1])) / SCALE


func _dist_to_edges(p: Vector2) -> float:
	var best := 1e9
	for e in traj["edges"]:
		var a := _node_pos(String(e[0]))
		var b := _node_pos(String(e[1]))
		var ab := b - a
		var f: float = clamp((p - a).dot(ab) / ab.length_squared(), 0.0, 1.0)
		best = min(best, (p - (a + ab * f)).length())
	return best


func _build_world() -> void:
	# 环境:冷色雾 + 环境光
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.06, 0.075, 0.1)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.55, 0.62, 0.72)
	env.ambient_light_energy = 0.7
	env.fog_enabled = true
	env.fog_light_color = Color(0.08, 0.1, 0.14)
	env.fog_density = 0.028
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)

	var sun := DirectionalLight3D.new()
	sun.rotation = Vector3(deg_to_rad(-55.0), deg_to_rad(35.0), 0.0)
	sun.light_energy = 0.5
	add_child(sun)

	# 地板 22x16m + 顶棚
	_box(Vector3(24, 0.1, 18), Vector3(9.5, -0.05, 8.5), _mat(Color(0.16, 0.17, 0.19)))
	_box(Vector3(24, 0.1, 18), Vector3(9.5, 3.4, 8.5), _mat(Color(0.1, 0.11, 0.13)))
	# 周界墙
	var wall := _mat(Color(0.2, 0.22, 0.26))
	_box(Vector3(24, 3.4, 0.2), Vector3(9.5, 1.7, -0.4), wall)
	_box(Vector3(24, 3.4, 0.2), Vector3(9.5, 1.7, 17.4), wall)
	_box(Vector3(0.2, 3.4, 18), Vector3(-2.4, 1.7, 8.5), wall)
	_box(Vector3(0.2, 3.4, 18), Vector3(21.5, 1.7, 8.5), wall)

	# 顶灯带 + 光源
	for lx in range(1, 20, 5):
		for lz in range(2, 16, 5):
			_box(Vector3(2.4, 0.06, 0.3), Vector3(lx, 3.3, lz),
				_mat(Color(0.9, 0.92, 0.86), true, 1.6))
			var o := OmniLight3D.new()
			o.position = Vector3(lx, 3.0, lz)
			o.light_energy = 1.5
			o.omni_range = 7.5
			o.light_color = Color(1.0, 0.97, 0.9)
			add_child(o)

	# 货架:栅格摆放,避开走廊(距任意边 <1.7m 的不放)
	var rack := _mat(Color(0.42, 0.3, 0.2))
	var rack2 := _mat(Color(0.3, 0.34, 0.4))
	var idx := 0
	for gx in range(0, 21, 3):
		for gz in range(1, 17, 3):
			var p := Vector2(gx, gz)
			if _dist_to_edges(p) < 1.7:
				continue
			idx += 1
			_box(Vector3(2.0, 2.3, 0.8), Vector3(gx, 1.15, gz),
				rack if idx % 2 == 0 else rack2)
			_box(Vector3(0.55, 0.5, 0.55), Vector3(gx - 0.5, 2.55 + 0.25, gz),
				_mat(Color(0.65, 0.5, 0.3)))

	# 走廊地面导引线
	var lane := _mat(Color(0.5, 0.45, 0.2), true, 0.25)
	for e in traj["edges"]:
		var a := _node_pos(String(e[0]))
		var b := _node_pos(String(e[1]))
		var mid := (a + b) / 2.0
		var seg := _box(Vector3(a.distance_to(b), 0.012, 0.12),
			Vector3(mid.x, 0.01, mid.y), lane)
		seg.rotation.y = atan2(-(b - a).y, (b - a).x)

	# 节点标记:地面圆盘 + 悬浮标签
	for id in traj["layout"].keys():
		var p := _node_pos(String(id))
		var acc: String = traj["access"].get(id, "free")
		var disc := MeshInstance3D.new()
		var cm := CylinderMesh.new()
		cm.top_radius = 0.5
		cm.bottom_radius = 0.5
		cm.height = 0.03
		disc.mesh = cm
		disc.material_override = _mat(NODE_COLORS[acc], true, 0.5)
		disc.position = Vector3(p.x, 0.02, p.y)
		add_child(disc)
		var lb := Label3D.new()
		lb.text = String(traj["names"].get(id, id))
		lb.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		lb.font_size = 30
		lb.outline_size = 9
		lb.position = Vector3(p.x, 2.3, p.y)
		lb.modulate = NODE_COLORS[acc].lightened(0.35)
		add_child(lb)

	# dock 充电桩 / 受限区 / 禁入区装饰
	var dp := _node_pos("dock")
	_box(Vector3(0.7, 1.4, 0.5), Vector3(dp.x - 0.9, 0.7, dp.y),
		_mat(Color(0.2, 0.6, 0.35), true, 0.9))
	var rp := _node_pos("r1")
	_box(Vector3(2.4, 0.014, 2.4), Vector3(rp.x, 0.008, rp.y),
		_mat(Color(0.85, 0.7, 0.15, 0.5)))
	var fp := _node_pos("f1")
	_box(Vector3(1.8, 2.6, 0.15), Vector3(fp.x, 1.3, fp.y - 0.7),
		_mat(Color(0.75, 0.2, 0.22), true, 0.4))

	# 异常物体(perceive 的目标):a2 旁的无主纸箱
	var ap := _node_pos(String(traj["anomaly_node"]))
	anomaly_box = _box(Vector3(0.55, 0.5, 0.55), Vector3(ap.x + 0.9, 0.25, ap.y + 0.6),
		_mat(Color(0.72, 0.54, 0.3)))
	_box(Vector3(0.4, 0.32, 0.4), Vector3(ap.x + 1.25, 0.16, ap.y + 0.15),
		_mat(Color(0.6, 0.44, 0.24)))

	# 受阻障碍:箱堆(fault tick 前隐藏)
	obstacle = Node3D.new()
	add_child(obstacle)
	if not traj["blocked"].is_empty():
		var bl: Array = traj["blocked"][0]["pos"]
		var bp := Vector2(float(bl[0]), float(bl[1])) / SCALE
		var cr := _mat(Color(0.55, 0.4, 0.22))
		_box(Vector3(0.8, 0.75, 0.8), Vector3(bp.x - 0.35, 0.38, bp.y), cr, obstacle)
		_box(Vector3(0.75, 0.7, 0.75), Vector3(bp.x + 0.5, 0.35, bp.y + 0.2), cr, obstacle)
		_box(Vector3(0.7, 0.65, 0.7), Vector3(bp.x + 0.05, 1.05, bp.y + 0.1),
			_mat(Color(0.62, 0.46, 0.26)), obstacle)
		var warn := Label3D.new()
		warn.text = "OBSTACLE"
		warn.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		warn.font_size = 26
		warn.outline_size = 8
		warn.modulate = Color(1.0, 0.5, 0.45)
		warn.position = Vector3(bp.x, 1.55, bp.y)
		obstacle.add_child(warn)
	obstacle.visible = false

	cam = Camera3D.new()
	cam.fov = 72.0
	add_child(cam)


func _build_hud() -> void:
	var cl := CanvasLayer.new()
	add_child(cl)

	hud_tick = Label.new()
	hud_tick.position = Vector2(18, 12)
	hud_tick.add_theme_font_size_override("font_size", 19)
	hud_tick.add_theme_color_override("font_outline_color", Color.BLACK)
	hud_tick.add_theme_constant_override("outline_size", 7)
	cl.add_child(hud_tick)

	var bat_bg := ColorRect.new()
	bat_bg.position = Vector2(18, 44)
	bat_bg.size = Vector2(180, 12)
	bat_bg.color = Color(0.1, 0.12, 0.15, 0.85)
	cl.add_child(bat_bg)
	bat_fg = ColorRect.new()
	bat_fg.position = Vector2(18, 44)
	bat_fg.size = Vector2(180, 12)
	cl.add_child(bat_fg)

	hud_caption = Label.new()
	hud_caption.position = Vector2(0, 74)
	hud_caption.size = Vector2(1280, 34)
	hud_caption.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	hud_caption.add_theme_font_size_override("font_size", 26)
	hud_caption.add_theme_color_override("font_outline_color", Color.BLACK)
	hud_caption.add_theme_constant_override("outline_size", 9)
	cl.add_child(hud_caption)

	hud_rec = Label.new()
	hud_rec.text = "* REC  POV-CAM 01"
	hud_rec.position = Vector2(1080, 12)
	hud_rec.add_theme_font_size_override("font_size", 19)
	hud_rec.add_theme_color_override("font_color", Color(1.0, 0.4, 0.4))
	hud_rec.add_theme_color_override("font_outline_color", Color.BLACK)
	hud_rec.add_theme_constant_override("outline_size", 7)
	cl.add_child(hud_rec)

	vlm_rect = Panel.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.2, 1.0, 0.6, 0.07)
	sb.border_color = Color(0.25, 1.0, 0.6)
	sb.set_border_width_all(3)
	vlm_rect.add_theme_stylebox_override("panel", sb)
	vlm_rect.visible = false
	cl.add_child(vlm_rect)

	vlm_label = Label.new()
	vlm_label.add_theme_font_size_override("font_size", 24)
	vlm_label.add_theme_color_override("font_color", Color(0.35, 1.0, 0.65))
	vlm_label.add_theme_color_override("font_outline_color", Color.BLACK)
	vlm_label.add_theme_constant_override("outline_size", 8)
	vlm_label.visible = false
	cl.add_child(vlm_label)

	vlm_head = Label.new()
	vlm_head.text = "PERCEIVE -> structured observation only (never actions)"
	vlm_head.position = Vector2(0, 640)
	vlm_head.size = Vector2(1280, 30)
	vlm_head.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	vlm_head.add_theme_font_size_override("font_size", 22)
	vlm_head.add_theme_color_override("font_color", Color(0.35, 1.0, 0.65))
	vlm_head.add_theme_color_override("font_outline_color", Color.BLACK)
	vlm_head.add_theme_constant_override("outline_size", 8)
	vlm_head.visible = false
	cl.add_child(vlm_head)

	scanline = ColorRect.new()
	scanline.size = Vector2(1280, 2)
	scanline.color = Color(0.25, 1.0, 0.6, 0.25)
	scanline.visible = false
	cl.add_child(scanline)
