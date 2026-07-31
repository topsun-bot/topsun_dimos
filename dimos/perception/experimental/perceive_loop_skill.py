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

from datetime import datetime, timezone
import json
import os
from threading import RLock
import time
from typing import TYPE_CHECKING, Any

from dimos.agents.agent_spec import AgentSpec
from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.models.vl.create import create
from dimos.msgs.sensor_msgs.Image import Image, sharpness_window
from dimos.perception.detection.detectors.person.yolo import YoloPersonDetector
from dimos.utils.logging_config import setup_logger
from dimos.utils.reactive import backpressure

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase

    from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
    from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D


logger = setup_logger()


class PerceiveLoopSkill(Module):
    color_image: In[Image]

    _agent_spec: AgentSpec
    _period: float = 0.1  # seconds - how often to run the perceive loop

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._vl_model = create(self.config.g.detection_model)
        self._yolo_detector: YoloPersonDetector | None = None
        self._active_lookout: tuple[str, ...] = ()
        self._then: dict[str, Any] | None = None
        self._continuous: bool = False
        self._use_yolo: bool = False
        self._cooldown: float = 10.0
        self._last_trigger_time: float = 0.0
        self._lookout_subscription: DisposableBase | None = None
        self._model_started: bool = False
        self._lock = RLock()

    @rpc
    def start(self) -> None:
        super().start()

    @rpc
    def stop(self) -> None:
        self._stop_lookout()
        super().stop()

    @skill
    def look_out_for(
        self,
        description_of_things: list[str],
        then: dict[str, Any] | None = None,
        continuous: bool = False,
        cooldown: float = 10.0,
        use_yolo: bool = False,
    ) -> str:
        """This tool will look out for things matching the description in the
        input list, and notify the agent whenever it finds a match.

        You can ask it for `look_out_for(["small dogs", "cats"])` and you will be
        notified back whenever such a detection is made.

        Optionally, you can specify a `then` parameter to automatically execute
        another tool when a match is found, without waiting for the agent to
        process the notification. This is useful for time-sensitive actions like
        following a detected person.

        The `then` parameter is a dict with:
        - "tool": name of the tool to call (e.g. "follow_person")
        - "args": dict of arguments to pass to the tool

        In the args, you can use template variables that will be replaced with
        detection data:
        - "$bbox": the bounding box [x1, y1, x2, y2] of the best detection
        - "$label": the label/name of the detection
        - "$image": base64-encoded JPEG of the frame the detection was made on

        Args:
            description_of_things: List of things to look for.
            then: Optional tool to call automatically when a match is found.
            continuous: If True, keep monitoring after a match instead of stopping.
                The action will be triggered repeatedly with a cooldown interval.
            cooldown: Minimum seconds between triggers in continuous mode.
            use_yolo: If True, use local YOLO model for fast person detection
                instead of VLM. Much faster (~30ms vs ~3s) but only detects
                COCO classes (person, car, dog, etc.), not arbitrary descriptions.

        Example:
            look_out_for(["person"], then={
                "tool": "speak_cached",
                "args": {"text": "Person detected"}
            }, continuous=True, cooldown=10.0, use_yolo=True)
        """

        with self._lock:
            if self._active_lookout:
                return (
                    f"Already looking for something else ({self._active_lookout}). "
                    "Cancel the current lookout with the `stop_looking_out` tool"
                )

            sharpest = backpressure(
                sharpness_window(1.0 / self._period, self.color_image.pure_observable())
            )
            self._use_yolo = use_yolo
            if use_yolo:
                if self._yolo_detector is None:
                    self._yolo_detector = YoloPersonDetector()
                    logger.info("PerceiveLoopSkill: YOLO detector initialized")
            else:
                self._vl_model.start()
            self._model_started = True
            self._active_lookout = tuple(description_of_things)
            self._then = then
            self._continuous = continuous
            self._cooldown = cooldown
            self._last_trigger_time = 0.0
            self._lookout_subscription = sharpest.subscribe(
                on_next=self._on_image,
                on_error=lambda e: logger.exception("Error in perceive loop", exc_info=e),
            )
        self.start_tool("look_out_for")

        detector = "YOLO (fast)" if use_yolo else "VLM"
        mode = "continuously (repeating)" if continuous else "once"
        return (
            f"Started looking for {json.dumps(description_of_things)} ({mode}, {detector}). "
            "This will run until you stop it by calling the `stop_looking_out` "
            "tool. Note that it can be intensive, so please cancel when you don't "
            "need to use it in order to save resources."
        )

    @skill
    def stop_looking_out(self) -> str:
        """Stop looking out. Use this to end `look_out_for` tool calls."""
        with self._lock:
            active_lookout_str = json.dumps(self._active_lookout)
            self._stop_lookout()
        return f"Stopped looking out for {active_lookout_str}"

    def _on_image(self, image: Image) -> None:
        with self._lock:
            if not self._active_lookout:
                return
            active_lookout = self._active_lookout
            active_lookout_str = json.dumps(active_lookout)
            continuous = self._continuous
            cooldown = self._cooldown
            last_trigger = self._last_trigger_time
            use_yolo = self._use_yolo

        t0 = time.monotonic()
        if use_yolo:
            detections = self._detect_with_yolo(image, active_lookout)
        else:
            detections = self._vl_model.query_detections(image, active_lookout_str)
        detect_ms = (time.monotonic() - t0) * 1000
        if not detections:
            return
        logger.info(f"Detection took {detect_ms:.0f}ms, found {len(detections.detections)} objects")

        now = time.monotonic()
        if continuous and (now - last_trigger) < cooldown:
            return

        if os.environ.get("DEBUG"):
            _write_debug_image(image, detections)

        best = max(detections.detections, key=lambda d: d.bbox_2d_volume())
        continuation_context: dict[str, Any] = {
            "bbox": list(best.bbox),
            "label": best.name,
            "image": image.to_base64(quality=70),
        }

        if continuous:
            with self._lock:
                if not self._active_lookout:
                    return
                self._last_trigger_time = now
                then = self._then
            logger.info(
                "Lookout matched (continuous), dispatching",
                lookout=active_lookout_str,
                detection_label=best.name,
            )
            if then is not None:
                self._agent_spec.dispatch_continuation(then, continuation_context)
            else:
                self.tool_update(
                    "look_out_for",
                    f"Found a match for {active_lookout_str}. Please announce audibly.",
                )
        else:
            with self._lock:
                if not self._active_lookout:
                    return
                if self._lookout_subscription is not None:
                    self._lookout_subscription.dispose()
                    self._lookout_subscription = None
                self._active_lookout = ()
                then = self._then
                self._then = None
                self._vl_model.stop()
                self._model_started = False

            if then is None:
                self.tool_update(
                    "look_out_for",
                    f"Found a match for {active_lookout_str}. Please announce audibly.",
                )
                self.stop_tool("look_out_for")
                return

            self.stop_tool("look_out_for")
            logger.info(
                "Lookout matched, dispatching continuation",
                lookout=active_lookout_str,
                continuation=then,
                detection=continuation_context,
            )
            self._agent_spec.dispatch_continuation(then, continuation_context)

    def _detect_with_yolo(
        self, image: Image, lookout_labels: tuple[str, ...]
    ) -> ImageDetections2D[Detection2DBBox] | None:
        """Use YOLO for fast local detection, filtering by requested labels."""
        from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D

        if self._yolo_detector is None:
            return None
        all_detections = self._yolo_detector.process_image(image)
        lower_labels = {l.lower() for l in lookout_labels}
        matched = [
            d for d in all_detections.detections if d.name and d.name.lower() in lower_labels
        ]
        if not matched:
            return None
        result = ImageDetections2D(image)
        result.detections = matched
        return result

    def _stop_lookout(self) -> None:
        with self._lock:
            if self._lookout_subscription is not None:
                self._lookout_subscription.dispose()
                self._lookout_subscription = None
            self._active_lookout = ()
            self._then = None
            self._continuous = False
            self._use_yolo = False
            self._last_trigger_time = 0.0
            if self._model_started:
                if not self._use_yolo:
                    self._vl_model.stop()
                self._model_started = False
        self.stop_tool("look_out_for")


def _write_debug_image(image: Image, detections: ImageDetections2D[Detection2DBBox]) -> None:
    import cv2

    try:
        debug_img = image.to_opencv().copy()
        for det in detections.detections:
            x1, y1, x2, y2 = (int(v) for v in det.bbox)
            cv2.rectangle(debug_img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                debug_img,
                det.name,
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )
        ts = datetime.now(tz=timezone.utc).isoformat().replace(":", "-")
        cv2.imwrite(f"debug-{ts}.ignore.jpg", debug_img)
    except Exception:
        pass  # Ignore debug drawing errors
