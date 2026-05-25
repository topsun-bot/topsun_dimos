#!/usr/bin/env bash
# Print NVIDIA driver mismatch recovery steps (no system changes).
set -euo pipefail

echo "NVIDIA driver/library version mismatch or NVML init failure detected."
echo
echo "Recommended fix:"
echo "  1. Save work and run: sudo reboot"
echo "  2. After reboot, verify: nvidia-smi"
echo "  3. Retry MuJoCo popup (see dimos/experimental/navigation_obstacle_mvp/README.md):"
echo '     GDK_BACKEND=x11 MUJOCO_GL=glfw CUDA_VISIBLE_DEVICES="" uv run dimos --simulation mujoco \'
echo "       --mujoco-offscreen-gl off --viewer none run unitree-go2"
echo
echo "Do NOT run modprobe -r nvidia* on a live desktop session (risk of black screen)."
echo "Diagnose: uv run python -m dimos.simulation.mujoco.diagnose --shell"
