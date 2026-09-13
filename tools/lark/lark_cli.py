#!/usr/bin/env python3
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

"""Minimal Feishu / Lark CLI.

Zero external dependencies (stdlib only) so it can be dropped into any
repo without touching the main pyproject.toml.

Usage
-----
    # 1) export credentials (or put them in tools/lark/.env)
    export LARK_APP_ID=cli_xxxxxxxxxxxxxxxx
    export LARK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    # 2) sanity check that the app credentials work
    python tools/lark/lark_cli.py verify

    # 3) resolve a wiki node URL and dump its body
    python tools/lark/lark_cli.py wiki-get YmjJw5BJAi2E3Tkv1INcbAtrnFb

    # or paste the full URL, the script will pull the token out
    python tools/lark/lark_cli.py wiki-get \\
        https://ycn9htpxsusl.feishu.cn/wiki/YmjJw5BJAi2E3Tkv1INcbAtrnFb

    # list child nodes of a space / parent node
    python tools/lark/lark_cli.py wiki-tree <space_id> [parent_node_token]

Required app permissions (in Feishu open platform → 权限管理):
    - wiki:wiki:readonly       (Wiki  read)
    - docx:document:readonly   (新版文档 read)
    - docs:doc:readonly        (老版文档 read, optional)
    - drive:drive:readonly     (file metadata, optional)

The wiki space owner must also add the app as a member of the space
(Feishu wiki → 知识库设置 → 成员管理 → 添加应用).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

LARK_HOST = os.environ.get("LARK_HOST", "https://open.feishu.cn")
ENV_FILE = Path(__file__).with_name(".env")


# --------------------------------------------------------------------------- #
# tiny .env loader (no python-dotenv dependency)
# --------------------------------------------------------------------------- #
def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_env_file(ENV_FILE)


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = None
    headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - network
        payload = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} on {url}\n{payload}") from None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        raise SystemExit(f"Non-JSON response from {url}:\n{payload}") from None


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
_token_cache: dict[str, Any] = {"token": None, "expires": 0.0}


def get_tenant_access_token() -> str:
    """Cache-aware tenant_access_token fetch."""
    if _token_cache["token"] and _token_cache["expires"] - 60 > time.time():
        return _token_cache["token"]  # type: ignore[return-value]

    app_id = os.environ.get("LARK_APP_ID")
    app_secret = os.environ.get("LARK_APP_SECRET")
    if not app_id or not app_secret:
        raise SystemExit(
            "Missing LARK_APP_ID / LARK_APP_SECRET.\n"
            "Set them in your shell or in tools/lark/.env "
            "(copy from .env.example)."
        )

    res = _request(
        "POST",
        f"{LARK_HOST}/open-apis/auth/v3/tenant_access_token/internal",
        body={"app_id": app_id, "app_secret": app_secret},
    )
    if res.get("code") != 0:
        raise SystemExit(f"auth failed: {res}")
    _token_cache["token"] = res["tenant_access_token"]
    _token_cache["expires"] = time.time() + float(res.get("expire", 7000))
    return res["tenant_access_token"]


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_tenant_access_token()}"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_WIKI_URL_RE = re.compile(r"/wiki/([A-Za-z0-9]+)")


def extract_node_token(value: str) -> str:
    """Accept either a raw token or a full wiki URL."""
    m = _WIKI_URL_RE.search(value)
    return m.group(1) if m else value


# --------------------------------------------------------------------------- #
# wiki API wrappers
# --------------------------------------------------------------------------- #
def wiki_get_node(token: str) -> dict[str, Any]:
    """Resolve a wiki node token → (obj_token, obj_type, title, ...)."""
    res = _request(
        "GET",
        f"{LARK_HOST}/open-apis/wiki/v2/spaces/get_node",
        params={"token": token},
        headers=_auth_headers(),
    )
    if res.get("code") != 0:
        raise SystemExit(f"get_node failed: {res}")
    return res["data"]["node"]


def docx_raw_content(doc_id: str) -> str:
    res = _request(
        "GET",
        f"{LARK_HOST}/open-apis/docx/v1/documents/{doc_id}/raw_content",
        headers=_auth_headers(),
    )
    if res.get("code") != 0:
        raise SystemExit(f"docx raw_content failed: {res}")
    return res["data"]["content"]


def doc_legacy_content(doc_token: str) -> str:
    res = _request(
        "GET",
        f"{LARK_HOST}/open-apis/doc/v2/{doc_token}/raw_content",
        headers=_auth_headers(),
    )
    if res.get("code") != 0:
        raise SystemExit(f"legacy doc fetch failed: {res}")
    return res["data"]["content"]


def wiki_create_node(
    space_id: str,
    *,
    title: str,
    obj_type: str = "docx",
    parent_node_token: str | None = None,
    node_type: str = "origin",
) -> dict[str, Any]:
    """Create a wiki node. Requires `wiki:wiki` (write) scope."""
    body: dict[str, Any] = {
        "obj_type": obj_type,
        "node_type": node_type,
        "title": title,
    }
    if parent_node_token:
        body["parent_node_token"] = parent_node_token
    res = _request(
        "POST",
        f"{LARK_HOST}/open-apis/wiki/v2/spaces/{space_id}/nodes",
        body=body,
        headers=_auth_headers(),
    )
    if res.get("code") != 0:
        raise SystemExit(f"create_node failed: {res}")
    return res["data"]["node"]


def wiki_delete_node(space_id: str, node_token: str) -> dict[str, Any]:
    """Delete a wiki node. Requires `wiki:wiki` (write) scope."""
    res = _request(
        "DELETE",
        f"{LARK_HOST}/open-apis/wiki/v2/spaces/{space_id}/nodes/{node_token}",
        headers=_auth_headers(),
    )
    if res.get("code") != 0:
        raise SystemExit(f"delete_node failed: {res}")
    return res.get("data", {})


def wiki_list_children(space_id: str, parent_node_token: str | None = None) -> list[dict[str, Any]]:
    params = {"page_size": "50"}
    if parent_node_token:
        params["parent_node_token"] = parent_node_token
    out: list[dict[str, Any]] = []
    while True:
        res = _request(
            "GET",
            f"{LARK_HOST}/open-apis/wiki/v2/spaces/{space_id}/nodes",
            params=params,
            headers=_auth_headers(),
        )
        if res.get("code") != 0:
            raise SystemExit(f"list nodes failed: {res}")
        data = res["data"]
        out.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        params["page_token"] = data["page_token"]
    return out


# --------------------------------------------------------------------------- #
# CLI commands
# --------------------------------------------------------------------------- #
def cmd_verify(_: list[str]) -> int:
    token = get_tenant_access_token()
    print(f"OK: obtained tenant_access_token (len={len(token)}, host={LARK_HOST})")
    print("Next: try `wiki-get <node_token>` to read a doc.")
    return 0


def cmd_wiki_get(args: list[str]) -> int:
    if not args:
        print("usage: lark_cli.py wiki-get <node_token_or_url> [--meta]")
        return 2
    token = extract_node_token(args[0])
    meta_only = "--meta" in args[1:]

    node = wiki_get_node(token)
    print(f"# {node.get('title', '(untitled)')}")
    print(f"  node_token : {node.get('node_token')}")
    print(f"  obj_token  : {node.get('obj_token')}")
    print(f"  obj_type   : {node.get('obj_type')}")
    print(f"  space_id   : {node.get('space_id')}")
    print(f"  has_child  : {node.get('has_child')}")
    print()

    if meta_only:
        return 0

    obj_type = node.get("obj_type")
    obj_token = node.get("obj_token")
    if obj_type == "docx":
        body = docx_raw_content(obj_token)
    elif obj_type == "doc":
        body = doc_legacy_content(obj_token)
    else:
        print(f"(no fetcher implemented for obj_type={obj_type}; supported: docx, doc)")
        return 0

    print("--- content ---")
    print(body)
    return 0


def cmd_wiki_tree(args: list[str]) -> int:
    if not args:
        print("usage: lark_cli.py wiki-tree <space_id> [parent_node_token]")
        return 2
    space_id = args[0]
    parent = args[1] if len(args) > 1 else None
    nodes = wiki_list_children(space_id, parent)
    for n in nodes:
        marker = "[D]" if n.get("has_child") else "   "
        print(
            f"{marker} {n['node_token']:32s}  {n.get('obj_type', '-'):8s}"
            f"  {n.get('title', '(untitled)')}"
        )
    print(f"\ntotal: {len(nodes)} node(s)")
    return 0


def _try(label: str, fn):  # type: ignore[no-untyped-def]
    """Run fn(), print PASS/FAIL line, return (ok, value_or_error)."""
    try:
        value = fn()
    except SystemExit as exc:
        msg = str(exc).replace("\n", " ")[:200]
        print(f"  [FAIL] {label}  → {msg}")
        return False, msg
    print(f"  [ OK ] {label}")
    return True, value


def cmd_wiki_rw_test(args: list[str]) -> int:
    """Read + non-destructive write probe against a wiki node."""
    if not args:
        print("usage: lark_cli.py wiki-rw-test <node_url_or_token>")
        return 2
    target_token = extract_node_token(args[0])
    print(f"Target node token: {target_token}\n")

    # --- AUTH ---
    print("[1/5] auth")
    ok, _ = _try("tenant_access_token", get_tenant_access_token)
    if not ok:
        return 1

    # --- READ: resolve node ---
    print("\n[2/5] read · resolve node metadata (wiki/v2/spaces/get_node)")
    ok, node = _try("get_node", lambda: wiki_get_node(target_token))
    if not ok:
        print(
            "\nRESULT: read=FAIL  write=SKIPPED "
            "(can't read this node — app not in wiki space, or "
            "missing wiki scope, or wrong node token)"
        )
        return 1
    space_id = node.get("space_id")
    obj_type = node.get("obj_type")
    print(f"        title='{node.get('title')}' obj_type={obj_type} space_id={space_id}")

    # --- READ: fetch doc body when possible ---
    print("\n[3/5] read · fetch document body")
    if obj_type == "docx":
        ok_body, body = _try("docx raw_content", lambda: docx_raw_content(node["obj_token"]))
    elif obj_type == "doc":
        ok_body, body = _try("doc raw_content", lambda: doc_legacy_content(node["obj_token"]))
    else:
        ok_body, body = True, None
        print(
            f"  [skip] obj_type={obj_type} — body fetch not implemented "
            "(read-meta still counts as a read)"
        )
    if ok_body and isinstance(body, str):
        preview = body[:120].replace("\n", " ⏎ ")
        print(f"        preview: {preview!r}{'…' if len(body) > 120 else ''}")

    read_ok = ok and ok_body

    # --- WRITE PROBE: create a temp child node ---
    print("\n[4/5] write · create temporary child node (wiki/v2/spaces/{space_id}/nodes)")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    probe_title = f"[lark-cli rw-test {stamp} — safe to delete]"
    ok_create, created = _try(
        "create_node",
        lambda: wiki_create_node(
            space_id,
            title=probe_title,
            obj_type="docx",
            parent_node_token=target_token,
        ),
    )

    write_ok = False
    cleanup_ok = True
    if ok_create:
        print(
            f"        created node_token={created.get('node_token')} title='{created.get('title')}'"
        )
        write_ok = True

        # --- CLEANUP: delete the probe ---
        print(
            "\n[5/5] cleanup · delete the probe node "
            "(DELETE wiki/v2/spaces/{space_id}/nodes/{token})"
        )
        cleanup_ok, _ = _try(
            "delete_node",
            lambda: wiki_delete_node(space_id, created["node_token"]),
        )
        if not cleanup_ok:
            print(
                f"  ⚠ probe node '{probe_title}' was NOT deleted — "
                "please remove it manually in the wiki UI."
            )
    else:
        print("\n[5/5] cleanup · skipped (nothing to delete)")

    # --- SUMMARY ---
    print("\n" + "=" * 60)
    print(f"  READ   : {'PASS' if read_ok else 'FAIL'}")
    print(
        f"  WRITE  : {'PASS' if write_ok else 'FAIL'}"
        + ("" if cleanup_ok else "  (cleanup failed — see above)")
    )
    print("=" * 60)

    if not write_ok:
        print(
            "\nHint: write needs `wiki:wiki` (NOT readonly) scope AND "
            "the app must have edit/admin rights on this wiki space."
        )
    return 0 if (read_ok and write_ok and cleanup_ok) else 1


COMMANDS = {
    "verify": cmd_verify,
    "wiki-get": cmd_wiki_get,
    "wiki-tree": cmd_wiki_tree,
    "wiki-rw-test": cmd_wiki_rw_test,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 0
    cmd, rest = argv[1], argv[2:]
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"unknown command: {cmd}\n")
        print("available: " + ", ".join(COMMANDS))
        return 2
    return fn(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
