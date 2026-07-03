#!/bin/bash
cd /games/povgen
for t in 14 42.8 30; do
  xvfb-run -a -s "-screen 0 1280x720x24" godot --path . -- \
    --traj /games/povgen/traj.json --shot "/games/povgen/_s$t.png" --tick "$t" 2>&1 \
    | grep -E "SHOT_OK|SCRIPT ERROR|signal 11"
done
