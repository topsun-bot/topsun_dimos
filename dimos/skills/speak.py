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

# 语音播报技能：将文本经 TTS 节点转为音频并顺序播放。

import queue
import threading
import time
from typing import Any

from pydantic import Field
from reactivex import Subject

from dimos.skills.skills import AbstractSkill
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# 全局锁：同一时刻只允许一路音频占用播放设备
_audio_device_lock = threading.RLock()

# 全局队列：所有 Speak 请求按入队顺序依次执行，避免并发抢设备
_audio_queue = queue.Queue()  # type: ignore[var-annotated]
_queue_processor_thread = None
_queue_running = False


# 后台线程：从队列取出任务并串行执行。
def _process_audio_queue() -> None:
    """Process audio requests sequentially on a background thread."""
    global _queue_running

    while _queue_running:
        try:
            # 带超时取任务，便于在空闲时仍能响应 _queue_running 变化
            task = _audio_queue.get(timeout=1.0)
            if task is None:  # 哨兵值，用于优雅停止消费线程
                break

            # 每个 task 是 speak_task 闭包，内部完成 TTS 与播放等待
            task()
            _audio_queue.task_done()

        except queue.Empty:
            continue
        except Exception as e:
            logger.error(f"Error in audio queue processor: {e}")


# 启动音频队列消费线程（模块导入时自动调用一次）。
def start_audio_queue_processor() -> None:
    """Start the background thread for processing audio requests."""
    global _queue_processor_thread, _queue_running

    if _queue_processor_thread is None or not _queue_processor_thread.is_alive():
        _queue_running = True
        _queue_processor_thread = threading.Thread(
            target=_process_audio_queue, daemon=True, name="AudioQueueProcessor"
        )
        _queue_processor_thread.start()
        logger.info("Started audio queue processor thread")


# 模块加载时即启动队列线程，后续 Speak 调用只需 put 任务即可
start_audio_queue_processor()


class Speak(AbstractSkill):
    """Speak text out loud to humans nearby or to other robots."""

    text: str = Field(..., description="Text to speak")

    def __init__(self, tts_node: Any | None = None, **data) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**data)
        self._tts_node = tts_node  # 外部注入的 TTS 节点，负责文本→音频管线
        self._audio_complete = threading.Event()  # TTS 播放结束信号
        self._subscription = None
        self._subscriptions: list = []  # type: ignore[type-arg]  # 保存 Rx 订阅，便于 dispose

    def __call__(self):  # type: ignore[no-untyped-def]
        if not self._tts_node:
            logger.error("No TTS node provided to Speak skill")
            return "Error: No TTS node available"

        # 在队列线程与调用线程之间传递执行结果（成功/失败字符串）
        result_queue = queue.Queue(1)  # type: ignore[var-annotated]

        # 实际播报逻辑，由全局音频队列线程调用。
        def speak_task() -> None:
            try:
                with _audio_device_lock:
                    text_subject = Subject()  # type: ignore[var-annotated]
                    self._audio_complete.clear()
                    self._subscriptions = []

                    def on_complete() -> None:
                        logger.info(f"TTS audio playback completed for: {self.text}")
                        self._audio_complete.set()

                    def on_error(error) -> None:  # type: ignore[no-untyped-def]
                        logger.error(f"Error in TTS processing: {error}")
                        self._audio_complete.set()

                    # 将文本 Subject 接入 TTS 消费端
                    self._tts_node.consume_text(text_subject)  # type: ignore[union-attr]

                    # 订阅 emit 流，在 on_completed/on_error 时唤醒等待
                    self._subscription = self._tts_node.emit_text().subscribe(  # type: ignore[union-attr]
                        on_next=lambda text: logger.debug(f"TTS processing: {text}"),
                        on_completed=on_complete,
                        on_error=on_error,
                    )
                    self._subscriptions.append(self._subscription)

                    text_subject.on_next(self.text)
                    text_subject.on_completed()  # 通知 TTS：文本已全部送入

                    # 超时与文本长度挂钩，避免长句过早判定失败
                    timeout = max(5, len(self.text) * 0.1)
                    logger.debug(f"Waiting for TTS completion with timeout {timeout:.1f}s")

                    if not self._audio_complete.wait(timeout=timeout):
                        logger.warning(f"TTS timeout reached for: {self.text}")
                    else:
                        # 播放结束后短暂等待，确保底层音频缓冲排空
                        time.sleep(0.3)

                    for sub in self._subscriptions:
                        if sub:
                            sub.dispose()
                    self._subscriptions = []

                    result_queue.put(f"Spoke: {self.text} successfully")
            except Exception as e:
                logger.error(f"Error in speak task: {e}")
                result_queue.put(f"Error speaking text: {e!s}")

        display_text = self.text[:50] + "..." if len(self.text) > 50 else self.text
        logger.info(f"Queueing speech task: '{display_text}'")
        _audio_queue.put(speak_task)

        # 调用方阻塞等待队列线程回传结果；超时略大于 speak_task 内部估算
        try:
            text_len_timeout = len(self.text) * 0.15  # 约 150ms/字
            max_timeout = max(10, text_len_timeout)

            return result_queue.get(timeout=max_timeout)
        except queue.Empty:
            logger.error("Timed out waiting for speech task to complete")
            return f"Error: Timed out while speaking: {self.text}"
