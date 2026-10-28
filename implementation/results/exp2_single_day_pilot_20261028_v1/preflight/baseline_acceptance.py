"""Independent, fail-closed acceptance of one simulated physical planning round.

This checks trusted local adapter/runtime records, not a cryptographic proof of a
remote model call. It does not establish GT correctness, calibration, deployment
safety, or a multi-observation closed loop. Synthetic fixtures can establish only
software success. No model, optimizer or real vehicle is invoked by this module.

Successful runtime result fields:
  status, revisions, agent_output, provenance, scene, corridor, trajectory,
  verification, execution, execution_receipt.
The receipt is written after the local SimulatedExecutor returns, and contains
capture_digest, scene_digest, trajectory_digest, verification_digest,
execution_digest, executed_at_s and consumed_once=true.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path

from .contracts import Failure, digest, finite, grid, identity, validate
from .geometry import center_state, integrate, rear_state
from . import t1, t2, t4

_VERSION = 'single_window_physical_v1'
_REQUIRED = ('agent_output', 'provenance', 'scene', 'corridor', 'trajectory',
             'verification', 'execution', 'execution_receipt')
_CLAIM = ('Conditional on the model/fixture scene hypotheses and whole-interval '
          'coverage. Trusted local runtime ledger plus independent checks; '
          'not remote attestation, GT correctness, or deployment safety.')


class _Rejected(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def _require(ok, code, message):
    if not ok:
        raise _Rejected(code, message)


def _close(a, b, tolerance=1e-8):
    return (isinstance(a, (int, float)) and not isinstance(a, bool)
            and isinstance(b, (int, float)) and not isinstance(b, bool)
            and math.isfinite(a) and math.isfinite(b)
            and abs(a-b) <= tolerance)


def _real_images(capture, provenance):
    """Verify bytes against this exact acquisition, never print/read credentials."""
    from PIL import Image
    frames = capture['observations']
    expected = provenance.get('image_sha256')
    _require(isinstance(expected, dict) and set(expected) == {f['id'] for f in frames},
             'IMAGE_PROVENANCE', 'Image hash keys do not match this acquisition.')
    _require(bool(frames), 'IMAGE_PROVENANCE', 'Online inference needs acquired images.')
    for frame in frames:
        name = frame['path']
        _require(isinstance(name, str) and '://' not in name,
                 'IMAGE_SOURCE', 'Fixture or remote image paths are not acquired local images.')
        path = Path(name)
        try:
            _require(path.is_file() and 0 < path.stat().st_size <= 20*1024*1024,
                     'IMAGE_SOURCE', 'An acquired image is absent or outside the input size limit.')
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            _require(actual == expected[frame['id']],
                     'IMAGE_HASH_MISMATCH', 'Image bytes changed after the model request.')
            with Image.open(path) as image:
                _require(image.format in ('JPEG', 'PNG', 'WEBP'),
                         'IMAGE_SOURCE', 'An acquired image is not a supported decoded image.')
                image.verify()
        except (OSError, ValueError):
            raise _Rejected('IMAGE_SOURCE', 'An acquired image cannot be independently decoded.') from None


def assess_round(result, capture):
    """Return a JSON-safe assessment; an incomplete/failed round never raises success.

    ``capture`` is the ORIGINAL observation passed to ClosedLoop.round. A repaired
    scene may increment only its revision, exactly by result.revisions. All other
    acquisition values must remain unchanged. STATIONARY_HOLD is successful tool
    completion but is never labelled as a moving prefix.
    """
    verdict = dict(
        acceptance_version=_VERSION, status='NOT_ACCEPTED',
        strict_live_physical_success=False, software_success=False,
        source=None, motion_class='NOT_EXECUTED',
        scope='SINGLE_OBSERVATION_SIMULATED_PREFIX',
        multi_round_closed_loop_success=False, ground_truth_correctness='NOT_EVALUATED',
        released_prefix_s=0, checks={}, reasons=[], claim=_CLAIM,
    )
    try:
        _require(isinstance(result, dict) and isinstance(capture, dict),
                 'ROUND_STRUCTURE', 'Round result and capture must be JSON objects.')
        _require(result.get('status') == 'executed_simulated_prefix',
                 'ROUND_NOT_EXECUTED', 'The round did not consume a verified simulated prefix.')
        missing = [key for key in _REQUIRED if not isinstance(result.get(key), dict)]
        _require(not missing, 'MISSING_ARTIFACTS', 'Missing complete round artifacts: '+', '.join(missing))
        finite(result); finite(capture)
        validate('capture_v2.schema.json', capture)
        scene, corridor, trajectory = (result[key] for key in ('scene', 'corridor', 'trajectory'))
        verification, execution, receipt = (result[key] for key in ('verification', 'execution', 'execution_receipt'))
        output, provenance = result['agent_output'], result['provenance']
        source = provenance.get('source'); verdict['source'] = source
        _require(source in ('online_model', 'synthetic_fixture'),
                 'UNSUPPORTED_SOURCE', 'Mock/replay/unidentified sources are not online or explicit fixtures.')
        revisions = result.get('revisions')
        _require(type(revisions) is int and revisions >= 0,
                 'REVISION_PROVENANCE', 'A nonnegative semantic revision count is required.')
        expected_capture = deepcopy(capture)
        expected_capture['metadata']['revision'] += revisions
        _require(scene.get('capture') == expected_capture,
                 'CAPTURE_CHANGED', 'Only the recorded revision may change within one observation.')
        _require(scene.get('metadata') == expected_capture['metadata'],
                 'CAPTURE_CHANGED', 'Scene identity differs from its acquired observation.')
        _require(scene.get('provenance') == provenance,
                 'PROVENANCE_MISMATCH', 'Scene and model provenance differ.')
        _require(provenance.get('output_digest') == digest(output),
                 'OUTPUT_DIGEST_MISMATCH', 'Model output differs from its provenance digest.')
        verdict['checks']['acquisition_and_model_identity'] = True

        if source == 'online_model':
            _require(isinstance(provenance.get('model_id'), str) and bool(provenance['model_id']),
                     'MODEL_PROVENANCE', 'Online model identity is missing.')
            _require(type(provenance.get('tool_calls')) is int and provenance['tool_calls'] == 0,
                     'TOOL_ACCESS_UNVERIFIED', 'Online record must explicitly attest zero auxiliary tool calls.')
            _require(isinstance(provenance.get('prompt_version'), str) and bool(provenance['prompt_version'])
                     and isinstance(provenance.get('prompt_sha256'), str)
                     and len(provenance['prompt_sha256']) == 64
                     and all(c in '0123456789abcdef' for c in provenance['prompt_sha256']),
                     'PROMPT_PROVENANCE', 'Versioned prompt identity/hash is missing.')
            _real_images(expected_capture, provenance)
            verdict['checks']['online_image_bytes_and_source'] = True

        # Reconstruct deterministic tools from their original inputs, rather than
        # trusting that the presence of a file named T1/T2 proves its content.
        reconstructed = t1.construct(expected_capture, output, provenance,
                                     allow_synthetic=source == 'synthetic_fixture')
        _require(reconstructed == scene, 'T1_RECONSTRUCTION_MISMATCH',
                 'Metric scene differs from the deterministic T1 result of the recorded input.')
        construction = corridor.get('construction', {})
        _require(isinstance(construction, dict), 'CORRIDOR_POLICY',
                 'Corridor construction metadata must be an explicit object.')
        policy = construction.get('policy', 'initial_anchor')
        _require(policy in ('initial_anchor', 'moving_seed'), 'CORRIDOR_POLICY',
                 'Unknown corridor construction policy cannot establish success.')
        validate('corridor_v2.schema.json', corridor)
        identity(scene, corridor)
        _require(t2.build(scene, policy=policy) == corridor, 'T2_RECONSTRUCTION_MISMATCH',
                 'Corridor differs from the deterministic T2 result of this scene.')
        _require(len(corridor['cells']) == len(grid(scene['metadata']))-1,
                 'CORRIDOR_COVERAGE', 'Corridor does not cover this prediction grid.')
        verdict['checks']['t1_t2_reconstructed'] = True

        validate('trajectory_v2.schema.json', trajectory); identity(scene, trajectory)
        solver = trajectory.get('solver', {})
        _require(solver.get('name') == 'casadi_ipopt_multiple_shooting'
                 and solver.get('status') in ('Solve_Succeeded', 'Solved_To_Acceptable_Level')
                 and not solver.get('fault_injection'),
                 'T3_NOT_CONSTRAINED_SOLUTION',
                 'Trajectory is not an unmodified successful constrained-optimizer result.')
        independent = t4.verify(scene, trajectory)
        _require(independent == verification, 'T4_RECHECK_MISMATCH',
                 'Recorded verification differs from independent raw-scene verification.')
        _require(independent['verdict'] == 'PASS' and not independent['violations']
                 and independent['released_prefix_s'] > 0,
                 'T4_NOT_PASSED', 'Independent verification did not release a positive prefix.')
        verdict['checks']['independent_t4_passed'] = True

        hashes = dict(capture_digest=digest(expected_capture), scene_digest=digest(scene),
                      trajectory_digest=digest(trajectory), verification_digest=digest(verification),
                      execution_digest=digest(execution))
        _require(all(receipt.get(key) == value for key, value in hashes.items())
                 and receipt.get('consumed_once') is True
                 and _close(receipt.get('executed_at_s'), scene['metadata']['reference_time_s']),
                 'EXECUTION_RECEIPT_MISMATCH',
                 'Trusted local execution receipt is missing or belongs to a different observation/artifact.')
        dt = independent['released_prefix_s']; cap = scene['capture']; point = trajectory['points'][0]
        start = rear_state(cap['ego'], cap['vehicle'])
        end = integrate(start, point['acceleration_mps2'], point['steer_rad'], dt, cap['vehicle'])
        center = center_state(end, cap['vehicle'])
        displacement = [center[0]-cap['ego']['x_m'], center[1]-cap['ego']['y_m']]
        _require(execution.get('interface') == 'SIMULATED_EXECUTION_NO_VEHICLE_CONTROL'
                 and _close(execution.get('elapsed_s'), dt)
                 and isinstance(execution.get('center_displacement_m'), list)
                 and len(execution['center_displacement_m']) == 2
                 and all(_close(a, b) for a, b in zip(execution['center_displacement_m'], displacement))
                 and _close(execution.get('yaw_delta_rad'), center[2]-cap['ego']['yaw_rad'])
                 and _close(execution.get('speed_mps'), max(0., center[3])),
                 'EXECUTION_INTEGRATION_MISMATCH',
                 'Reported simulated consumption does not match independent integration of the released prefix.')
        verdict['checks']['execution_receipt_and_integration'] = True
        arc = max(0., (start[3]+end[3])*.5*dt)
        translation = math.hypot(*displacement)
        motion = 'MOVING_PREFIX' if max(arc, translation) > 1e-5 else 'STATIONARY_HOLD'
        verdict.update(
            status='ONLINE_SINGLE_WINDOW_SUCCESS' if source == 'online_model' else 'SYNTHETIC_SOFTWARE_SUCCESS',
            strict_live_physical_success=source == 'online_model', software_success=True,
            motion_class=motion, released_prefix_s=dt,
            prefix_arc_length_m=arc, prefix_center_displacement_m=translation,
        )
    except _Rejected as exc:
        verdict['reasons'].append(dict(code=exc.code, message=exc.message))
    except Failure as exc:
        verdict['reasons'].append(dict(code=exc.record['code'],
                                       message='Independent physical/input check rejected the round.',
                                       category=exc.record['category']))
    except (KeyError, TypeError, ValueError, IndexError, OverflowError, AttributeError):
        verdict['reasons'].append(dict(code='ARTIFACT_STRUCTURE',
                                       message='Malformed round artifacts cannot establish success.'))
    return verdict
