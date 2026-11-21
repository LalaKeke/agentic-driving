"""Use the independent Shapely-2 scorer from the project's evaluation interpreter."""
import json
from pathlib import Path
import subprocess

from cordriver_paths import CHECK_PYTHON

import shapely

import cordriver_iter_dense


def check(plan,visual):
    if hasattr(shapely,"polygons"):
        return cordriver_iter_dense.check(plan,visual)
    script=Path(__file__).resolve()
    minimal_plan={k:plan[k] for k in ("state_xy_yaw_v","controls_accel_steer")}
    inputs=dict(plan=minimal_plan,visual=dict(current_objects=visual["current_objects"]))
    result=subprocess.run([str(CHECK_PYTHON),str(script)],input=json.dumps(inputs),text=True,
                          stdout=subprocess.PIPE,check=True)
    return json.loads(result.stdout)


if __name__=="__main__":
    import sys
    inputs=json.load(sys.stdin)
    json.dump(cordriver_iter_dense.check(inputs["plan"],inputs["visual"]),sys.stdout,allow_nan=False)
