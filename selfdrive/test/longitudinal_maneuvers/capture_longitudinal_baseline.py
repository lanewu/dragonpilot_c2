#!/usr/bin/env python3
# Capture or compare planner-only longitudinal maneuver logs (LongitudinalControl parameter grid).
# Local self-generated baseline only; no reference is fetched or uploaded.
# Usage:
#   python selfdrive/test/longitudinal_maneuvers/capture_longitudinal_baseline.py capture OUT.npz
#   python selfdrive/test/longitudinal_maneuvers/capture_longitudinal_baseline.py compare BASE.npz NEW.npz
# Run from the openpilot root (the directory that contains common/, selfdrive/, cereal/).
import itertools
import os
import sys

os.environ['SIMULATION'] = "1"
os.environ['SKIP_FW_QUERY'] = "1"
os.environ['NO_CAN_TIMEOUT'] = "1"

import numpy as np

from openpilot.common.params import Params
from openpilot.selfdrive.test.longitudinal_maneuvers.test_longitudinal import create_maneuvers


def setup_params():
  params = Params()
  params.clear_all()
  params.put_bool("Passive", bool(os.getenv("PASSIVE")))
  params.put_bool("OpenpilotEnabledToggle", True)


def capture(out_path):
  setup_params()
  keys, valid, logs = [], [], {}
  for e2e, force_decel in itertools.product([True, False], repeat=2):
    for m in create_maneuvers({"e2e": e2e, "force_decel": force_decel}):
      ok, log = m.evaluate()
      keys.append(f"e2e={e2e}|force_decel={force_decel}|{m.title}")
      valid.append(ok)
      logs[f"log_{len(keys) - 1}"] = log
  np.savez(out_path, keys=np.array(keys), valid=np.array(valid), **logs)
  print(f"captured {len(keys)} maneuvers, valid {sum(valid)}")


def compare(base_path, new_path):
  base, new = np.load(base_path), np.load(new_path)
  if list(base["keys"]) != list(new["keys"]):
    print("KEY MISMATCH")
    return 1
  bad = 0
  for i, key in enumerate(base["keys"]):
    b, n = base[f"log_{i}"], new[f"log_{i}"]
    same_valid = bool(base["valid"][i]) == bool(new["valid"][i])
    if b.shape != n.shape:
      print(f"SHAPE   {key}: {b.shape} vs {n.shape}")
      bad += 1
      continue
    max_abs = float(np.max(np.abs(b - n))) if b.size else 0.0
    if not same_valid or max_abs > 0.0:
      print(f"DIFF    {key}: valid {bool(base['valid'][i])}->{bool(new['valid'][i])}, max_abs={max_abs:.3e}")
      bad += 1
  print(f"compared {len(base['keys'])} maneuvers, not-identical {bad}, valid new {int(new['valid'].sum())}")
  return bad


if __name__ == "__main__":
  if sys.argv[1] == "capture":
    capture(sys.argv[2])
  elif sys.argv[1] == "compare":
    sys.exit(int(compare(sys.argv[2], sys.argv[3]) > 0))
