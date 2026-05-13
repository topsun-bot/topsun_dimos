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

"""Feishu (Lark) custom bot webhook: text messages.

Webhook URL and secret are configured via ``GlobalConfig`` (environment variables
and optional ``[feishu]`` in repo-root ``dimos.local.toml``; see ``dimos.local.example.toml``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import requests

from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_DEFAULT_TIMEOUT_S = 5.0


def _feishu_sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    mac = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(mac).decode("utf-8")


def send_feishu_text(
    webhook_url: str,
    text: str,
    *,
    secret: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> bool:
    """POST a text message to a Feishu custom bot webhook.

    Args:
        webhook_url: Full hook URL from Feishu group bot settings.
        text: Plain text body (Feishu ``msg_type`` = ``text``).
        secret: If the bot has signature verification enabled, pass the secret.
        timeout_s: HTTP timeout in seconds.

    Returns:
        True if the HTTP call succeeded and the API reported success.
    """
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _feishu_sign(secret, ts)

    try:
        resp = requests.post(webhook_url, json=payload, timeout=timeout_s)
    except requests.RequestException as e:
        logger.warning("Feishu webhook request failed", error=str(e))
        return False

    if not resp.ok:
        logger.warning(
            "Feishu webhook HTTP error",
            status_code=resp.status_code,
            body=resp.text[:500],
        )
        return False

    try:
        data = resp.json()
    except ValueError:
        logger.warning("Feishu webhook returned non-JSON", body=resp.text[:500])
        return False

    code = data.get("code")
    if code == 0:
        return True
    # Some deployments return StatusCode instead
    status_code = data.get("StatusCode")
    if status_code == 0:
        return True

    logger.warning("Feishu webhook API error", response=data)
    return False
