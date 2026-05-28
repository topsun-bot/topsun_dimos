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

import numpy as np

from dimos.perception.object_tracker_2d import ObjectTracker2D


class DummyTracker:
    def init(self, frame, bbox):
        return None


class DummyFailedTracker:
    def __init__(self, init_result):
        self._init_result = init_result

    def init(self, frame, bbox):
        return self._init_result


class _ClearingBBox:
    def __init__(self, tracker: ObjectTracker2D):
        self._tracker = tracker

    def __iter__(self):
        self._tracker._last_bbox = None
        return iter((1, 2, 3, 4))


def test_track_accepts_none_return_from_init(monkeypatch):
    tracker = ObjectTracker2D()
    try:
        tracker._latest_rgb_frame = np.zeros((120, 160, 3), dtype=np.uint8)
        tracker._start_tracking_thread = lambda: None

        monkeypatch.setattr(
            "dimos.perception.object_tracker_2d._create_opencv_tracker",
            lambda: DummyTracker(),
        )

        result = tracker.track([20, 30, 80, 90])

        assert result["status"] == "tracking_started"
        assert tracker.tracking_initialized is True
        assert tracker.tracking_bbox == (20, 30, 60, 60)
    finally:
        tracker.stop()


def test_track_rejects_falsey_init_result(monkeypatch):
    tracker = ObjectTracker2D()
    try:
        tracker._latest_rgb_frame = np.zeros((120, 160, 3), dtype=np.uint8)
        tracker._start_tracking_thread = lambda: None

        monkeypatch.setattr(
            "dimos.perception.object_tracker_2d._create_opencv_tracker",
            lambda: DummyFailedTracker(0),
        )

        result = tracker.track([20, 30, 80, 90])

        assert result["status"] == "init_failed"
        assert tracker.tracking_initialized is False
    finally:
        tracker.stop()


def test_track_rejects_numpy_false_init_result(monkeypatch):
    tracker = ObjectTracker2D()
    try:
        tracker._latest_rgb_frame = np.zeros((120, 160, 3), dtype=np.uint8)
        tracker._start_tracking_thread = lambda: None

        monkeypatch.setattr(
            "dimos.perception.object_tracker_2d._create_opencv_tracker",
            lambda: DummyFailedTracker(np.bool_(False)),
        )

        result = tracker.track([20, 30, 80, 90])

        assert result["status"] == "init_failed"
        assert tracker.tracking_initialized is False
    finally:
        tracker.stop()


def test_get_latest_bbox_uses_single_snapshot_when_tracker_stops():
    tracker = ObjectTracker2D()
    try:
        tracker._last_bbox = _ClearingBBox(tracker)

        assert tracker.get_latest_bbox() == [1.0, 2.0, 3.0, 4.0]
        assert tracker._last_bbox is None
    finally:
        tracker.stop()
