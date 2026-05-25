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

"""Record Go2 RGB images to disk for nav-go2 data collection."""

from __future__ import annotations

from pathlib import Path
import time

from reactivex.disposable import Disposable
from trajectory_inference import dimos_image_to_pil

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In
from dimos.msgs.sensor_msgs.Image import Image
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

DEFAULT_RECORD_IMAGE_DIR = Path(__file__).resolve().parent / "recordings" / "images"


def save_record_image(image: Image, output_dir: Path, saved_count: int) -> Path:
    """Convert a DimOS image to PIL RGB and save it as a timestamped PNG."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp_ns = int(float(image.ts) * 1_000_000_000)
    output_path = output_dir / f"{timestamp_ns}_{saved_count:06d}.png"
    pil_image = dimos_image_to_pil(image)
    pil_image.save(output_path)
    return output_path


class RecordImageConfig(ModuleConfig):
    """Configuration for periodic RGB image recording."""

    output_dir: str = str(DEFAULT_RECORD_IMAGE_DIR)
    interval_s: float = 1.0


class RecordImageModule(Module):
    """Save incoming ``color_image`` frames as PNG files at a fixed interval."""

    config: RecordImageConfig
    color_image: In[Image]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._last_saved_monotonic = float("-inf")
        self._saved_count = 0

    @rpc
    def start(self) -> None:
        super().start()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_image)))
        logger.info(
            "RecordImageModule started (output_dir=%s, interval=%.2fs)",
            self.output_dir,
            self.config.interval_s,
        )

    @property
    def output_dir(self) -> Path:
        return Path(self.config.output_dir).expanduser().resolve()

    def _on_image(self, image: Image) -> None:
        now = time.monotonic()
        if now - self._last_saved_monotonic < self.config.interval_s:
            return

        output_path = save_record_image(image, self.output_dir, self._saved_count)

        self._last_saved_monotonic = now
        self._saved_count += 1
        logger.info("Saved record image %s", output_path)
