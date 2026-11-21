"""Pinned native QP module, region dimensions and numeric parsing for oriented guidance."""
import hashlib
import math
from pathlib import Path

import numpy as np


PROJECT = Path(__file__).resolve().parents[2]
NATIVE = "projects.DifferentiableLQR.QPControl_HARD_corner_12N_learned_weights"
SOURCE_SHA = "3f60a2b31ddd7375db96e8d3268d6e9f6fbf67ed587fefc9928c0bbf6581330b"
REGIONS = {"cruise": (2.0, 1.0), "near_stop": (1.0, 1.0), "lateral": (2.0, 0.6)}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def numeric_pair(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Expected two numeric entries")
    if not all(type(x) in (float, int) and math.isfinite(x) for x in value):
        raise ValueError("Expected finite JSON numbers")
    return np.asarray(value, dtype=float)
