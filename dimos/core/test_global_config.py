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

"""Tests for GlobalConfig security defaults and Feishu local TOML."""

import os
from pathlib import Path

from pytest import MonkeyPatch


class TestGlobalConfigSecurityDefaults:
    """Network services must bind to localhost by default (not 0.0.0.0)."""

    def test_listen_host_defaults_to_localhost(self) -> None:
        from dimos.core.global_config import GlobalConfig

        config = GlobalConfig()
        assert config.listen_host == "127.0.0.1", (
            f"listen_host must default to 127.0.0.1, got {config.listen_host}"
        )


class TestGlobalConfigFeishuLocalToml:
    """dimos.local.toml [feishu] vs .env vs process env (see global_config module doc)."""

    def test_local_toml_overrides_dotenv(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        from dimos.core.global_config import DIMOS_LOCAL_CONFIG_FILENAME, GlobalConfig

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("FEISHU_WEBHOOK_URL=http://from-dotenv\n", encoding="utf-8")
        (tmp_path / DIMOS_LOCAL_CONFIG_FILENAME).write_text(
            '[feishu]\nwebhook_url = "http://from-toml"\n',
            encoding="utf-8",
        )
        monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("DIMOS_FEISHU_WEBHOOK_URL", raising=False)
        cfg = GlobalConfig()
        assert cfg.feishu_webhook_url == "http://from-toml"

    def test_process_env_overrides_local_toml(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        from dimos.core.global_config import GlobalConfig

        monkeypatch.chdir(tmp_path)
        (tmp_path / "dimos.local.toml").write_text(
            '[feishu]\nwebhook_url = "http://from-toml"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("FEISHU_WEBHOOK_URL", "http://from-env")
        cfg = GlobalConfig()
        assert cfg.feishu_webhook_url == "http://from-env"

    def test_dimos_local_config_env_path(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
        from dimos.core.global_config import GlobalConfig

        p = tmp_path / "custom.toml"
        p.write_text('[feishu]\nwebhook_url = "http://custom-path"\n', encoding="utf-8")
        monkeypatch.setenv("DIMOS_LOCAL_CONFIG", os.fspath(p))
        monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
        cfg = GlobalConfig()
        assert cfg.feishu_webhook_url == "http://custom-path"
