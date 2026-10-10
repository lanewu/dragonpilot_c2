#!/usr/bin/env python3
# Compare decoded Process Replay outputs in NEW_DIR against BASE_DIR (local baseline).
# Files are matched by {segment}_{proc}; the commit hash in the file name may differ.
# Usage: python selfdrive/test/process_replay/compare_local_baseline.py BASE_DIR NEW_DIR
import os
import sys

from openpilot.selfdrive.test.process_replay.compare_logs import compare_logs
from openpilot.selfdrive.test.process_replay.process_replay import CONFIGS
from openpilot.tools.lib.logreader import LogReader

PROCS = {cfg.proc_name: cfg for cfg in CONFIGS}


def index(directory):
  # key: "{segment}_{proc}", value: full path
  return {fn[:-len(".bz2")].rsplit("_", 1)[0]: os.path.join(directory, fn)
          for fn in os.listdir(directory) if fn.endswith(".bz2")}


def main(base_dir, new_dir):
  base, new = index(base_dir), index(new_dir)
  failed = 0
  for key in sorted(base):
    seg, proc = key.rsplit("_", 1)
    if key not in new:
      print(f"MISSING {seg} {proc}")
      failed += 1
      continue
    cfg = PROCS[proc]
    try:
      diffs = compare_logs(list(LogReader(base[key])), list(LogReader(new[key])), cfg.ignore, [], cfg.tolerance)
    except Exception as e:
      diffs = str(e)
    if isinstance(diffs, str):
      print(f"ERROR   {seg} {proc}: {diffs}")
      failed += 1
    elif len(diffs):
      keys = sorted({str(d[1]) for d in diffs})
      print(f"DIFF    {seg} {proc}: {len(diffs)} diffs, keys={keys[:8]}")
      failed += 1
    else:
      print(f"SAME    {seg} {proc}")
  print(f"compared {len(names)} files, not-identical {failed}")
  return failed


if __name__ == "__main__":
  sys.exit(int(main(sys.argv[1], sys.argv[2]) > 0))
