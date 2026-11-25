"""Check the binding of the reviewed path, and retention of unchanged image bytes."""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from cordriver_fast_review import fast_path, review_packet, approaching_boundary
from cordriver_guidance_wire import CHANNELS, build
from cordriver_reachable_options import load, save, sha


class FastReviewTests(unittest.TestCase):
    def test_approach_gate_needs_two_numeric_shrinking_clearances(self):
        close = dict(initial_clearance_m=1.25, min_clearance_lower_bound_m=0.925)
        self.assertTrue(approaching_boundary(close, close))
        self.assertFalse(approaching_boundary(close, None))
        self.assertFalse(approaching_boundary(close, dict(close, initial_clearance_m=1.0)))
        self.assertFalse(approaching_boundary(close, dict(close, min_clearance_lower_bound_m=0.93)))

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.token = "ab12"
        f = self.root / "frontend" / self.token
        f.mkdir(parents=True)
        xy = np.cumsum([[0.0, 1.0]] * 6, axis=0).tolist()
        save(
            f / "sample.json",
            dict(
                token=self.token,
                scene_name="test",
                fallback=True,
                can_bus=[0.0] * 18,
                qp_reference_steps=[[0.0, 1.0]] * 6,
                final_xy_lidar_m=xy,
                reference_xy_lidar_m=xy,
            ),
        )
        save(f / "visual_map.json", {})
        save(f.parent / "frontend.json", {})
        save(
            self.root / "native_states.json",
            dict(
                frontend_sha256=sha(f.parent / "frontend.json"),
                samples=[dict(token=self.token, scene="test")],
            ),
        )
        sec = self.root / "secondary_primary"
        sec.mkdir()
        save(sec / "sources.json", {})
        p = self.root / "packets" / self.token
        p.mkdir(parents=True)
        images = []
        camera = dict(
            K_original_px=[[30.0, 0, 48.0], [0, 30.0, 32.0], [0, 0, 1.0]],
            T_camera_from_reference_ego=np.eye(4).tolist(),
        )
        for name in sorted(CHANNELS):
            im = p / (name + ".png")
            Image.new("RGB", (96, 64)).save(im)
            images.append(dict(label=name + " original RGB", path=im.name, sha256=sha(im)))
        images.append(dict(images[0], label="old overlay"))
        ctx = dict(
            navigation=dict(command="STRAIGHT", polyline_guidance_m=[[0.0, 0.0], [3.0, 0.0]]),
            cameras={name: camera for name in CHANNELS},
            overlay_geometry=dict(
                xy_guidance_to_reference_ego_linear=np.eye(2).tolist(),
                xy_guidance_origin_in_reference_ego_m=[0.0, 0.0],
                display_height_reference_ego_z_m=2.0,
            ),
        )
        self.packet = p / "packet.json"
        save(self.packet, dict(token=self.token, images=images, model_context=ctx))
        self.result = self.root / "return.json"
        save(
            self.result,
            dict(
                token=self.token,
                policy="fast",
                candidate=None,
                returned_xy_lidar_m=xy,
                native_sha256=sha(f / "sample.json"),
                map_sha256=sha(f / "visual_map.json"),
                packet_sha256=sha(self.packet),
                secondary_sources_sha256=sha(sec / "sources.json"),
                secondary_input_sha256=None,
            ),
        )

    def test_wire_displays_delivered_path_and_keeps_six_original_images(self):
        packet = review_packet(self.root, self.token, self.result, self.root / "review")
        old, _ = build(self.packet)
        bound, raw = build(packet)
        self.assertEqual([r["sha256"] for r in old["images"][:6]], [r["sha256"] for r in bound["images"][:6]])
        self.assertNotEqual(old["body_sha256"], bound["body_sha256"])
        self.assertIn("actual fast return", json.loads(raw)["instructions"])
        steps = load(packet)["model_context"]["trajectory"]
        self.assertEqual([r["t_s"] for r in steps], [0.5 * j for j in range(7)])
        self.assertAlmostEqual(steps[-1]["speed_mps"], 2.0, places=2)

    def test_reject_coordinates_or_geometry_edited_after_fast_planning(self):
        wrong = load(self.result)
        wrong["returned_xy_lidar_m"][2][0] += 1.0
        alternate = self.root / "unbound.json"
        save(alternate, wrong)
        with self.assertRaisesRegex(ValueError, "COORDINATE"):
            fast_path(self.root, self.token, alternate)
        (self.root / "secondary_primary/sources.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "BINDING"):
            fast_path(self.root, self.token, self.result)


@unittest.skipUnless(os.environ.get("CORDRIVER_REPLAY_STUDY"), "requires the local nuScenes transfer study")
class GuidedPlannerIntegrationTest(unittest.TestCase):
    def test_bound_adjust_reaches_planner_with_network_disabled(self):
        from cordriver_guidance_wire import MODEL
        from cordriver_pipeline import plan, checked_return
        from cordriver_published_xy import published_xy

        root = Path(os.environ["CORDRIVER_REPLAY_STUDY"]).resolve()
        data = Path(os.environ["NUSCENES_ROOT"]).resolve()
        token = "c4bdc2fcf6ae4ba599d53663ed2ab5c5"
        study = root / "archived/study"
        fast = root / "archived/fast" / token / "return.json"
        answer = load(root / "archived/window_comparison" / token / "task.json")["guidance"]
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            packet = review_packet(study, token, fast, tmp / "offer")
            _, wire = build(packet)
            folder = tmp / "in_process_fixture" / token
            folder.mkdir(parents=True)
            (folder / "request.json").write_bytes(wire)
            save(folder / "native_output.json", answer)
            save(
                folder / "native_envelope.json",
                dict(
                    status="completed",
                    model=MODEL,
                    output=[
                        dict(
                            type="message",
                            role="assistant",
                            status="completed",
                            content=[dict(type="output_text", text=json.dumps(answer))],
                        )
                    ],
                ),
            )
            save(
                folder / "receipt.json",
                dict(
                    token=token,
                    requested_model=MODEL,
                    returned_model=MODEL,
                    http=200,
                    status="COMPLETE",
                    origin="IN_PROCESS_FIXTURE_ONLY",
                ),
            )
            with patch("socket.socket.connect", side_effect=AssertionError("Unexpected network activity")):
                plan(
                    SimpleNamespace(
                        study=study,
                        token=token,
                        data=data,
                        out=tmp / "plan",
                        fast_only=False,
                        fast_return=fast,
                        guided_search="minimal",
                        responses=folder.parent,
                        live=False,
                        frozen_response=None,
                    )
                )
            _, selected = checked_return(study, token, tmp / "plan/return.json")
            ref = load(root / "archive_recheck/window_minimal" / token / "minimal/result.json")
            self.assertEqual(selected, published_xy(Path(ref["selected_path"])).tolist())
