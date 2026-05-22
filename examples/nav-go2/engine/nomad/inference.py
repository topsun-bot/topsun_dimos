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

"""NoMaD exploration inference (goal-masked diffusion), adapted from visualnav-transformer."""

from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

from engine.nomad.config import NoMaDConfig
from PIL import Image as PILImage
from trajectory_inference import TrajectoryInferenceResult, TrajectoryNavigationRuntimeError

NoMaDInferenceResult = TrajectoryInferenceResult
NoMaDRuntimeError = TrajectoryNavigationRuntimeError


class NoMaDEngine:
    """Lazy-loaded NoMaD model using visualnav-transformer deployment code."""

    def __init__(self, config: NoMaDConfig) -> None:
        self._config = config
        self._context: deque[PILImage.Image] = deque(maxlen=32)
        self._context_size: int | None = None
        self._model_params: dict[str, Any] | None = None
        self._model: Any = None
        self._noise_scheduler: Any = None
        self._device: Any = None
        self._ready = False
        self._init_error: str | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def init_error(self) -> str | None:
        return self._init_error

    def _setup_python_path(self, visualnav_root: Path) -> None:
        train_dir = visualnav_root / "train"
        deploy_src = visualnav_root / "deployment" / "src"
        for path in (train_dir, deploy_src):
            text = str(path)
            if path.is_dir() and text not in sys.path:
                sys.path.insert(0, text)
        self._setup_diffusion_policy_path(visualnav_root)

    @staticmethod
    def _setup_diffusion_policy_path(visualnav_root: Path) -> None:
        """Add diffusion_policy repo root to sys.path.

        ``pip install -e diffusion_policy`` often registers an empty editable mapping
        (no top-level ``__init__.py``). Cloning the repo and setting
        ``DIFFUSION_POLICY_ROOT`` is enough when the tree contains
        ``diffusion_policy/model/``.
        """
        if "diffusion_policy" in sys.modules:
            return

        candidates: list[Path] = []
        env_root = os.environ.get("DIFFUSION_POLICY_ROOT", "").strip()
        if env_root:
            candidates.append(Path(env_root).expanduser().resolve())
        candidates.extend(
            [
                visualnav_root.parent / "diffusion_policy",
                Path.home() / "work" / "diffusion_policy",
            ]
        )

        for repo in candidates:
            if not (repo / "diffusion_policy" / "model").is_dir():
                continue
            text = str(repo)
            if text not in sys.path:
                sys.path.insert(0, text)
            return

    @staticmethod
    def _stub_ros_sensor_msgs() -> None:
        """Stub ROS ``sensor_msgs`` so visualnav ``utils`` can import without rospy.

        Upstream ``deployment/src/utils.py`` does ``from sensor_msgs.msg import Image``
        at import time for ROS deployment scripts (``msg_to_pil`` / ``pil_to_msg``).
        NoMaD inference only uses ``load_model``, ``transform_images``, and ``to_numpy``
        (PIL/torch path) — not ROS messages.
        """
        if "sensor_msgs.msg" in sys.modules:
            return

        class _RosImageStub:
            """Unused by NoMaDEngine; satisfies ``utils`` module-level import only."""

        sensor_msgs = ModuleType("sensor_msgs")
        msg_mod = ModuleType("sensor_msgs.msg")
        msg_mod.Image = _RosImageStub
        sensor_msgs.msg = msg_mod
        sys.modules["sensor_msgs"] = sensor_msgs
        sys.modules["sensor_msgs.msg"] = msg_mod

    def initialize(self) -> None:
        if self._ready:
            return
        if self._init_error is not None:
            raise NoMaDRuntimeError(self._init_error)

        root = self._config.resolved_visualnav_root()
        ckpt = self._config.resolved_checkpoint()
        model_yaml = self._config.resolved_model_config()

        if root is None:
            self._init_error = (
                "VISUALNAV_ROOT not set. Clone visualnav-transformer and export "
                "VISUALNAV_ROOT=/path/to/vint_release"
            )
            raise NoMaDRuntimeError(self._init_error)
        if ckpt is None or not ckpt.is_file():
            self._init_error = f"NoMaD checkpoint not found (tried {ckpt})"
            raise NoMaDRuntimeError(self._init_error)
        if model_yaml is None or not model_yaml.is_file():
            self._init_error = f"NoMaD model config not found (tried {model_yaml})"
            raise NoMaDRuntimeError(self._init_error)

        # deployment/src (utils) and train/ (vint_train) must be on sys.path before imports.
        self._setup_python_path(root)
        self._stub_ros_sensor_msgs()

        try:
            from diffusers.schedulers.scheduling_ddpm import DDPMScheduler
            import torch
            from utils import load_model, to_numpy, transform_images  # type: ignore[import-untyped]
            from vint_train.training.train_utils import get_action  # type: ignore[import-untyped]
            import yaml
        except ImportError as exc:
            self._init_error = (
                f"Missing NoMaD dependencies ({exc}). Install: "
                "uv pip install -e $VISUALNAV_ROOT/train/; "
                "clone https://github.com/real-stanford/diffusion_policy and set "
                "DIFFUSION_POLICY_ROOT=/path/to/diffusion_policy (repo root, sibling of "
                "visualnav-transformer is OK). "
                f"VISUALNAV_ROOT={root}."
            )
            raise NoMaDRuntimeError(self._init_error) from exc

        with model_yaml.open(encoding="utf-8") as f:
            model_params = yaml.safe_load(f)

        self._context_size = int(model_params["context_size"])
        self._context = deque(maxlen=self._context_size + 2)

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = load_model(str(ckpt), model_params, device)
        model = model.to(device)
        model.eval()

        num_diffusion_iters = self._config.num_diffusion_iters
        if num_diffusion_iters is None:
            num_diffusion_iters = int(model_params["num_diffusion_iters"])

        noise_scheduler = DDPMScheduler(
            num_train_timesteps=num_diffusion_iters,
            beta_schedule="squaredcos_cap_v2",
            clip_sample=True,
            prediction_type="epsilon",
        )

        self._torch = torch
        self._transform_images = transform_images
        self._get_action = get_action
        self._to_numpy = to_numpy
        self._model_params = model_params
        self._model = model
        self._noise_scheduler = noise_scheduler
        self._device = device
        self._ready = True

    def push_observation(self, pil_image: PILImage.Image) -> None:
        if self._context_size is None:
            needed = 4
        else:
            needed = self._context_size + 1
        if len(self._context) < needed:
            self._context.append(pil_image)
        else:
            self._context.popleft()
            self._context.append(pil_image)

    def context_ready(self) -> bool:
        if self._context_size is None:
            return len(self._context) > 3
        return len(self._context) > self._context_size

    def infer_exploration(self) -> NoMaDInferenceResult:
        if not self._ready:
            self.initialize()
        assert self._model is not None
        assert self._model_params is not None
        assert self._noise_scheduler is not None
        assert self._device is not None

        if not self.context_ready():
            raise NoMaDRuntimeError("Not enough image context for NoMaD inference")

        torch = self._torch
        model = self._model
        model_params = self._model_params
        noise_scheduler = self._noise_scheduler
        device = self._device

        obs_images = self._transform_images(
            list(self._context), model_params["image_size"], center_crop=False
        )
        obs_images = obs_images.to(device)
        goal_img, goal_mask = self._goal_condition(model_params["image_size"])

        num_samples = self._config.num_samples
        num_diffusion_iters = self._config.num_diffusion_iters
        if num_diffusion_iters is None:
            num_diffusion_iters = int(model_params["num_diffusion_iters"])

        with torch.no_grad():
            obs_cond = model(
                "vision_encoder",
                obs_img=obs_images,
                goal_img=goal_img,
                input_goal_mask=goal_mask,
            )
            goal_distance = self._predict_goal_distance(obs_cond, goal_mask)
            if len(obs_cond.shape) == 2:
                obs_cond = obs_cond.repeat(num_samples, 1)
            else:
                obs_cond = obs_cond.repeat(num_samples, 1, 1)

            naction = torch.randn(
                (num_samples, model_params["len_traj_pred"], 2), device=device
            )
            noise_scheduler.set_timesteps(num_diffusion_iters)
            for k in noise_scheduler.timesteps[:]:
                noise_pred = model(
                    "noise_pred_net",
                    sample=naction,
                    timestep=k,
                    global_cond=obs_cond,
                )
                naction = noise_scheduler.step(
                    model_output=noise_pred,
                    timestep=k,
                    sample=naction,
                ).prev_sample

            actions = self._to_numpy(self._get_action(naction))

        if model_params.get("normalize", True):
            scale = self._config.max_v / self._config.frame_rate
            actions = actions * scale

        waypoint_index = self._config.waypoint_index
        chosen = actions[0, waypoint_index].copy()
        return NoMaDInferenceResult(
            trajectories=actions,
            chosen_waypoint=chosen,
            goal_distance=goal_distance,
        )

    def _predict_goal_distance(self, obsgoal_cond: Any, goal_mask: Any) -> float | None:
        if self._config.inference_mode != "navigate":
            return None

        torch = self._torch
        if bool(torch.any(goal_mask != 0).item()):
            return None

        dists = self._model("dist_pred_net", obsgoal_cond=obsgoal_cond)
        return float(self._to_numpy(dists.flatten())[0])

    def _goal_condition(self, image_size: tuple[int, int]) -> tuple[Any, Any]:
        torch = self._torch
        device = self._device

        if self._config.inference_mode == "explore":
            goal_img = torch.randn((1, 3, *image_size), device=device)
            goal_mask = torch.ones(1, dtype=torch.long, device=device)
            return goal_img, goal_mask

        goal_path = self._config.resolved_goal_image()
        if goal_path is None:
            raise NoMaDRuntimeError("navigate mode requires goal_image_path in nomad_nav.yaml")
        if not goal_path.is_file():
            raise NoMaDRuntimeError(f"Goal image not found: {goal_path}")

        goal_pil = PILImage.open(goal_path).convert("RGB")
        goal_img = self._transform_images([goal_pil], image_size, center_crop=False).to(device)
        goal_mask = torch.zeros(1, dtype=torch.long, device=device)
        return goal_img, goal_mask
