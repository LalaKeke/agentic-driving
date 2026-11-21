"""File hashing and dataset-relative paths shared by the visual frontend."""
import hashlib
from pathlib import Path


def sha256(path):
    with path.open('rb') as f:
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def data_path(path, root):
    parts = Path(path).parts
    for index, name in enumerate(parts):
        if name in ('samples', 'sweeps'):
            candidate = root.joinpath(*parts[index:])
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(path)
