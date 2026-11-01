"""Write-once offline analysis outputs; sealed inputs are always read-only.

The legacy result roots below were already evidence at this version's creation.
New result roots can be created, then sealed with a SHA manifest. No permission
flag can override an existing destination or an evidence-directory guard.
"""
import json
from pathlib import Path

from .contracts import Failure, need

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / 'implementation/results'
LEGACY_EVIDENCE_ROOTS = (
    'casebook', 'gpt61_live', 'gpt61_subscription', 'nuscenes',
    'process_recovery', 't1_unknown_diagnosis', 'v2', 'visual_report',
)


def _sealed_directory(path):
    if any(path == RESULTS / name for name in LEGACY_EVIDENCE_ROOTS):
        return True
    if not path.is_dir():
        return False
    return any((p.name.upper().startswith('DELIVERY_SHA') or
               (p.name.upper().startswith('SHA') and 'MANIFEST' in p.name.upper())) and p.is_file()
               for p in path.iterdir())


def check_new_destination(path, readonly_inputs=()):
    """Validate before creating anything; resolve symlink parents as well."""
    raw = Path(path).expanduser()
    need(not raw.exists() and not raw.is_symlink(), 'OUTPUT_EXISTS',
         'Destination already exists; choose a new output path. Evidence is never refreshed in place.',
         path=str(raw))
    target = raw.resolve()
    for parent in (target, *target.parents):
        need(not _sealed_directory(parent), 'SEALED_EVIDENCE',
             'Destination is inside sealed or historical evidence; choose a new independent directory.',
             path=str(target), sealed_ancestor=str(parent))
    for source in map(lambda x: Path(x).expanduser().resolve(), readonly_inputs):
        need(target != source and source not in target.parents and target not in source.parents,
             'INPUT_OUTPUT_OVERLAP', 'Inputs and output must have separate directory trees.',
             input=str(source), output=str(target))
    return target


def reserve_directory(path, readonly_inputs=()):
    target = check_new_destination(path, readonly_inputs)
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise Failure('OUTPUT_EXISTS', 'Destination appeared concurrently; no output was overwritten.', path=str(target)) from None
    return target


def open_new(path, mode='x', **kwargs):
    need(mode in ('x', 'xb'), 'OUTPUT_MODE', 'Only exclusive creation is supported')
    target = check_new_destination(path)
    need(target.parent.is_dir(), 'OUTPUT_PARENT', 'Reserve the output directory first', path=str(target.parent))
    try:
        return target.open(mode, **kwargs)
    except FileExistsError:
        raise Failure('OUTPUT_EXISTS', 'Destination appeared concurrently; no output was overwritten.', path=str(target)) from None


def write_json_new(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    with open_new(path, 'x', encoding='utf-8') as stream:
        stream.write(text)


def write_text_new(path, text):
    with open_new(path, 'x', encoding='utf-8') as stream:
        stream.write(text)
