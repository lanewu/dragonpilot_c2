#!/usr/bin/env python3
# Process replay regression test using a LOCAL baseline.
#
# Workflow:
#   1. Generate the baseline on a known-good commit (full test only):
#        python selfdrive/test/process_replay/test_processes.py --update-baseline
#   2. Later, after changes, compare the current code against that baseline:
#        python selfdrive/test/process_replay/test_processes.py
#
# The baseline lives in fakedata/baseline/ and holds exactly one version, named
# {segment}_{proc}_{HEAD hash}.bz2. Nothing is uploaded and no ref_commit is used.
import argparse
import concurrent.futures
import os
import shutil
import sys
from collections import defaultdict
from tqdm import tqdm
from typing import Any, DefaultDict, Dict, List, Optional, Tuple

from openpilot.selfdrive.car.car_helpers import interface_names
from openpilot.selfdrive.test.dp_default_params import seed_default_params
from openpilot.selfdrive.test.openpilotci import get_url
from openpilot.selfdrive.test.process_replay.compare_logs import compare_logs
from openpilot.selfdrive.test.process_replay.process_replay import CONFIGS, PROC_REPLAY_DIR, FAKEDATA, check_openpilot_enabled, replay_process
from openpilot.system.version import get_commit
from openpilot.tools.lib.filereader import FileReader
from openpilot.tools.lib.logreader import LogReader
from openpilot.tools.lib.helpers import save_log

# Source routes that the generated segments below were produced from (provenance only).
source_segments = [
  ("BODY", "937ccb7243511b65|2022-05-24--16-03-09--1"),        # COMMA.BODY
  ("HYUNDAI", "02c45f73a2e5c6e9|2021-01-01--19-08-22--1"),     # HYUNDAI.SONATA
  ("HYUNDAI2", "d545129f3ca90f28|2022-11-07--20-43-08--3"),    # HYUNDAI.KIA_EV6 (+ QCOM GPS)
  ("TOYOTA", "0982d79ebb0de295|2021-01-04--17-13-21--13"),     # TOYOTA.PRIUS (INDI)
  ("TOYOTA2", "0982d79ebb0de295|2021-01-03--20-03-36--6"),     # TOYOTA.RAV4  (LQR)
  ("TOYOTA3", "f7d7e3538cda1a2a|2021-08-16--08-55-34--6"),     # TOYOTA.COROLLA_TSS2
  ("HONDA", "eb140f119469d9ab|2021-06-12--10-46-24--27"),      # HONDA.CIVIC (NIDEC)
  ("HONDA2", "7d2244f34d1bbcda|2021-06-25--12-25-37--26"),     # HONDA.ACCORD (BOSCH)
  ("CHRYSLER", "4deb27de11bee626|2021-02-20--11-28-55--8"),    # CHRYSLER.PACIFICA_2018_HYBRID
  ("RAM", "17fc16d840fe9d21|2023-04-26--13-28-44--5"),         # CHRYSLER.RAM_1500
  ("SUBARU", "341dccd5359e3c97|2022-09-12--10-35-33--3"),      # SUBARU.OUTBACK
  ("GM", "0c58b6a25109da2b|2021-02-23--16-35-50--11"),         # GM.VOLT
  ("GM2", "376bf99325883932|2022-10-27--13-41-22--1"),         # GM.BOLT_EUV
  ("NISSAN", "35336926920f3571|2021-02-12--18-38-48--46"),     # NISSAN.XTRAIL
  ("VOLKSWAGEN", "de9592456ad7d144|2021-06-29--11-00-15--6"),  # VOLKSWAGEN.GOLF
  ("MAZDA", "bd6a637565e91581|2021-10-30--15-14-53--4"),       # MAZDA.CX9_2021
  ("FORD", "54827bf84c38b14f|2023-01-26--21-59-07--4"),        # FORD.BRONCO_SPORT_MK1

  # Enable when port is tested and dashcamOnly is no longer set
  #("TESLA", "bb50caf5f0945ab1|2021-06-19--17-20-18--3"),      # TESLA.AP2_MODELS
  #("VOLKSWAGEN2", "3cfdec54aa035f3f|2022-07-19--23-45-10--2"),  # VOLKSWAGEN.PASSAT_NMS
]

# Generated input segments (downloaded read-only from openpilotci).
segments = [
  ("BODY", "aregenECF15D9E559|2023-05-10--14-26-40--0"),
  ("HYUNDAI", "aregenAB9F543F70A|2023-05-10--14-28-25--0"),
  ("HYUNDAI2", "aregen39F5A028F96|2023-05-10--14-31-00--0"),
  ("TOYOTA", "aregen8D6A8B36E8D|2023-05-10--14-32-38--0"),
  ("TOYOTA2", "aregenB1933C49809|2023-05-10--14-34-14--0"),
  ("TOYOTA3", "aregen5D9915223DC|2023-05-10--14-36-43--0"),
  ("HONDA", "aregen484B732B675|2023-05-10--14-38-23--0"),
  ("HONDA2", "aregenAF6ACED4713|2023-05-10--14-40-01--0"),
  ("CHRYSLER", "aregen99B094E1E2E|2023-05-10--14-41-40--0"),
  ("RAM", "aregen5C2487E1EEB|2023-05-10--14-44-09--0"),
  ("SUBARU", "aregen98D277B792E|2023-05-10--14-46-46--0"),
  ("GM", "aregen377BA28D848|2023-05-10--14-48-28--0"),
  ("GM2", "aregen7CA0CC0F0C2|2023-05-10--14-51-00--0"),
  ("NISSAN", "aregen7097BF01563|2023-05-10--14-52-43--0"),
  ("VOLKSWAGEN", "aregen765AF3D2CB5|2023-05-10--14-54-23--0"),
  ("MAZDA", "aregen3053762FF2E|2023-05-10--14-56-53--0"),
  ("FORD", "aregenDDE0F89FA1E|2023-05-10--14-59-26--0"),
  ]

# dashcamOnly makes don't need to be tested until a full port is done
excluded_interfaces = ["mock", "mazda", "tesla"]

BASELINE_DIR = os.path.join(FAKEDATA, "baseline")
CURRENT_DIR = os.path.join(FAKEDATA, "current")
EXCLUDED_PROCS = {"modeld", "dmonitoringmodeld"}


def run_test_process(data):
  segment, cfg, args, cur_log_fn, ref_log_path, lr_dat = data
  lr = LogReader.from_bytes(lr_dat)
  res, log_msgs = test_process(cfg, lr, segment, ref_log_path, cur_log_fn, args.ignore_fields, args.ignore_msgs)
  save_log(cur_log_fn, log_msgs)
  return (segment, cfg.proc_name, res)


def get_log_data(segment):
  r, n = segment.rsplit("--", 1)
  with FileReader(get_url(r, n)) as f:
    return (segment, f.read())


def test_process(cfg, lr, segment, ref_log_path, new_log_path, ignore_fields=None, ignore_msgs=None):
  if ignore_fields is None:
    ignore_fields = []
  if ignore_msgs is None:
    ignore_msgs = []

  try:
    log_msgs = replay_process(cfg, lr, disable_progress=True)
  except Exception as e:
    raise Exception("failed on segment: " + segment) from e

  # check to make sure openpilot is engaged in the route
  if cfg.proc_name == "controlsd":
    if not check_openpilot_enabled(log_msgs):
      return f"Route did not enable at all or for long enough: {new_log_path}", log_msgs

  # baseline generation: nothing to compare against
  if ref_log_path is None:
    return [], log_msgs

  ref_log_msgs = list(LogReader(ref_log_path))
  try:
    return compare_logs(ref_log_msgs, log_msgs, ignore_fields + cfg.ignore, ignore_msgs, cfg.tolerance), log_msgs
  except Exception as e:
    return str(e), log_msgs


def get_baseline_commit() -> Optional[str]:
  # The baseline must contain exactly one version; return its commit hash or None if absent.
  if not os.path.isdir(BASELINE_DIR):
    return None
  commits = {fn[:-len(".bz2")].rsplit("_", 1)[1] for fn in os.listdir(BASELINE_DIR) if fn.endswith(".bz2")}
  if len(commits) > 1:
    raise Exception(f"Multiple baseline versions found in {BASELINE_DIR}: {sorted(commits)}. "
                    "Regenerate with --update-baseline to keep a single version.")
  return commits.pop() if commits else None


def replace_baseline() -> None:
  # Swap in the freshly generated outputs as the only baseline version.
  shutil.rmtree(BASELINE_DIR, ignore_errors=True)
  os.makedirs(BASELINE_DIR)
  for fn in os.listdir(CURRENT_DIR):
    shutil.move(os.path.join(CURRENT_DIR, fn), os.path.join(BASELINE_DIR, fn))


def format_diff(results, log_paths, label):
  diff1, diff2 = "", ""
  diff2 += f"***** {label} *****\n"

  failed = False
  for segment, result in list(results.items()):
    diff1 += f"***** results for segment {segment} *****\n"
    diff2 += f"***** differences for segment {segment} *****\n"

    for proc, diff in list(result.items()):
      # long diff
      diff2 += f"*** process: {proc} ***\n"
      diff2 += f"\tref: {log_paths[segment][proc]['ref']}\n"
      diff2 += f"\tnew: {log_paths[segment][proc]['new']}\n\n"

      # short diff
      diff1 += f"    {proc}\n"
      if isinstance(diff, str):
        diff1 += f"        ref: {log_paths[segment][proc]['ref']}\n"
        diff1 += f"        new: {log_paths[segment][proc]['new']}\n\n"
        diff1 += f"        {diff}\n"
        failed = True
      elif len(diff):
        diff1 += f"        ref: {log_paths[segment][proc]['ref']}\n"
        diff1 += f"        new: {log_paths[segment][proc]['new']}\n\n"

        cnt: Dict[str, int] = {}
        for d in diff:
          diff2 += f"\t{str(d)}\n"

          k = str(d[1])
          cnt[k] = 1 if k not in cnt else cnt[k] + 1

        for k, v in sorted(cnt.items()):
          diff1 += f"        {k}: {v}\n"
        failed = True
  return diff1, diff2, failed


if __name__ == "__main__":
  all_cars = {car for car, _ in segments}
  all_procs = {cfg.proc_name for cfg in CONFIGS if cfg.proc_name not in EXCLUDED_PROCS}

  parser = argparse.ArgumentParser(description="Regression test to identify changes in a process's output")
  parser.add_argument("--whitelist-procs", type=str, nargs="*", default=all_procs,
                      help="Whitelist given processes from the test (e.g. controlsd)")
  parser.add_argument("--whitelist-cars", type=str, nargs="*", default=all_cars,
                      help="Whitelist given cars from the test (e.g. HONDA)")
  parser.add_argument("--blacklist-procs", type=str, nargs="*", default=[],
                      help="Blacklist given processes from the test (e.g. controlsd)")
  parser.add_argument("--blacklist-cars", type=str, nargs="*", default=[],
                      help="Blacklist given cars from the test (e.g. HONDA)")
  parser.add_argument("--ignore-fields", type=str, nargs="*", default=[],
                      help="Extra fields or msgs to ignore (e.g. carState.events)")
  parser.add_argument("--ignore-msgs", type=str, nargs="*", default=[],
                      help="Msgs to ignore (e.g. carEvents)")
  parser.add_argument("--update-baseline", action="store_true",
                      help="Regenerate the local baseline from the current commit (requires full test)")
  parser.add_argument("-j", "--jobs", type=int, default=1)
  args = parser.parse_args()

  seed_default_params()

  tested_procs = set(args.whitelist_procs) - set(args.blacklist_procs)
  tested_cars = set(args.whitelist_cars) - set(args.blacklist_cars)
  tested_cars = {c.upper() for c in tested_cars}

  full_test = (tested_procs == all_procs) and (tested_cars == all_cars) and all(len(x) == 0 for x in (args.ignore_fields, args.ignore_msgs))

  if args.update_baseline:
    assert full_test, "Need to run full test when updating the local baseline"

  cur_commit = get_commit()
  if cur_commit is None:
    raise Exception("Couldn't get current commit")

  baseline_commit: Optional[str] = None
  if not args.update_baseline:
    baseline_commit = get_baseline_commit()
    if baseline_commit is None:
      print(f"No local baseline found in {BASELINE_DIR}. Generate it first with --update-baseline.")
      sys.exit(1)
    print(f"***** testing against local baseline commit {baseline_commit} *****")

  # outputs of this run; cleared every run so only the current results remain
  shutil.rmtree(CURRENT_DIR, ignore_errors=True)
  os.makedirs(CURRENT_DIR)

  # check to make sure all car brands are tested
  if full_test:
    untested = (set(interface_names) - set(excluded_interfaces)) - {c.lower() for c in tested_cars}
    assert len(untested) == 0, f"Cars missing routes: {str(untested)}"

  log_paths: DefaultDict[str, Dict[str, Dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
  with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
    download_segments = [seg for car, seg in segments if car in tested_cars]
    log_data: Dict[str, bytes] = {}
    p1 = pool.map(get_log_data, download_segments)
    for segment, lr in tqdm(p1, desc="Getting Logs", total=len(download_segments)):
      log_data[segment] = lr

    pool_args: Any = []
    missing: List[Tuple[str, str]] = []
    for car_brand, segment in segments:
      if car_brand not in tested_cars:
        continue

      for cfg in CONFIGS:
        if cfg.proc_name not in tested_procs:
          continue

        cur_log_fn = os.path.join(CURRENT_DIR, f"{segment}_{cfg.proc_name}_{cur_commit}.bz2")
        if args.update_baseline:
          ref_log_path = None
        else:
          ref_log_path = os.path.join(BASELINE_DIR, f"{segment}_{cfg.proc_name}_{baseline_commit}.bz2")
          if not os.path.exists(ref_log_path):
            missing.append((segment, cfg.proc_name))

        pool_args.append((segment, cfg, args, cur_log_fn, ref_log_path, log_data[segment]))

        log_paths[segment][cfg.proc_name]['ref'] = ref_log_path if ref_log_path else "n/a (baseline generation)"
        log_paths[segment][cfg.proc_name]['new'] = cur_log_fn

    if missing:
      print(f"Missing {len(missing)} baseline file(s) for commit {baseline_commit}, e.g. {missing[0]}")
      print("Regenerate them with --update-baseline (full test).")
      sys.exit(1)

    results: Any = defaultdict(dict)
    p2 = pool.map(run_test_process, pool_args)
    for (segment, proc, result) in tqdm(p2, desc="Running Tests", total=len(pool_args)):
      results[segment][proc] = result

  if args.update_baseline:
    diff1, _, failed = format_diff(results, log_paths, f"baseline generation at commit {cur_commit}")
    print(diff1)
    if failed:
      print("BASELINE NOT UPDATED: some processes failed to generate output")
      sys.exit(1)
    replace_baseline()
    print(f"Updated local baseline for commit: {cur_commit} in {BASELINE_DIR}")
    sys.exit(0)

  diff1, diff2, failed = format_diff(results, log_paths, f"tested against local baseline commit {baseline_commit}")
  with open(os.path.join(PROC_REPLAY_DIR, "diff.txt"), "w") as f:
    f.write(diff2)
  print(diff1)

  if failed:
    print("TEST FAILED")
  else:
    print("TEST SUCCEEDED")

  sys.exit(int(failed))
