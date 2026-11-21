"""Build the frozen oriented-guidance request, verify the native answer and debit a single live POST."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from jsonschema import validate

MODEL = "gpt-6.2-astra"
ENDPOINT = "https://api.openai.com/v1/responses"
NODES = ("at_1_5s","at_3_0s")
PROMPT = """You provide a 3-second open-loop driving guidance decision. The six original
views, the extra annotated front image, calibration, measured vehicle state, navigation
and learned pre-optimization proposal refer to the supplied observation. Read the legend
and the numerical fields. The navigation polyline is a recommended path sampled by
distance; it does not specify where the car must be at a future time, a target speed,
obstacle clearance or road permission. Inspect the images and the initial trajectory.
KEEP it if appropriate, otherwise ADJUST using both 1.5 s and 3.0 s nodes. No object
reports or reasoning text in the output. For KEEP return null for both nodes. For ADJUST,
each center_xy_m is the desired body center in the fixed initial planning frame
(x forward, y left, meters). region_yaw_deg is the orientation of the region's length
axis relative to initial ego forward, positive counterclockwise toward ego left, between
-180 and 180 degrees. It orients the longitudinal and lateral tolerances and does not
impose a vehicle yaw. Full length and width in meters: cruise (2,1), near_stop (1,1),
lateral (2,0.6). Set nonnegative ordered absolute speed_range_mps at each node.
Both arms will use this same answer. First try to reach both regions, optionally with
bounded evidence-supported corridor changes; if both cannot be reached the downstream
planner may report deviations. Set the targets you judge appropriate for the scene and
navigation, including slowing or stopping when warranted."""
CHANNELS = {"CAM_FRONT", "CAM_FRONT_LEFT", "CAM_FRONT_RIGHT","CAM_BACK","CAM_BACK_LEFT","CAM_BACK_RIGHT"}
LIMIT = 10


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")

def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}

def output_schema():
    pair = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
    node = obj({"region_type": {"type": "string", "enum": ["cruise", "near_stop", "lateral"]},
                "center_xy_m": pair, "speed_range_mps": pair})
    optional_node = {"anyOf": [node, {"type": "null"}]}
    return obj({"decision": {"type": "string", "enum": ["KEEP", "ADJUST"]},
                **{name: optional_node for name in NODES}})

def build_request(path):
    packet = json.loads(path.read_bytes())
    token = packet["token"]
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", token):
        raise ValueError("INVALID_PACKET_TOKEN")
    if not isinstance(packet["model_context"], dict) or not packet["images"]:
        raise ValueError("MISSING_MODEL_INPUTS")
    content = [{"type": "input_text", "text": encode(packet["model_context"]).decode()}]
    images = []
    for item in packet["images"]:
        raw = (path.parent / item["path"]).read_bytes()
        if sha(raw) != item["sha256"]:
            raise ValueError("INPUT_IMAGE_DIGEST_MISMATCH")
        if raw.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        else:
            raise ValueError("EXPECTED_ORIGINAL_JPEG_OR_RENDERED_PNG")
        images.append({"label": item["label"], "sha256": item["sha256"], "bytes": len(raw)})
        content.extend([{"type": "input_text", "text": item["label"]},
                        {"type": "input_image", "detail": "high",
                         "image_url": f"data:{mime};base64," + base64.b64encode(raw).decode()}])
    body = {"model": MODEL, "instructions": PROMPT,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "trajectory_guidance",
                                "strict": True, "schema": output_schema()}},
            "reasoning": {"effort": "medium"}, "max_output_tokens": 4000, "tools": [],
            "store": False, "stream": False}
    wire = encode(body)
    case = {"token": token, "packet": str(path.resolve()), "packet_sha256": sha(path.read_bytes()),
            "images": images, "body_sha256": sha(wire), "body_bytes": len(wire)}
    return case, wire

def schema():
    result = output_schema()
    for key in NODES:
        node = result["properties"][key]["anyOf"][0]
        node["properties"]["region_yaw_deg"] = {"type":"number","minimum":-180,"maximum":180}
        node["required"] = list(node["properties"])
    return result

def build(path):
    packet = json.loads(path.read_bytes())
    ctx = packet["model_context"]
    if set(ctx["cameras"]) != CHANNELS or len(packet["images"]) != 7:
        raise ValueError("EXPECTED_SIX_CAMERAS_AND_ONE_FRONT_OVERLAY")
    if {a["label"].split()[0] for a in packet["images"] if a["label"].endswith("original RGB")} != CHANNELS:
        raise ValueError("EXPECTED_ORIGINAL_CAMERA_IMAGES")
    if ctx["navigation"].get("command") not in ("LEFT","RIGHT","STRAIGHT") or len(ctx["navigation"].get("polyline_guidance_m",[]))<2:
        raise ValueError("MISSING_CAUSAL_ROUTE")
    expected, raw = build_request(path)
    body = json.loads(raw)
    body.update(instructions=PROMPT)
    body["text"]["format"].update(name="oriented_trajectory_guidance",schema=schema())
    wire = encode(body)
    expected.update(body_sha256=sha(wire),body_bytes=len(wire))
    return expected,wire

def parse(data):
    if data.get("model") != MODEL:
        raise ValueError("MODEL_ID_MISMATCH")
    answer=json.loads(native_text(data))
    return check_answer(answer)


def check_answer(answer):
    validate(answer,schema())
    for key in NODES:
        node=answer[key]
        if (answer["decision"]=="KEEP") != (node is None):
            raise ValueError("DECISION_AND_NODES_MISMATCH")
        if node is not None:
            lo,hi=node["speed_range_mps"]
            if not all(math.isfinite(v) for v in [*node["center_xy_m"],lo,hi,node["region_yaw_deg"]]) or not 0<=lo<=hi:
                raise ValueError("INVALID_METRIC_OUTPUT")
    return answer


def native_text(data):
    if data.get("status") != "completed" or data.get("error") or data.get("incomplete_details"):
        raise ValueError("INCOMPLETE_NATIVE_RESPONSE")
    items = data["output"]
    if any(item["type"] not in ("message", "reasoning") for item in items):
        raise ValueError("UNEXPECTED_TOOL_OR_OUTPUT")
    messages = [item for item in items if item["type"] == "message"]
    if len(messages) != 1 or messages[0]["status"] != "completed" or messages[0]["role"] != "assistant":
        raise ValueError("EXPECTED_ONE_COMPLETED_ASSISTANT_MESSAGE")
    parts = messages[0]["content"]
    if not parts or any(p["type"] != "output_text" for p in parts):
        raise ValueError("REFUSED_OR_MISSING_TEXT")
    return "".join(p["text"] for p in parts)


def obtain(packet, responses, output, live):
    """No dispatch occurs in archive mode. Compare the complete wire, including all image bytes."""
    token = json.loads(packet.read_bytes())["token"]
    binding, wire = build(packet)
    if responses is not None:
        source = responses / token
        if (source / "request.json").read_bytes() != wire:
            raise ValueError("ARCHIVED_REQUEST_DIFFERS_FROM_CURRENT_INPUT")
        receipt = json.loads((source / "receipt.json").read_bytes())
        answer = parse(json.loads((source / "native_envelope.json").read_bytes()))
        if (receipt["token"] != token or receipt["requested_model"] != MODEL
                or receipt["returned_model"] != MODEL or receipt["status"] != "COMPLETE" or receipt["http"] != 200
                or answer != json.loads((source / "native_output.json").read_bytes())):
            raise ValueError("INVALID_ARCHIVED_RESPONSE_BINDING")
        return responses, answer
    if not live:
        raise ValueError("ASTRA_REQUIRED: supply --responses for an offline replay or explicitly select --live")
    import httpx
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DRIVING_API_KEY")
    if not key and os.environ.get("DRIVING_API_KEY_FILE"):
        key = Path(os.environ["DRIVING_API_KEY_FILE"]).expanduser().read_text().strip()
    if not key or any(char.isspace() for char in key):
        raise ValueError("Configure a single OPENAI_API_KEY or DRIVING_API_KEY_FILE for --live")
    source = output / token
    source.mkdir(parents=True, exist_ok=False)
    (source / "request.json").write_bytes(wire)
    save(source / "precharge.json", {"token":token, "body_sha256":binding["body_sha256"], "limit":1})
    receipt = dict(token=token, requested_model=MODEL, client_post_attempts=1, returned_model=None,
                   status="STOP", dispatch="UNKNOWN", http=None, usage=None)
    start = time.monotonic()
    try:
        with httpx.Client(timeout=450, follow_redirects=False) as client:
            res = client.post(ENDPOINT, content=wire, headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
        receipt.update(dispatch="HTTP_RESPONSE_RECEIVED", http=res.status_code, request_id=res.headers.get("x-request-id"))
        res.raise_for_status()
        envelope = res.json()
        save(source / "native_envelope.json", envelope)
        receipt.update(returned_model=envelope.get("model"), usage=envelope.get("usage"))
        answer = parse(envelope)
        save(source / "native_output.json", answer)
        receipt.update(status="COMPLETE", decision=answer["decision"])
    finally:
        receipt["elapsed_s"] = time.monotonic() - start
        save(source / "receipt.json", receipt)
    return output, answer

def bind_frozen(packet, receipt):
    """Bind an archived semantic decision to reproduced wire bytes without redistributing scene imagery."""
    binding, _ = build(packet)
    saved = json.loads(receipt.read_bytes())
    answer = check_answer(saved["native_output"])
    original_output = (json.dumps(answer, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    if (saved["format"] != "cordriver.frozen-guidance.v1" or saved["token"] != binding["token"]
            or saved["requested_model"] != MODEL or saved["returned_model"] != MODEL
            or saved["status"] != "COMPLETE" or saved["http"] != 200
            or saved["body_sha256"] != binding["body_sha256"] or saved["native_output_sha256"] != sha(original_output)
            or saved["answer_canonical_sha256"] != sha(encode(answer))):
        raise ValueError("FROZEN_RESPONSE_OR_REQUEST_BINDING_MISMATCH")
    return saved, answer
