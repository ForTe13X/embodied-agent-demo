#!/bin/bash
# 渲染多条 POV:traj_<name>.json -> frames_<name>/(统一 fpt=5,与 viewer 同步映射一致)
set -e
cd /games/povgen
for name in "$@"; do
  rm -rf "frames_$name"
  xvfb-run -a -s "-screen 0 1280x720x24" godot --path . -- \
    --traj "/games/povgen/traj_$name.json" --out "/games/povgen/frames_$name" --fpt 5 2>&1 \
    | grep -E "POVGEN_DONE|SCRIPT ERROR|signal 11"
  echo "== $name done =="
done
