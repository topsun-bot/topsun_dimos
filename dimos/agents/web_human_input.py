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

from __future__ import annotations

import json
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, Any

from fastapi import Request
from fastapi.responses import JSONResponse
import reactivex as rx
import reactivex.operators as ops

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT, STATE_DIR
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.core.transport import PubSubTransport
from dimos.core.transport_factory import make_transport
from dimos.msgs.sensor_msgs.Image import Image
from dimos.stream.audio.node_normalizer import AudioNormalizer
from dimos.types.door_memory_spec import SpatialLandmarkMemorySpec
from dimos.types.spatial_record import RecordType, SpatialRecord
from dimos.utils.logging_config import setup_logger
from dimos.web.robot_web_interface import RobotWebInterface

if TYPE_CHECKING:
    from dimos.stream.audio.base import AudioEvent

logger = setup_logger()

_LANDMARK_DB_PATH = STATE_DIR / "landmark_memory" / "landmarks.json"


def _image_to_bgr(frame: Any) -> Any:
    """Convert DimOS Image (or ndarray) to OpenCV BGR for MJPEG encoding."""
    if isinstance(frame, Image):
        return frame.to_opencv()
    if hasattr(frame, "to_opencv"):
        return frame.to_opencv()
    if hasattr(frame, "data"):
        return frame.data
    return frame


def _record_to_item(rec: SpatialRecord | dict[str, Any]) -> dict[str, Any]:
    if isinstance(rec, SpatialRecord):
        data = rec.to_dict()
    else:
        data = dict(rec)
    pos = data.get("position") or (0.0, 0.0, 0.0)
    return {
        "name": data.get("name") or "",
        "record_type": data.get("record_type") or "",
        "record_id": data.get("record_id") or "",
        "position": [float(pos[0]), float(pos[1]), float(pos[2]) if len(pos) > 2 else 0.0],
        "observation_count": int(data.get("observation_count") or 0),
        "description": data.get("description") or "",
        "state": data.get("state") or "",
        "metadata": data.get("metadata") or {},
    }


def _load_landmarks_from_disk() -> list[SpatialRecord]:
    path = Path(_LANDMARK_DB_PATH)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read landmark memory from %s", path)
        return []
    if not isinstance(raw, list):
        return []
    records: list[SpatialRecord] = []
    for item in raw:
        try:
            records.append(SpatialRecord.from_dict(item))
        except Exception:
            logger.exception("Skipping invalid landmark record")
    return records


class WebInput(Module):
    """Browser chat + spatial memory console on http://localhost:5555."""

    color_image: In[Image]
    _landmark_memory: SpatialLandmarkMemorySpec | None

    _web_interface: RobotWebInterface | None = None
    _thread: Thread | None = None
    _human_transport: PubSubTransport[str] | None = None

    def _list_records(self, record_type: RecordType | None = None) -> list[dict[str, Any]]:
        records: list[SpatialRecord]
        memory = self._landmark_memory
        if memory is not None:
            try:
                if record_type is None:
                    records = memory.get_all()
                else:
                    records = memory.query_by_type(record_type)
            except Exception:
                logger.exception("Landmark memory RPC failed; falling back to disk")
                records = _load_landmarks_from_disk()
                if record_type is not None:
                    records = [r for r in records if r.record_type == record_type]
        else:
            records = _load_landmarks_from_disk()
            if record_type is not None:
                records = [r for r in records if r.record_type == record_type]

        items = [_record_to_item(r) for r in records]
        items.sort(key=lambda x: (-x["observation_count"], x["name"]))
        return items

    def _register_spatial_routes(self) -> None:
        assert self._web_interface is not None
        app = self._web_interface.app
        query_subject = self._web_interface.query_subject

        @app.get("/spatial/rooms")
        async def spatial_rooms() -> JSONResponse:
            return JSONResponse({"items": self._list_records(RecordType.ROOM)})

        @app.get("/spatial/objects")
        async def spatial_objects() -> JSONResponse:
            return JSONResponse({"items": self._list_records(RecordType.LANDMARK)})

        @app.get("/spatial/doors")
        async def spatial_doors() -> JSONResponse:
            return JSONResponse({"items": self._list_records(RecordType.DOOR)})

        @app.get("/spatial/all")
        async def spatial_all() -> JSONResponse:
            return JSONResponse(
                {
                    "rooms": self._list_records(RecordType.ROOM),
                    "objects": self._list_records(RecordType.LANDMARK),
                    "doors": self._list_records(RecordType.DOOR),
                }
            )

        @app.post("/spatial/find")
        async def spatial_find(request: Request) -> JSONResponse:
            data = await request.json()
            name = str(data.get("name") or "").strip()
            if not name:
                return JSONResponse(
                    {"success": False, "message": "请提供要找的物品名称"},
                    status_code=400,
                )
            command = f"去找{name}"
            query_subject.on_next(command)
            return JSONResponse({"success": True, "message": f"已请求：{command}", "command": command})

        @app.post("/spatial/goto")
        async def spatial_goto(request: Request) -> JSONResponse:
            data = await request.json()
            name = str(data.get("name") or "").strip()
            if not name:
                return JSONResponse(
                    {"success": False, "message": "请提供目标名称"},
                    status_code=400,
                )
            command = f"导航到{name}"
            query_subject.on_next(command)
            return JSONResponse({"success": True, "message": f"已请求：{command}", "command": command})

        @app.post("/spatial/detect")
        async def spatial_detect() -> JSONResponse:
            command = "请检测当前画面中的物体并记住它们"
            query_subject.on_next(command)
            return JSONResponse({"success": True, "message": f"已请求：{command}", "command": command})

    @rpc
    def start(self) -> None:
        super().start()

        self._human_transport = make_transport("/human_input")

        audio_subject: rx.subject.Subject[AudioEvent] = rx.subject.Subject()

        camera_stream = self.color_image.pure_observable().pipe(
            ops.map(_image_to_bgr),
            ops.filter(lambda frame: frame is not None),
            ops.share(),
        )

        self._web_interface = RobotWebInterface(
            port=5555,
            text_streams={"agent_responses": rx.subject.Subject()},
            audio_subject=audio_subject,
            color_image=camera_stream,
        )
        self._register_spatial_routes()

        normalizer = AudioNormalizer()

        # Here to prevent unwanted imports in the file.
        from dimos.stream.audio.stt.node_whisper import WhisperNode

        stt_node = WhisperNode()

        # Connect audio pipeline: browser audio → normalizer → whisper
        normalizer.consume_audio(audio_subject.pipe(ops.share()))
        stt_node.consume_audio(normalizer.emit_audio())

        # Subscribe to both text input sources
        # 1. Direct text from web interface
        unsub = self._web_interface.query_stream.subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        # 2. Transcribed text from STT
        unsub = stt_node.emit_text().subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        self._thread = Thread(target=self._web_interface.run, daemon=True)
        self._thread.start()

        logger.info("Web interface started at http://localhost:5555")

    @rpc
    def stop(self) -> None:
        if self._web_interface:
            self._web_interface.shutdown()
        if self._thread:
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
        if self._human_transport:
            self._human_transport.stop()
        super().stop()
