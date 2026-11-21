"""Derive the initial steering estimate from causal ego measurements."""
from hashlib import sha256
import json
import math


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def derive(path, row):
    ts = row["timestamp_us"]
    history = [s for s in read(path) if ts - 250000 <= s["utime"] <= ts]
    if not history or history[-1]["utime"] != row["can_pose_timestamp_us"]:
        raise ValueError("Missing last causal pose or frame timestamp mismatch")
    speed_square = sum(s["vel"][0] ** 2 for s in history)
    if speed_square < .01:
        raise ValueError("Insufficient motion for bicycle-equivalent angle")
    estimate = math.atan(2 * sum(s["vel"][0] * s["rotation_rate"][2] for s in history) / speed_square)
    return estimate, history
