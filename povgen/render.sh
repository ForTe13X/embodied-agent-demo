#!/bin/bash
set -e
rm -rf /games/povgen/frames
xvfb-run -a -s "-screen 0 1280x720x24" godot --path /games/povgen -- \
  --traj /games/povgen/traj.json --out /games/povgen/frames --fpt 5 2>&1 \
  | grep -E "POVGEN_DONE|FRAME|SCRIPT ERROR|signal 11"
