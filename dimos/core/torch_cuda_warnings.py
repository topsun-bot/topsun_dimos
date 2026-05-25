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

"""Suppress repeated PyTorch CUDA forward-compat (Error 804) UserWarnings."""

from __future__ import annotations

import os
import re
import warnings

_TORCH_CUDA_804_MESSAGE = "CUDA initialization: Unexpected error from cudaGetDeviceCount"
_torch_cuda_804_seen = False
_filters_installed = False


def configure_torch_cuda_warning_filters() -> None:
    """Hide duplicate torch.cuda init Error 804 warnings in CLI and worker processes."""
    global _filters_installed
    if _filters_installed:
        return
    _filters_installed = True

    escaped = re.escape(_TORCH_CUDA_804_MESSAGE)
    if os.environ.get("CUDA_VISIBLE_DEVICES") is not None:
        warnings.filterwarnings(
            "ignore",
            message=f".*{escaped}.*",
            category=UserWarning,
            module=r"torch\.cuda",
        )
        return

    original_showwarning = warnings.showwarning

    def _showwarning_once(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: object | None = None,
        line: str | None = None,
    ) -> None:
        global _torch_cuda_804_seen
        text = str(message)
        if category is UserWarning and _TORCH_CUDA_804_MESSAGE in text and "Error 804" in text:
            if _torch_cuda_804_seen:
                return
            _torch_cuda_804_seen = True
            warnings.filterwarnings(
                "ignore",
                message=f".*{escaped}.*",
                category=UserWarning,
                module=r"torch\.cuda",
            )
        original_showwarning(message, category, filename, lineno, file, line)

    warnings.showwarning = _showwarning_once  # type: ignore[assignment]
