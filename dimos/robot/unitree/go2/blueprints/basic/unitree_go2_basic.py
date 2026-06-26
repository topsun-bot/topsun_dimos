#!/usr/bin/env python3

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

import logging
import platform
from typing import Any

from dimos.constants import DEFAULT_CAPACITY_COLOR_IMAGE
from dimos.core.coordination.blueprints import autoconnect
from dimos.core.global_config import global_config
from dimos.core.transport import pSHMTransport
from dimos.msgs.sensor_msgs.Image import Image
from dimos.protocol.pubsub.impl.lcmpubsub import LCM
from dimos.protocol.pubsub.impl.shmpubsub import PickleSharedMemory
from dimos.protocol.service.system_configurator.clock_sync import ClockSyncConfigurator
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.visualization.vis_module import vis_module

_logger = logging.getLogger(__name__)

# Mac has some issue with high bandwidth UDP, so we use pSHMTransport for color_image
# actually we can use pSHMTransport for all platforms, and for all streams
# TODO need a global transport toggle on blueprints/global config
_mac_transports: dict[tuple[str, type], pSHMTransport[Image]] = {
    ("color_image", Image): pSHMTransport(
        "color_image", default_capacity=DEFAULT_CAPACITY_COLOR_IMAGE
    ),
}

_transports_base = (
    autoconnect() if platform.system() == "Linux" else autoconnect().transports(_mac_transports)
)


class _ColorImageSHMSubscriber:
    """Expose the macOS color_image shared-memory stream to the Rerun bridge."""

    def __init__(self) -> None:
        self.shm: PickleSharedMemory | None = None

    def start(self) -> None:
        self.shm = PickleSharedMemory(default_capacity=DEFAULT_CAPACITY_COLOR_IMAGE)
        self.shm.start()

        import hashlib

        cap = int(self.shm.config.default_capacity)
        h = hashlib.blake2b(f"color_image:{cap}".encode(), digest_size=8).hexdigest()
        _logger.info(
            "ColorImageSHMSubscriber started: capacity=%d (DEFAULT_CAPACITY_COLOR_IMAGE) "
            "segment=psm_%s_data",
            cap,
            h,
        )

    def stop(self) -> None:
        if self.shm:
            self.shm.stop()
            self.shm = None

    def subscribe_all(self, callback: Any) -> Any:
        if self.shm is None:
            self.start()

        seen = {"count": 0}

        def on_color_image(msg: Any, _: str) -> None:
            seen["count"] += 1
            if seen["count"] <= 3 or seen["count"] % 60 == 0:
                data = getattr(msg, "data", None)
                shape = getattr(data, "shape", None)
                dtype = getattr(data, "dtype", None)
                fmt = getattr(msg, "format", None)
                _logger.info(
                    "color_image SHM frame #%d shape=%s dtype=%s format=%s",
                    seen["count"],
                    shape,
                    dtype,
                    fmt,
                )
            callback(msg, "/color_image")

        return self.shm.subscribe("color_image", on_color_image)


def _convert_camera_info(camera_info: Any) -> Any:
    return camera_info.to_rerun(
        image_topic="/world/color_image",
        optical_frame="camera_optical",
    )


def _convert_global_map(grid: Any) -> Any:
    return grid.to_rerun(bottom_cutoff=0)


def _convert_navigation_costmap(grid: Any) -> Any:
    return grid.to_rerun(
        colormap="Accent",
        z_offset=0.015,
        opacity=0.2,
        background="#484981",
    )


def _static_base_link(rr: Any) -> list[Any]:
    return [
        rr.Boxes3D(
            half_sizes=[0.35, 0.155, 0.2],
            colors=[(0, 255, 127)],
        ),
        rr.Transform3D(parent_frame="tf#/base_link"),
    ]


def _go2_rerun_blueprint() -> Any:
    """Split layout: camera feed + 3D world view side by side."""
    import rerun as rr
    import rerun.blueprint as rrb

    return rrb.Blueprint(
        rrb.Horizontal(
            rrb.Spatial2DView(origin="world/color_image", name="Camera"),
            rrb.Spatial3DView(
                origin="world",
                name="3D",
                background=rrb.Background(kind="SolidColor", color=[0, 0, 0]),
                line_grid=rrb.LineGrid3D(
                    plane=rr.components.Plane3D.XY.with_distance(0.5),
                ),
                overrides={
                    "world/lidar": rrb.EntityBehavior(visible=False),
                },
            ),
            column_shares=[1, 2],
        ),
        rrb.TimePanel(state="hidden"),
        rrb.SelectionPanel(state="hidden"),
    )


rerun_config = {
    "blueprint": _go2_rerun_blueprint,
    # macOS publishes color_image over pSHMTransport so we add a SHM subscriber
    # alongside the default LCM one.
    "pubsubs": [LCM(), _ColorImageSHMSubscriber()] if platform.system() != "Linux" else [LCM()],
    "visual_override": {
        "world/camera_info": _convert_camera_info,
        "world/global_map": _convert_global_map,
        "world/merged_map": _convert_global_map,
        "world/navigation_costmap": _convert_navigation_costmap,
    },
    "max_hz": {
        "world/global_map": 2,  # source ~7.8 Hz
        "world/color_image": 5,  # source ~14 Hz
        "world/global_costmap": 2,  # source ~7.6 Hz
    },
    "memory_limit": "2GB",
    "static": {
        "world/tf/base_link": _static_base_link,
    },
}

_with_vis = vis_module(
    viewer_backend=global_config.viewer,
    rerun_config=rerun_config,
    foxglove_config={"shm_channels": ["/color_image#sensor_msgs.Image"]},
)

unitree_go2_basic = (
    autoconnect(
        _transports_base,
        _with_vis,
        GO2Connection.blueprint(),
    ).global_config(n_workers=4, robot_model="unitree_go2")
    # we temporarily disabled sensor timestamps
    # and are derriving all timestmaps upon reception
    # this is because image webrtc stream doesn't have timestamps,
    # so it's difficult to corelate the streams otherwise
    #
    #    .configurators(ClockSyncConfigurator())
)

__all__ = [
    "unitree_go2_basic",
]
