# MiniMax TTS Guide

This guide documents the working MiniMax TTS flow used in `HappyLearn`.
It uses the non-streaming HTTP T2A API, receives hex-encoded MP3 audio, and
decodes it to a local `.mp3` file.

## Result File

The generated test audio in this workspace is:

```text
/home/lenovo/working/dimos-auto-test/dog_bark_female_tianmei_speech28hd_retry.mp3
```

## Working API Shape

Endpoint:

```text
POST https://api.minimaxi.com/v1/t2a_v2
```

Headers:

```text
Authorization: Bearer $MINIMAX_API_KEY
Content-Type: application/json
```

Payload used successfully:

```json
{
  "model": "speech-2.8-hd",
  "text": "汪汪 汪汪 汪汪 汪汪汪",
  "stream": false,
  "voice_setting": {
    "voice_id": "female-tianmei",
    "speed": 1.0,
    "vol": 1.0,
    "pitch": 0,
    "text_normalization": true
  },
  "audio_setting": {
    "sample_rate": 32000,
    "bitrate": 256000,
    "format": "mp3",
    "channel": 1
  },
  "language_boost": "Chinese"
}
```

Important details:

- Current confirmed voice: `female-tianmei`, MiniMax system voice name `甜美女性音色`.
- Current confirmed model: `speech-2.8-hd`.
- Do not pass `emotion` for this working flow.
- Do not request URL output.
- Read `data.audio` from the JSON response.
- `data.audio` is hex-encoded MP3 bytes.
- Decode the hex string with `bytes.fromhex(...)`.

## Shell Test

Set the key in the environment first:

```bash
export MINIMAX_API_KEY='replace-with-your-key'
```

Call the API:

```bash
curl -sS -o minimax_tts_response.json \
  https://api.minimaxi.com/v1/t2a_v2 \
  -H "Authorization: Bearer ${MINIMAX_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "speech-2.8-hd",
    "text": "汪汪 汪汪 汪汪 汪汪汪",
    "stream": false,
    "voice_setting": {
      "voice_id": "female-tianmei",
      "speed": 1.0,
      "vol": 1.0,
      "pitch": 0,
      "text_normalization": true
    },
    "audio_setting": {
      "sample_rate": 32000,
      "bitrate": 256000,
      "format": "mp3",
      "channel": 1
    },
    "language_boost": "Chinese"
  }'
```

Decode the MP3:

```bash
python3 - <<'PY'
import json
from pathlib import Path

response = json.loads(Path("minimax_tts_response.json").read_text())
base = response.get("base_resp") or {}
if base.get("status_code") != 0:
    raise SystemExit(f"MiniMax error: {base}")

audio_hex = response["data"]["audio"].strip().replace(" ", "")
Path("dog_bark_minimax.mp3").write_bytes(bytes.fromhex(audio_hex))
print("wrote dog_bark_minimax.mp3")
PY
```

Verify:

```bash
file dog_bark_minimax.mp3
```

Expected type:

```text
Audio file with ID3 version 2.4.0, contains: MPEG ADTS, layer III, 256 kbps, 32 kHz, Monaural
```

## Minimal Python Function

```python
from __future__ import annotations

import os
from pathlib import Path

import requests


def synthesize_minimax_mp3(text: str, output_path: str | Path) -> Path:
    api_key = os.environ["MINIMAX_API_KEY"]
    output_path = Path(output_path)

    payload = {
        "model": "speech-2.8-hd",
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": "female-tianmei",
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
            "text_normalization": True,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 256000,
            "format": "mp3",
            "channel": 1,
        },
        "language_boost": "Chinese",
    }

    response = requests.post(
        "https://api.minimaxi.com/v1/t2a_v2",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()

    data = response.json()
    base = data.get("base_resp") or {}
    if base.get("status_code") != 0:
        raise RuntimeError(f"MiniMax error: {base}")

    audio_hex = data["data"]["audio"].strip().replace(" ", "")
    output_path.write_bytes(bytes.fromhex(audio_hex))
    return output_path


if __name__ == "__main__":
    path = synthesize_minimax_mp3("汪汪 汪汪 汪汪 汪汪汪", "dog_bark_minimax.mp3")
    print(path)
```

## Source Reference

This flow matches the TTS implementation in:

```text
https://github.com/chenrui-1024/HappyLearn/blob/main/app/minimax_tts.py
```
