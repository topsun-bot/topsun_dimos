# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import socket
from typing import Any
from urllib.parse import urlparse

DEFAULT_QWEN_MLX_BASE_URL = "http://10.10.197.175:8080"
DEFAULT_QWEN_MLX_MODEL = "/Users/dijia/models/Qwen3.6-27B-mlx-bf16"
DEFAULT_QWEN_MLX_TIMEOUT_SECONDS = 180.0


def qwen_mlx_base_url() -> str:
    return os.getenv("DIMOS_QWEN_MLX_BASE_URL", DEFAULT_QWEN_MLX_BASE_URL).rstrip("/")


def qwen_mlx_model_name() -> str:
    return os.getenv("DIMOS_QWEN_MLX_MODEL", DEFAULT_QWEN_MLX_MODEL)


def qwen_mlx_timeout_seconds() -> float:
    raw_timeout = os.getenv("DIMOS_QWEN_MLX_TIMEOUT_SECONDS")
    if raw_timeout is None:
        return DEFAULT_QWEN_MLX_TIMEOUT_SECONDS
    return float(raw_timeout)


def qwen_mlx_model_kwargs() -> dict[str, Any]:
    return {
        "base_url": qwen_mlx_base_url(),
        "api_key": os.getenv("DIMOS_QWEN_MLX_API_KEY", "EMPTY"),
        "timeout": qwen_mlx_timeout_seconds(),
        "max_retries": 0,
    }


def qwen_mlx_installed() -> str | None:
    """Return an error message if the local Qwen MLX OpenAI-compatible server is unavailable."""
    parsed = urlparse(qwen_mlx_base_url())
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host is None:
        return f"Invalid DIMOS_QWEN_MLX_BASE_URL: {qwen_mlx_base_url()}"

    try:
        with socket.create_connection((host, port), timeout=3.0):
            pass
        return None
    except Exception as exc:
        return (
            "Cannot connect to local Qwen MLX server. "
            f"Expected OpenAI-compatible endpoint at {qwen_mlx_base_url()} "
            f"(override with DIMOS_QWEN_MLX_BASE_URL). Error: {exc}"
        )
