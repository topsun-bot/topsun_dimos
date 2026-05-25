#!/usr/bin/env python3
"""Standalone test for VLM-based object detection and memory-image query.

Usage:
    # DashScope 百炼 (native API):
    export DASHSCOPE_API_KEY="sk-..."
    python test_vlm_navigation.py image.jpg --target "灭火器"
"""

import argparse
import base64
import json
import os
import sys


def _resolve():
    """Resolve VLM credentials with strict key-URL pairing."""
    provider = os.getenv("DIMOS_VLM_PROVIDER", "").lower().strip()

    if not provider:
        if os.getenv("DASHSCOPE_API_KEY"):
            provider = "dashscope"
        elif os.getenv("DIMOS_VLM_API_KEY") or os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        else:
            provider = "dashscope"

    if provider == "dashscope":
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            print("ERROR: DASHSCOPE_API_KEY not set")
            sys.exit(1)
        # model = os.getenv("DIMOS_VLM_MODEL_NAME", "qwen3.6-plus")
        model = os.getenv("DIMOS_VLM_MODEL_NAME", "qwen-vl-plus")

        # Native API endpoint — NOT compatible-mode
        base_url = "https://dashscope.aliyuncs.com/api/v1"
        return provider, api_key, base_url, model

    elif provider == "openai":
        api_key = os.getenv("DIMOS_VLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("ERROR: DIMOS_VLM_API_KEY or OPENAI_API_KEY not set")
            sys.exit(1)
        model = os.getenv("DIMOS_VLM_MODEL_NAME", "gpt-4o-mini")
        base_url = os.getenv("DIMOS_VLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        return provider, api_key, base_url, model

    else:
        print(f"ERROR: Unknown provider: {provider}")
        sys.exit(1)


def vlm_query_dashscope(image_path_or_array, prompt, api_key, base_url, model):
    """DashScope native MultiModalConversation API."""
    import io

    import cv2
    import dashscope
    from dashscope import MultiModalConversation
    from PIL import Image as PILImage

    dashscope.base_http_api_url = base_url

    if isinstance(image_path_or_array, str):
        img = cv2.imread(image_path_or_array)
        if img is None:
            raise FileNotFoundError(f"Cannot read image: {image_path_or_array}")
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    else:
        img_rgb = image_path_or_array

    pil_img = PILImage.fromarray(img_rgb)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    messages = [{
        "role": "user",
        "content": [
            {"image": f"data:image/png;base64,{img_b64}"},
            {"text": prompt},
        ],
    }]

    response = MultiModalConversation.call(
        api_key=api_key,
        model=model,
        messages=messages,
    )

    if response.output and response.output.choices:
        content = response.output.choices[0].message.content
        if isinstance(content, list) and len(content) > 0:
            return content[0].get("text", "") or ""
        return str(content)

    raise RuntimeError(
        f"DashScope error: code={response.status_code} msg={response.message}"
    )


def vlm_query_openai(image_path_or_array, prompt, api_key, base_url, model):
    """OpenAI-compatible API."""
    import io

    import cv2
    from openai import OpenAI
    from PIL import Image as PILImage

    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)

    if isinstance(image_path_or_array, str):
        img = cv2.imread(image_path_or_array)
        if img is None:
            raise FileNotFoundError(f"Cannot read image: {image_path_or_array}")
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    else:
        img_rgb = image_path_or_array

    pil_img = PILImage.fromarray(img_rgb)
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=85)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return response.choices[0].message.content or ""


def vlm_query(image_path_or_array, prompt, model=None):
    """Dispatch to correct VLM backend."""
    import time
    provider, api_key, base_url, default_model = _resolve()
    model = model or default_model
    print(f"[provider={provider}, model={model}, base_url={base_url}]")

    t0 = time.time()
    if provider == "dashscope":
        result = vlm_query_dashscope(image_path_or_array, prompt, api_key, base_url, model)
    else:
        result = vlm_query_openai(image_path_or_array, prompt, api_key, base_url, model)
    print(f"  → VLM call took {time.time() - t0:.1f}s")
    return result


def run_detect_objects(image_path, model=None):
    """Way 2: Capture-time object detection."""
    print("\n" + "=" * 60)
    print("TEST 1: detect_objects_in_view (Way 2 - capture-time detection)")
    print("=" * 60)

    prompt = (
        "List all notable, nameable objects you see in this image "
        "(furniture, appliances, equipment, decorations, people, etc.). "
        "Return ONLY a JSON array of objects: "
        '[{"name": "<short name>", "description": "<brief>"}]. '
        "Skip walls, floors, ceilings, doors. Focus on distinct objects."
    )

    response = vlm_query(image_path, prompt, model)
    print(f"Raw VLM response:\n{response}\n")

    try:
        resp = response.strip()
        if resp.startswith("```"):
            lines = resp.split("\n")
            resp = "\n".join(lines[1:])
            if resp.endswith("```"):
                resp = resp[:-3]
        objects = json.loads(resp)
        print(f"Parsed {len(objects)} object(s):")
        for obj in objects:
            print(f"  - {obj.get('name', '?')}: {obj.get('description', '')}")
    except json.JSONDecodeError:
        print("FAILED to parse JSON from response — prompt may need tuning")


def run_find_object(image_path, target, model=None):
    """Way 1: Search for a specific object in a stored image."""
    print("\n" + "=" * 60)
    print(f"TEST 2: VLM memory-image query for '{target}' (Way 1)")
    print("=" * 60)

    prompt = (
        f"Look at this image. Is there a '{target}' visible anywhere in this image? "
        f"Answer ONLY 'YES' or 'NO'."
    )

    response = vlm_query(image_path, prompt, model)
    print(f"Query: 'Is there a {target}?'")
    print(f"VLM answer: {response.strip()}")


def run_find_bbox(image_path, target, model=None):
    """Test in-frame bbox detection."""
    import cv2

    from dimos.msgs.sensor_msgs.Image import Image as DimosImage
    from dimos.navigation.visual.query import parse_object_bbox_from_vlm_response
    from dimos.utils.generic import extract_json_from_llm_response

    print("\n" + "=" * 60)
    print(f"TEST 3: In-frame bbox detection for '{target}'")
    print("=" * 60)

    prompt = (
        f"Look at this image and find the '{target}'. "
        "Return JSON for every matching instance. Prefer either format:\n"
        "1) A single object: {\"name\": \"...\", \"bbox\": [x1, y1, x2, y2]}\n"
        "2) Multiple objects: [{\"label\": \"...\", \"bbox_2d\": [x1, y1, x2, y2]}, ...]\n"
        "If not found, return null."
    )

    response = vlm_query(image_path, prompt, model)
    print(f"Raw response: {response.strip()}")

    if isinstance(image_path, str):
        img = cv2.imread(image_path)
        dimos_img = DimosImage.from_numpy(img)
    else:
        dimos_img = DimosImage.from_numpy(image_path)

    parsed = extract_json_from_llm_response(response)
    bbox = parse_object_bbox_from_vlm_response(parsed, target, dimos_img)
    if bbox:
        print(f"Parsed bbox (pixels): {bbox}")
    else:
        print("FAILED to parse bbox from response (navigation L2 would miss)")


def main():
    parser = argparse.ArgumentParser(description="Test VLM-based object recognition")
    parser.add_argument("image", nargs="?", help="Path to an image file")
    parser.add_argument("--target", default="fire extinguisher",
                        help="Object to search for")
    parser.add_argument("--model", help="Override VLM model name")
    parser.add_argument("--camera", action="store_true", help="Capture from camera")
    args = parser.parse_args()

    model = args.model

    if args.camera:
        import cv2
        cap = cv2.VideoCapture(0)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            print("ERROR: Cannot capture from camera")
            sys.exit(1)
        image_input = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        print("Captured frame from camera")
    elif args.image:
        image_input = args.image
        print(f"Using image: {args.image}")
    else:
        print("ERROR: Provide an image path or --camera")
        sys.exit(1)

    run_detect_objects(image_input, model)
    run_find_object(image_input, args.target, model)
    run_find_bbox(image_input, args.target, model)

    print("\n" + "=" * 60)
    print("All tests complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
