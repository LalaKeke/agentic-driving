"""Check the paths a PR would introduce or change on main. Requires only Git and Python."""
import argparse
import json
import os
from pathlib import PurePosixPath
import subprocess
import sys

# These are repository interfaces, not general allowances for JSON, YAML or Markdown.
MAINTENANCE_FILES = {
    ".gitattributes", ".gitignore", "AGENTS.md", "README.md",
    ".github/pull_request_template.md",
    "implementation/case_study/README.md",
    "implementation/environment/README.md",
    "implementation/environment/assets.json",
    "implementation/environment/numba_warning.patch",
    "implementation/environment/requirements-control.txt",
    "implementation/environment/requirements-vision.txt",
    "implementation/evaluation/README.md",
    "implementation/experiments/CORDRIVER.md",
}
ARCHIVE_DIRS = {"outputs", "results", "runs", "data", "__pycache__", ".venv"}
CODE_DIRS = {"case_study", "environment", "evaluation", "experiments", "tests"}


def allowed(path):
    p = PurePosixPath(path)
    if ARCHIVE_DIRS.intersection(part.lower() for part in p.parts[:-1]):
        return False
    if path in MAINTENANCE_FILES:
        return True
    if p.parent == PurePosixPath(".github/workflows"):
        return p.suffix in {".yml", ".yaml"}
    if p.parent == PurePosixPath("implementation/experiments/cordriver_config"):
        return p.name == "protocol.json" or p.name.endswith("_protocol.json")
    in_code = (len(p.parts) >= 3 and p.parts[0] == "implementation"
               and p.parts[1] in CODE_DIRS)
    in_ci = p.parts[:2] == (".github", "scripts")
    return (in_code or in_ci) and p.suffix in {".py", ".sh"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="PR base commit or ref")
    parser.add_argument("head", help="PR head commit or ref")
    args = parser.parse_args()

    # Three dots select the contribution of the branch even if main has advanced.
    # --no-renames makes moves into/out of an archive an ordinary delete plus add.
    diff = subprocess.check_output([
        "git", "diff", "--raw", "-z", "--no-renames", "--no-ext-diff",
        "--diff-filter=ACMRT", f"{args.base}...{args.head}", "--",
    ])
    fields = diff.split(b"\0")[:-1]
    blocked = []
    for header, name in zip(fields[::2], fields[1::2]):
        new_mode = header.split()[1]
        path = os.fsdecode(name)
        if new_mode not in (b"100644", b"100755") or not allowed(path):
            blocked.append(path)

    if blocked:
        print("release-scope: these additions or changes are outside the main release scope:")
        for path in blocked:
            print("  " + json.dumps(path))
        print("Keep experiment artifacts in outputs/. See AGENTS.md for the release procedure.")
        return 1
    print(f"release-scope: passed ({len(fields) // 2} added or changed paths checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
