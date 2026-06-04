# WildOS Open-Goal Inference Dockerfile (GPU, minimal)
#
# Build from the repo root:
#   docker build -t wildos-inference:latest \
#     -f dimos/navigation/open_goal/Dockerfile .
#
# Run (self-test, exits after warmup):
#   docker run --gpus all --rm wildos-inference:latest
#
# Run (interactive):
#   docker run --gpus all -it --rm wildos-inference:latest bash

FROM nvidia/cuda:12.4.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 \
    python3-pip \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1

# ---- Python inference dependencies ----
RUN pip3 install --no-cache-dir \
    torch \
    torchvision \
    "timm>=1.0.15" \
    "einops==0.8.1" \
    "numpy>=1.26.4" \
    opencv-python-headless \
    "transformers>=4.53.0,<4.54"

WORKDIR /workspace

# ---- Copy inference code into the dimos namespace ----
RUN mkdir -p /workspace/dimos/navigation
COPY dimos/navigation/open_goal/ /workspace/dimos/navigation/open_goal/

# ---- Minimal dimos stubs ----
RUN mkdir -p /workspace/dimos/utils
RUN printf '%s\n' \
    'from pathlib import Path' \
    '' \
    'def get_data(name: str) -> Path:' \
    '    """Resolve named data directory."""' \
    '    if name == "models_wildos":' \
    '        return Path("/workspace/models")' \
    '    return Path("/workspace/models")' \
    > /workspace/dimos/utils/data.py

RUN touch /workspace/dimos/__init__.py \
    && touch /workspace/dimos/utils/__init__.py \
    && touch /workspace/dimos/navigation/__init__.py

# ---- Copy model weights from local data/models_wildos ----
COPY data/models_wildos/ /workspace/models/

ENV PYTHONPATH=/workspace
ENV HF_HOME=/workspace/models
ENV HF_HUB_CACHE=/workspace/models

# ---- Inference example script ----
RUN printf '%s\n' \
    '#!/usr/bin/env python3' \
    '"""Minimal GPU inference warmup for WildOS Open-Goal."""' \
    '' \
    'import sys, time' \
    'import numpy as np' \
    '' \
    'from dimos.navigation.open_goal import WildOSInference' \
    '' \
    'def main():' \
    '    print("Loading WildOS model (RADIO + SigLIP2)...", flush=True)' \
    '    model = WildOSInference(model_precision="FP16")' \
    '    print(f"Model loaded on {model.device}", flush=True)' \
    '' \
    '    print("Encoding text query...", flush=True)' \
    '    model.encode_text(["orange flag"])' \
    '' \
    '    H, W = 720, 1280' \
    '    rgb = np.random.randint(0, 255, (H, W, 3), dtype=np.uint8)' \
    '    print(f"Running inference on ({H}, {W})...", flush=True)' \
    '    t0 = time.time()' \
    '    trav, frontiers, ad_spatial = model.forward(rgb)' \
    '    dt = 1000 * (time.time() - t0)' \
    '    print(f"Inference: {dt:.1f} ms", flush=True)' \
    '    print(f"  traversability: {trav.shape}, frontiers: {frontiers.shape}")' \
    '' \
    '    if ad_spatial is not None:' \
    '        print(f"  ad_spatial_features: {ad_spatial.shape}")' \
    '        mask = model.localize_object(ad_spatial, rgb.shape[:2])' \
    '        print(f"  binary_mask: {mask.shape}, fg_pixels: {mask.sum()}")' \
    '        K = np.array([[700, 0, W/2], [0, 700, H/2], [0, 0, 1]], dtype=np.float64)' \
    '        pos = model.estimate_3d_position(mask, K, camera_height=0.5)' \
    '        print(f"  3D position: {pos}")' \
    '' \
    '    print("OK -- inference pipeline ready.")' \
    '' \
    'if __name__ == "__main__":' \
    '    main()' \
    > /workspace/infer.py && chmod +x /workspace/infer.py

ENTRYPOINT ["python3", "/workspace/infer.py"]
