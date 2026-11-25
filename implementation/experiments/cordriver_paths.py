"""Resolve external CorDriver dependencies relative to the checkout or explicit environment."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(os.environ.get("CORDRIVER_TOOLS", ROOT / "runs/tools")).expanduser().resolve()
CHECK_PYTHON = Path(os.environ.get("CORDRIVER_CHECK_PYTHON", ROOT / ".venv/bin/python")).expanduser().absolute()
CONFIG = Path(__file__).resolve().parent / "cordriver_config"


def load_asset_manifest():
    """Include the two upstream label-processing sources used by the paper evaluator."""
    manifest = json.loads((ROOT / "implementation/environment/assets.json").read_bytes())
    manifest["cordriver"]["source_sha256"].update({
        "projects/mmdet3d_plugin/datasets/nuscenes_vad_dataset.py":
            "0556c73ee4886e646f6970e41af70d25b6edb3b9c4c49d9f7a043be3a6b0c45a",
        "projects/mmdet3d_plugin/datasets/pipelines/transform_3d.py":
            "56623217271309973c8a9ead8bb26f172966865a0a9a0494715edb8c86e7d529",
    })
    return manifest
