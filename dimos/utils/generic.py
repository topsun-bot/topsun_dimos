# Copyright 2025-2026 Dimensional Inc.
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

from collections.abc import Callable
import hashlib
import json
import os
import socket
import string
from typing import Any, Generic, TypeVar, overload
import uuid

import psutil


def get_local_ips() -> list[tuple[str, str]]:
    """Return ``(ip, interface_name)`` for every non-loopback IPv4 address.

    Picks up physical, virtual, and VPN interfaces (including Tailscale).
    """
    results: list[tuple[str, str]] = []
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                results.append((addr.address, iface))
    return results


_T = TypeVar("_T")


def truncate_display_string(arg: Any, max: int | None = None) -> str:
    """
    If we print strings that are too long that potentially obscures more important logs.

    Use this function to truncate it to a reasonable length (configurable from the env).
    """
    string = str(arg)

    if max is not None:
        max_chars = max
    else:
        max_chars = int(os.getenv("TRUNCATE_MAX", "2000"))

    if max_chars == 0 or len(string) <= max_chars:
        return string

    return string[:max_chars] + "...(truncated)..."


def extract_json_from_llm_response(response: str) -> Any:
    """Parse JSON object or array from an LLM text response."""
    if not response:
        return None

    text = response.strip()
    if text.lower() in ("none", "null"):
        return None

    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start_obj = text.find("{")
    end_obj = text.rfind("}") + 1
    if start_obj >= 0 and end_obj > start_obj:
        try:
            return json.loads(text[start_obj:end_obj])
        except json.JSONDecodeError:
            pass

    start_arr = text.find("[")
    end_arr = text.rfind("]") + 1
    if start_arr >= 0 and end_arr > start_arr:
        try:
            return json.loads(text[start_arr:end_arr])
        except json.JSONDecodeError:
            pass

    return None


def short_id(from_string: str | None = None) -> str:
    alphabet = string.digits + string.ascii_letters
    base = len(alphabet)

    if from_string is None:
        num = uuid.uuid4().int
    else:
        hash_bytes = hashlib.sha1(from_string.encode()).digest()[:16]
        num = int.from_bytes(hash_bytes, "big")

    min_chars = 18

    chars: list[str] = []
    while num > 0 or len(chars) < min_chars:
        num, rem = divmod(num, base)
        chars.append(alphabet[rem])

    return "".join(reversed(chars))[:min_chars]


class classproperty(Generic[_T]):
    def __init__(self, fget: Callable[..., _T]) -> None:
        self.fget = fget

    @overload
    def __get__(self, obj: None, cls: type) -> _T: ...
    @overload
    def __get__(self, obj: object, cls: type) -> _T: ...
    def __get__(self, obj: object | None, cls: type) -> _T:
        return self.fget(cls)
