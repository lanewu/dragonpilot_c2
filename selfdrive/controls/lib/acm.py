# MIT Non-Commercial License
#
# Copyright (c) 2019, dragonpilot
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, for non-commercial purposes only, subject to the following conditions:
#
# - The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
# - Commercial use (e.g., use in a product, service, or activity intended to generate revenue) is prohibited without explicit written permission from dragonpilot. Contact ricklan@gmail.com for inquiries.
# - Any project that uses the Software must visibly mention the following acknowledgment: "This project uses software from dragonpilot and is licensed under a custom license requiring permission for use."
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# Adaptive Coasting Mode (ACM) allows the vehicle to coast instead of braking mildly when no lead is close.
# Logic ported from dragonpilot's ACM (dragonpilot/selfdrive/controls/lib/acm.py, commit 94ad85cf).
# The original is Copyright (c) 2025, Rick Lan, under the same non-commercial terms as the notice above.
import time
import numpy as np
from openpilot.common.swaglog import cloudlog

# Must be within 2% over cruise speed
SPEED_RATIO = 0.98
# Disable ACM when lead time-to-collision (TTC) is below this (seconds)
TTC_THRESHOLD = 3.0

# Emergency thresholds - immediately disable ACM
EMERGENCY_TTC = 2.0  # seconds
EMERGENCY_RELATIVE_SPEED = 10.0  # m/s closing speed
EMERGENCY_DECEL_THRESHOLD = -1.5  # m/s^2, MPC braking request that aborts ACM

# Brief cooldown after lead detection to handle sensor glitches
LEAD_COOLDOWN_TIME = 0.5  # seconds

# Speed-based minimum lead distance for emergency checks
SPEED_BP = [0., 10., 20., 30.]  # m/s
MIN_DIST_V = [15., 20., 25., 30.]  # m

# Braking weaker than this (but still negative) is suppressed to 0
MILD_BRAKE_LIMIT = -1.0  # m/s^2

# Downhill when sin(pitch) is below this; assumes nose-up positive pitch in orientationNED
DOWNHILL_SIN_PITCH = -0.04
# Downhill coasting speed target is capped at cruise plus this margin
MAX_SPEED_OVER_CRUISE = 15.0 / 3.6  # m/s (15 km/h)

class ACM:
  def __init__(self):
    self.enabled = False  # set by the planner from the dp_lon_acm parameter
    self.active = False
    self.just_disabled = False  # True for one cycle when ACM turns off
    self.is_downhill = False
    self.speed_cap = float('inf')  # m/s
    self.lead_ttc = float('inf')

    self._is_speed_over_cruise = False
    self._has_lead = False
    self._active_prev = False
    self._last_lead_time = -float('inf')  # monotonic time of the last close lead

    # Suppression episode tracking, for logging only (frames where braking is actually zeroed)
    self._suppressing = False
    self._suppress_start = 0.0
    self._suppress_last = 0.0
    self._suppress_frames = 0

  def _check_emergency_conditions(self, lead, v_ego, current_time):
    """Return True if a close, fast-closing lead requires an immediate ACM disable."""
    if not lead or not lead.status:
      return False

    self.lead_ttc = lead.dRel / max(v_ego, 0.1)
    relative_speed = v_ego - lead.vLead  # positive = closing

    min_dist_for_speed = np.interp(v_ego, SPEED_BP, MIN_DIST_V)

    # Require BOTH close distance AND (fast closing OR very short TTC)
    if lead.dRel < min_dist_for_speed and (self.lead_ttc < EMERGENCY_TTC or relative_speed > EMERGENCY_RELATIVE_SPEED):
      self._last_lead_time = current_time
      if self._active_prev:
        cloudlog.warning(f"ACM emergency disable: dRel={lead.dRel:.1f}m, TTC={self.lead_ttc:.1f}s, relSpeed={relative_speed:.1f}m/s")
      return True

    return False
  def _update_lead_status(self, lead, v_ego, current_time):
    """Update lead vehicle detection status."""
    if lead and lead.status:
      self.lead_ttc = lead.dRel / max(v_ego, 0.1)
      if self.lead_ttc < TTC_THRESHOLD:
        self._has_lead = True
        self._last_lead_time = current_time
      else:
        self._has_lead = False
    else:
      self._has_lead = False
      self.lead_ttc = float('inf')

  def _check_cooldown(self, current_time):
    """Return True while still in the cooldown period after lead detection."""
    return current_time - self._last_lead_time < LEAD_COOLDOWN_TIME

  def _should_activate(self, user_ctrl_lon, v_ego, v_cruise, in_cooldown):
    """Return True if all activation conditions hold."""
    self._is_speed_over_cruise = v_ego > (v_cruise * SPEED_RATIO)

    return (not user_ctrl_lon and
            not self._has_lead and
            not in_cooldown and
            self._is_speed_over_cruise)

  def update_states(self, cc, rs, user_ctrl_lon, v_ego, v_cruise):
    """Update ACM state. Must be called every planner cycle."""
    active_prev = self._active_prev
    self.active = False
    self.is_downhill = False
    self.speed_cap = v_cruise + MAX_SPEED_OVER_CRUISE

    # Disabled, or incomplete orientation data: stay inactive
    if self.enabled and len(cc.orientationNED) == 3:
      current_time = time.monotonic()
      lead = rs.leadOne
      self.is_downhill = np.sin(cc.orientationNED[1]) < DOWNHILL_SIN_PITCH

      # Emergency conditions take priority over everything else
      if not self._check_emergency_conditions(lead, v_ego, current_time):
        self._update_lead_status(lead, v_ego, current_time)
        in_cooldown = self._check_cooldown(current_time)
        self.active = self._should_activate(user_ctrl_lon, v_ego, v_cruise, in_cooldown)

    # Compute transitions on every path so just_disabled is never stale
    self.just_disabled = active_prev and not self.active
    if self.active and not active_prev:
      cloudlog.info(f"ACM activated: v_ego={v_ego*3.6:.1f} km/h, v_cruise={v_cruise*3.6:.1f} km/h")
    elif self.just_disabled:
      cloudlog.info("ACM deactivated")
      self._end_suppression()

    self._active_prev = self.active

  def _end_suppression(self):
    """Log the end of an open suppression episode, if any."""
    if self._suppressing:
      duration = self._suppress_last - self._suppress_start
      cloudlog.info(f"ACM suppress end: frames={self._suppress_frames}, duration={duration:.2f} s")
      self._suppressing = False

  def update_a_desired_trajectory(self, a_desired_trajectory):
    """Suppress mild braking in the acceleration trajectory while ACM is active.

    SAFETY: a strong braking request aborts ACM and the trajectory is returned unmodified.
    """
    if not self.active:
      return a_desired_trajectory

    min_accel = np.min(a_desired_trajectory)
    if min_accel < EMERGENCY_DECEL_THRESHOLD:
      cloudlog.warning(f"ACM aborting: MPC requested {min_accel:.2f} m/s^2 braking")
      self.active = False
      return a_desired_trajectory

    # Coasting: zero mild braking, or all braking downhill so gravity can add speed
    modified_trajectory = np.copy(a_desired_trajectory)
    brake_limit = -np.inf if self.is_downhill else MILD_BRAKE_LIMIT
    braking = (modified_trajectory > brake_limit) & (modified_trajectory < 0.0)

    # Log suppression episodes: frames where braking is actually zeroed
    if np.any(braking):
      now = time.monotonic()
      if not self._suppressing:
        self._suppressing = True
        self._suppress_start = now
        self._suppress_frames = 0
        cloudlog.info(f"ACM suppress begin: min suppressed accel={np.min(modified_trajectory[braking]):.2f} m/s^2")
      self._suppress_frames += 1
      self._suppress_last = now
    else:
      self._end_suppression()

    modified_trajectory[braking] = 0.0
    return modified_trajectory

  def update_speed_trajectory(self, speeds, v_ego):
    # Otherwise LongControl's PID sees a negative speed error and brakes anyway
    if not self.active or not self.is_downhill:
      return speeds
    return np.maximum(speeds, np.minimum(v_ego, self.speed_cap))
