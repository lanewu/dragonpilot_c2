#!/usr/bin/env python3
# Read-only access to the openpilotci blob storage (input route segments only).
# Uploading and reference-log handling are intentionally not part of this repo's port.

BASE_URL = "https://commadataci.blob.core.windows.net/openpilotci/"


def get_url(route_name, segment_num, log_type="rlog"):
  ext = "hevc" if log_type.endswith('camera') else "bz2"
  return BASE_URL + f"{route_name.replace('|', '/')}/{segment_num}/{log_type}.{ext}"
