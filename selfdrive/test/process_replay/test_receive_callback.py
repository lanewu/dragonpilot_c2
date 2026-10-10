#!/usr/bin/env python3
import unittest
from types import SimpleNamespace

from openpilot.selfdrive.test.process_replay.process_replay import FrequencyBasedRcvCallback


class TriggerMessage:
  def which(self):
    return "driverStateV2"


class TestFrequencyBasedRcvCallback(unittest.TestCase):
  def test_skipped_input_frames_still_advance_frequency(self):
    callback = FrequencyBasedRcvCallback("driverStateV2")
    message = TriggerMessage()
    config = SimpleNamespace(subs=["driverMonitoringState"])
    replay_frame = 0
    results = []

    for _ in range(8):
      should_recv = callback(message, config, replay_frame)
      results.append(should_recv)
      if should_recv:
        replay_frame += 1

    self.assertEqual(results, [True, False, True, False, True, False, True, False])


if __name__ == "__main__":
  unittest.main()
