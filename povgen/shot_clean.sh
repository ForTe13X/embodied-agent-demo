#!/bin/bash
cd /games/povgen
xvfb-run -a -s "-screen 0 1280x720x24" godot --path . -- \
  --traj /games/povgen/traj_blocked.json --nohud 1 \
  --vantage /games/povgen/_clean_vantage.png 2>&1 \
  | grep -E "SHOT_OK|SCRIPT ERROR|signal 11"
