# NoMaD 依赖安装与排错

本文档整理 `examples/nav-go2` 在 DimOS 环境中运行 NoMaD 推理时常见的依赖问题、原因与修复步骤。

NoMaD 相关 Python 包**不在** DimOS 主 `pyproject.toml` 中，需在**与 `dimos` / `go2_nomad_nav.py` 相同的 venv** 里，按 [visualnav-transformer](https://github.com/robodhruv/visualnav-transformer) 额外安装。

---

## 依赖一览

`engine/nomad/inference.py` 在 `NoMaDEngine.initialize()` 中会加载：

| 组件 | 包 / 路径 | 用途 |
|------|-----------|------|
| `torch` | DimOS 已装 | 推理设备与张量 |
| `yaml` | PyYAML（DimOS 已装） | 读取 `nomad.yaml` |
| `diffusers` | 需额外安装 | `DDPMScheduler` 扩散采样 |
| `vint_train` | `pip install -e <VISUALNAV_ROOT>/train/` | `get_action` 等训练工具 |
| `utils` | `<VISUALNAV_ROOT>/deployment/src`（运行时加入 `sys.path`） | `load_model`、`transform_images` |
| `diffusion_policy` | `pip install -e diffusion_policy/` | `ConditionalUnet1D`（`load_model` 内部） |

可选（`vint_train` 导入链可能用到）：

- `wandb`
- `efficientnet-pytorch`、`vit-pytorch`、`positional-encodings`、`lmdb` 等（见 upstream `train/train_environment.yml`）

---

## 前置：visualnav-transformer 与权重

```bash
git clone https://github.com/robodhruv/visualnav-transformer.git ~/work/visualnav-transformer
# 将 nomad.pth 放到 deployment/model_weights/ 或 examples/nav-go2/models/nomad/
```

环境变量（写入 `~/.bashrc` 或每次运行前 `export`）：

```bash
export VISUALNAV_ROOT=/home/sgk/work/visualnav-transformer
export DIFFUSION_POLICY_ROOT=/home/sgk/work/diffusion_policy
# 权重路径写在 examples/nav-go2/config/nomad_nav.yaml 的 checkpoint_path（也可用 NOMAD_CHECKPOINT 环境变量兜底）
# 可选，默认会找 $VISUALNAV_ROOT/train/config/nomad.yaml
export NOMAD_MODEL_CONFIG=$VISUALNAV_ROOT/train/config/nomad.yaml
```

---

## 推荐安装步骤（topsun_dimos venv）

在仓库根目录、已激活 `.venv` 的前提下：

```bash
cd /home/sgk/work/topsun_dimos
source .venv/bin/activate

export VISUALNAV_ROOT=/home/sgk/work/visualnav-transformer
export DIFFUSION_POLICY_ROOT=/home/sgk/work/diffusion_policy

# 1. visualnav / vint_train 的 pip 依赖（一次性，避免 prettytable、wandb 等逐个报错）
uv pip install -r examples/nav-go2/requirements-nomad.txt

# 2. visualnav 训练包（提供 vint_train）
uv pip install -e "$VISUALNAV_ROOT/train/"

# 3. diffusion_policy 仓库（见下文第 7 节；仅需 clone + DIFFUSION_POLICY_ROOT）
git clone https://github.com/real-stanford/diffusion_policy.git ~/work/diffusion_policy
```

`requirements-nomad.txt` 对应 upstream `train/train_environment.yml` 的 pip 段（`diffusers` 版本已按 DimOS 调整，见第 2 节）。

---

## 一键验证

```bash
cd /home/sgk/work/topsun_dimos
export VISUALNAV_ROOT=/home/sgk/work/visualnav-transformer

uv run python -c "
from diffusers.schedulers.scheduling_ddpm import DDPMScheduler
import sys
sys.path.insert(0, f'{__import__(\"os\").environ[\"VISUALNAV_ROOT\"]}/train')
sys.path.insert(0, f'{__import__(\"os\").environ[\"VISUALNAV_ROOT\"]}/deployment/src')
from vint_train.training.train_utils import get_action
from utils import load_model
from diffusion_policy.model.diffusion.conditional_unet1d import ConditionalUnet1D
print('NoMaD 依赖检查通过')
"
```

通过后运行示例：

```bash
uv run python examples/nav-go2/go2_nomad_nav.py --simulation --viewer rerun
```

日志中不应再出现 `NoMaD inference skipped: Missing NoMaD dependencies`。

---

## 常见问题与修复

### 1. `No module named 'diffusers'`

**现象**

```
NoMaD inference skipped: Missing NoMaD dependencies (No module named 'diffusers'). ...
```

**原因**  
DimOS 默认 `uv sync` 不会安装 `diffusers`。

**修复**

```bash
uv pip install "diffusers>=0.27,<0.32"
```

---

### 2. `cannot import name 'cached_download' from 'huggingface_hub'`

**现象**

```text
ImportError: cannot import name 'cached_download' from 'huggingface_hub'
Did you mean: 'hf_hub_download'?
```

**原因**  
visualnav 文档中的 `diffusers==0.11.1` 依赖旧版 `huggingface_hub.cached_download`；DimOS 环境里 `huggingface_hub` 通常为 **0.30+**（由 `transformers` 等带入），二者不兼容。

| 包 | 冲突组合 |
|----|----------|
| `diffusers` | 0.11.1 ❌ |
| `huggingface_hub` | 0.36.x（DimOS 常见）✅ |

**修复（推荐）**  
升级 `diffusers`，**不要**降级 `huggingface_hub`（会影响 DimOS 的 `transformers` / LangChain）：

```bash
uv pip install "diffusers>=0.27,<0.32"
```

已验证 `diffusers==0.31.0` 可正常 `from diffusers.schedulers.scheduling_ddpm import DDPMScheduler`，且与 NoMaD 调用参数兼容。

**不推荐**

```bash
# 勿在 DimOS venv 中强行安装 diffusers==0.11.1
uv pip install diffusers==0.11.1
```

---

### 3. `SyntaxWarning: invalid escape sequence '\s'`（diffusers）

**现象**  
安装或 import 旧版 `diffusers` 时出现多条 `SyntaxWarning`（`dynamic_modules_utils.py`）。

**原因**  
旧版 diffusers 在 Python 3.12 下的正则字符串写法告警。

**处理**  
可忽略；升级到 `diffusers>=0.27` 后通常消失。不影响功能时无需单独处理。

---

### 4. `No module named 'prettytable'`（或其它 vint 依赖）

**现象**

```
Missing NoMaD dependencies (No module named 'prettytable')
```

（同理可能出现 `wandb`、`efficientnet_pytorch`、`vit_pytorch` 等。）

**原因**  
`from vint_train.training.train_utils import get_action` 会加载整个 `train_utils.py`，其顶层依赖包括 `prettytable`、`wandb`、`matplotlib` 等（见 visualnav `train/train_environment.yml`）。

**修复（推荐，一次性）**

```bash
uv pip install -r examples/nav-go2/requirements-nomad.txt
uv pip install -e "$VISUALNAV_ROOT/train/"
```

或只补当前缺的包：

```bash
uv pip install prettytable
```

---

### 6. `No module named 'utils'`

**现象**

```
Missing NoMaD dependencies (No module named 'utils')
```

**原因**  
`utils` 来自 `$VISUALNAV_ROOT/deployment/src/utils.py`，不是 pip 包名。若 `VISUALNAV_ROOT` 未设置、路径错误，或 `inference.py` 在把 `deployment/src` 加入 `sys.path` **之前** 就 `import utils`，会误报为缺依赖。

**修复**

1. 确认环境变量与目录：

   ```bash
   export VISUALNAV_ROOT=/home/sgk/work/visualnav-transformer
   test -f "$VISUALNAV_ROOT/deployment/src/utils.py" && echo OK
   ```

2. 使用已修复导入顺序的 `engine/nomad/inference.py`（先 `_setup_python_path`，再 `from utils import ...`）。

3. 若仍失败，检查是否与其它顶层包名 `utils` 冲突；保证 `deployment/src` 在 `sys.path` 靠前。

---

### 7. `No module named 'sensor_msgs'`

**现象**

```
Missing NoMaD dependencies (No module named 'sensor_msgs')
```

**原因**

1. `from utils import load_model, ...` 会加载 visualnav 的 `deployment/src/utils.py` **整个模块**。
2. 该文件第 8 行在**模块顶层**写死：

   ```python
   from sensor_msgs.msg import Image  # ROS 1 消息类型
   ```

3. `sensor_msgs` 属于 **ROS 1**（需 `rospy` / `genpy` 或 [rospypi](https://github.com/rospypi/simple)），**不是** DimOS 里的 `dimos.msgs.sensor_msgs`。
4. NoMaD 在 DimOS 里实际只用 `load_model`、`transform_images`、`to_numpy`（PIL → torch），**不用** `msg_to_pil` / `pil_to_msg`（那俩才依赖 ROS `Image`）。但 Python 在 import `utils` 时仍会执行顶层 `sensor_msgs` 导入，因此在无 ROS 的 venv 里报错。

**修复（推荐，已内置）**

`engine/nomad/inference.py` 在 import `utils` 前会注册轻量 **stub**，避免为推理单独装 ROS。

**备选（上游 ROS 部署环境）**

若你要跑 visualnav 自带的 `deployment/src/explore.py`（真机 ROS），再装 ROS 或 rospypi：

```bash
uv pip install --extra-index-url https://rospypi.github.io/simple/ sensor-msgs genpy
```

DimOS + `go2_nomad_nav.py` 路径**不需要**这一步。

---

### 8. `No module named 'diffusion_policy'`

**原因**

1. `utils.load_model` 会 `from diffusion_policy.model.diffusion.conditional_unet1d import ConditionalUnet1D`。
2. 需把 **[real-stanford/diffusion_policy](https://github.com/real-stanford/diffusion_policy)** 仓库克隆到本机。
3. 仅执行 `uv pip install -e ~/work/diffusion_policy/` **常常不够**：该仓库顶层包无 `__init__.py`，`setuptools.find_packages()` 可能得到空列表，editable 安装的 `MAPPING` 为空，Python 仍找不到 `diffusion_policy`。

**修复（推荐）**

克隆仓库并设置环境变量（与 `VISUALNAV_ROOT` 同级目录即可）：

```bash
git clone https://github.com/real-stanford/diffusion_policy.git ~/work/diffusion_policy
export DIFFUSION_POLICY_ROOT=/home/sgk/work/diffusion_policy
```

`engine/nomad/inference.py` 会把该目录加入 `sys.path`（无需可编辑安装也能 import）。

**可选：可编辑安装（若你希望 pip 能识别）**

```bash
touch ~/work/diffusion_policy/diffusion_policy/__init__.py
uv pip install -e ~/work/diffusion_policy/
```

---

### 9. DimOS 主进程 `import torch` 失败（CUDA 动态库）

**现象**（运行 `dimos run` 或加载含 `memory2`/embedding 的 blueprint 时）

```text
ImportError: libcudnn.so.9: cannot open shared object file
ImportError: libcusparseLt.so.0: cannot open shared object file
```

**说明**  
这与 NoMaD **无直接关系**，是 GPU 版 `torch` 所需的 NVIDIA wheel（`nvidia-cudnn-cu12`、`nvidia-cusparselt-cu12` 等）未完整解压到 venv。

**修复**

```bash
cd /home/sgk/work/topsun_dimos
uv pip install --reinstall \
  nvidia-cudnn-cu12 nvidia-cusparselt-cu12 nvidia-nccl-cu12 nvidia-nvshmem-cu12
```

安装后确认：

```bash
ls .venv/lib/python3.12/site-packages/nvidia/cudnn/lib/libcudnn.so.9
ls .venv/lib/python3.12/site-packages/nvidia/cusparselt/lib/libcusparseLt.so.0
uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

大包下载中断会导致「有 dist-info、无 `.so`」；需 `--reinstall` 补全。

---

## 版本建议（DimOS + Python 3.12）

| 包 | 建议版本 | 说明 |
|----|----------|------|
| `diffusers` | `>=0.27,<0.32` | 与当前 `huggingface_hub` 兼容 |
| `huggingface_hub` | 保持 DimOS 解析结果 | 勿为迁就 0.11.1 而降级 |
| `diffusers` | **避免** `==0.11.1` | 仅适用于独立 conda 老环境 |
| `torch` | DimOS 已锁定 | 与 CUDA wheel 一并维护 |

---

## 与独立 conda 环境的关系

visualnav 官方文档建议：

```bash
conda env create -f train/train_environment.yml
conda activate nomad_train  # 或 vint_train
pip install -e train/
```

该环境为 **Python 3.8 + 旧 diffusers**，与 DimOS **Python 3.12** venv **不要混用**。

本仓库推荐：**在 topsun_dimos `.venv` 中按本文安装**，保证 `go2_nomad_nav.py` 与 `ModuleCoordinator` 使用同一解释器。

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `engine/nomad/inference.py` | 依赖检查与错误文案来源 |
| `engine/nomad/config.py` | `VISUALNAV_ROOT` / checkpoint 路径解析 |
| `go2_nomad_nav.py` | 入口与 CLI |
| [README.md](./README.md) | 架构与运行方式 |

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-05-20 | 初版：diffusers / huggingface_hub 冲突、安装步骤与 CUDA wheel 排错 |
| 2026-05-20 | 补充 `No module named 'utils'`；修复 inference 中 sys.path 顺序 |
| 2026-05-20 | 补充 `sensor_msgs` 原因；inference 增加 ROS stub |
