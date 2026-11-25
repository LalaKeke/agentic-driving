"""Check local CorDriver assets without loading weights, pickles or model credentials."""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from cordriver_paths import load_asset_manifest


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            hasher.update(block)
    return hasher.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, default=Path("runs/tools"))
    parser.add_argument("--data", type=Path, required=True, help="Licensed nuScenes v1.0-trainval with CAN and maps")
    parser.add_argument("--sparse", type=Path, help="Check the secondary map exports against this study")
    args = parser.parse_args()
    spec = load_asset_manifest()
    upstream = args.tools / "cordriver"
    revision = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], capture_output=True, text=True,
    )
    pinned = spec["cordriver"]
    checks = {"cordriver revision": revision.returncode == 0 and revision.stdout.strip() == pinned["commit"]}
    expected = {upstream / name: value for name, value in pinned["source_sha256"].items()}
    expected.update({args.tools / "cordriver_assets" / name: value["sha256"]
                     for name, value in pinned["assets"].items()})
    if args.sparse:
        expected.update({args.sparse / name: value
                         for name, value in spec["sparsedrive"]["reference_exports_sha256"].items()})
    for path, value in expected.items():
        checks[str(path)] = path.is_file() and digest(path) == value
    for name in ("ego_pose", "sample", "sample_data", "calibrated_sensor", "scene"):
        metadata = args.data / "v1.0-trainval" / (name + ".json")
        checks[str(metadata)] = metadata.is_file()
    for camera in ("FRONT", "FRONT_LEFT", "FRONT_RIGHT", "BACK", "BACK_LEFT", "BACK_RIGHT"):
        images = args.data / "samples" / ("CAM_" + camera)
        checks[str(images)] = next(images.glob("*.jpg"), None) is not None
    for name, pattern in (("can_bus", "*_pose.json"), ("can_bus", "*_steeranglefeedback.json"),
                          ("can_bus", "*_route.json"), ("maps/expansion", "*.json")):
        folder = args.data / name
        checks[str(folder / pattern)] = next(folder.glob(pattern), None) is not None
    for label, ok in checks.items():
        print(("OK   " if ok else "FAIL "), label)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
