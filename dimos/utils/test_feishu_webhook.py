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

from __future__ import annotations

from dimos.utils.feishu_webhook import send_feishu_text


def test_send_feishu_text_success_statuscode(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"StatusCode": 0, "StatusMessage": "success"}
    post = mocker.patch("dimos.utils.feishu_webhook.requests.post", return_value=mock_resp)

    ok = send_feishu_text("https://example.com/hook", "hello")

    assert ok is True
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://example.com/hook"
    assert kwargs["json"]["msg_type"] == "text"
    assert kwargs["json"]["content"]["text"] == "hello"


def test_send_feishu_text_success_code_field(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"code": 0}
    mocker.patch("dimos.utils.feishu_webhook.requests.post", return_value=mock_resp)

    assert send_feishu_text("https://example.com/hook", "x") is True


def test_send_feishu_text_http_error(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 500
    mock_resp.text = "err"
    mocker.patch("dimos.utils.feishu_webhook.requests.post", return_value=mock_resp)

    assert send_feishu_text("https://example.com/hook", "x") is False


def test_send_feishu_text_includes_sign_when_secret(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"code": 0}
    post = mocker.patch("dimos.utils.feishu_webhook.requests.post", return_value=mock_resp)

    send_feishu_text("https://example.com/hook", "msg", secret="mysecret")

    payload = post.call_args.kwargs["json"]
    assert "timestamp" in payload
    assert "sign" in payload
    assert len(payload["sign"]) > 0
