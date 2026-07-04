#!/bin/bash
# Day-4 真实集成 POV 渲染。MODE=shot 出单帧(de-risk);MODE=frames 出整段帧序列。
# 用脚本文件跑,避开 PowerShell→docker 的 -s "-screen ..." 引号被吞。
set -e
cd /games/povgen
TRAJ=/games/povgen/traj_day4.json
MODE=${MODE:-shot}
if [[ "$MODE" == shot ]]; then
  TICK=${TICK:-40}
  xvfb-run -a -s "-screen 0 1280x720x24" godot --path /games/povgen -- \
    --traj "$TRAJ" --shot /games/povgen/_day4_s.png --tick "$TICK" 2>&1 \
    | grep -iE "POVGEN|SHOT|SCRIPT ERROR|signal 11|error" | head -12
  ls -la /games/povgen/_day4_s.png
else
  rm -rf /games/povgen/frames_day4
  xvfb-run -a -s "-screen 0 1280x720x24" godot --path /games/povgen -- \
    --traj "$TRAJ" --out /games/povgen/frames_day4 --fpt "${FPT:-3}" 2>&1 \
    | grep -iE "POVGEN_DONE|SCRIPT ERROR|signal 11" | tail -5
  echo "frames: $(ls /games/povgen/frames_day4 | wc -l)"
fi
