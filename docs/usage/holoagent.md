# HoloAgent integration (HorizonRobotics)

This document maps [HorizonRobotics/HoloAgent](https://github.com/HorizonRobotics/HoloAgent)
onto DimOS and records what we actually merged. HoloAgent is Apache-2.0 and
explicitly acknowledges DimOS; it is a ROS 2 + OpenClaw stack with a
DimOS-inspired skill/blueprint layer, not a drop-in replacement for this repo.

Do **not** vendor the HoloAgent tree. HoloAgent bundles Navigation2, FAST-LIVO
(GPL/BSD license conflict — see HoloAgent `THIRD_PARTY_NOTICES.md`), OVO,
SAM, and robot-specific ROS packages. Topsun Go2/G1 already have DimOS-native
equivalents for most of that surface.

Sources used for this matrix (HoloAgent `main`):

- [README.md](https://github.com/HorizonRobotics/HoloAgent/blob/main/README.md)
- [docs/user_guide/Intruduction.md](https://github.com/HorizonRobotics/HoloAgent/blob/main/docs/user_guide/Intruduction.md)
- [agentic_robot/fsr_vln/README.md](https://github.com/HorizonRobotics/HoloAgent/blob/main/agentic_robot/fsr_vln/README.md)
- [agentic_robot/fsr_vln/api.py](https://github.com/HorizonRobotics/HoloAgent/blob/main/agentic_robot/fsr_vln/api.py)
- [agentic_robot/agentOS/README.md](https://github.com/HorizonRobotics/HoloAgent/blob/main/agentic_robot/agentOS/README.md)
- [agentic_robot/agentOS/holoagent_skills/README.md](https://github.com/HorizonRobotics/HoloAgent/blob/main/agentic_robot/agentOS/holoagent_skills/README.md)
- [agentic_robot/services/src/robot_bridge/config/bridge_config.yaml](https://github.com/HorizonRobotics/HoloAgent/blob/main/agentic_robot/services/src/robot_bridge/config/bridge_config.yaml)
- [robots/unitree/src](https://github.com/HorizonRobotics/HoloAgent/tree/main/robots/unitree/src)

## Capability alignment

| HoloAgent feature | HoloAgent path | DimOS equivalent | Gap | Approach |
| --- | --- | --- | --- | --- |
| Embodied AgentOS / OpenClaw skill registry | `agentic_robot/agentOS/holoagent_skills/` (`SKILL.md` + CRUD scripts) | `@skill` on `Module` (`dimos/agents/annotation.py`), MCP (`dimos/agents/mcp/`), blueprints (`dimos/core/coordination/blueprints.py`) | Different packaging (markdown skill dirs vs Python methods). HoloAgent is already DimOS-inspired. | **Reuse DimOS.** Do not import the markdown skill registry. |
| Workflow / long-horizon policy | `holoagent_skills/skills/workflow/SKILL.md` | Agent system prompt + LangGraph agent (`dimos/agents/agent.py`, `dimos/agents/system_prompt.py`) | HoloAgent's workflow SKILL.md is a prompt policy, not executable code. | **Skip** as code. Prompt ideas can be copied later if needed. |
| robot_bridge HTTP → ROS | `agentic_robot/services/src/robot_bridge/` port **8000**; `bridge_config.yaml` | DimOS LCM/RPC + MCP (`GlobalConfig.mcp_port` 9990). Deprecated REST skill: `dimos/skills/rest/rest.py` | No first-class client for HoloAgent's `/api/*` when that stack is colocated. | **Wrap as skill (this PR).** `HoloAgentBridgeClient` + `HoloAgentNavSkillContainer` (Go2 and G1). `/api/arm` is not exposed. |
| Semantic navigation skill | `holoagent_skills/skills/sem-nav-skill/` → `POST /api/semantic_nav` `{"cmd":"floor,room,object"}` | `NavigationSkillContainer.navigate_with_text` (`dimos/agents/skills/navigation.py`) + spatial memory | HoloAgent targets FSR-VLN/HMSG floor/room/object triples. DimOS uses CLIP/VLM/landmarks. | **Wrap as skill** `holoagent_semantic_nav`. Keep native `navigate_with_text` as default. |
| Relative move skill | `holoagent_skills/skills/rel-move-skill/` → `POST /api/relative_nav` `{"cmd":"forward,left,degrees"}` | Go2 `UnitreeSkillContainer` / G1 `move` (`dimos/robot/unitree/g1/skill_container.py`) | HoloAgent relative nav is ROS `/relative_nav` on their stack. | **Wrap as skill** `holoagent_relative_move` for the HoloAgent path only. |
| Arm skill | `holoagent_skills/skills/arm-skill/` + `robots/unitree/src/g1_arm/` (`POST /api/arm/{skill}` → `arm_signal_pub`) | G1 `execute_arm_command` (`dimos/robot/unitree/g1/skill_container.py`) | `g1_arm` `getcmd.cpp` subscribes to `chat_signal_pub`, not `arm_signal_pub`. No consumer for the documented arm HTTP path. | **Skip exposing** `/api/arm`. **Reuse DimOS** `execute_arm_command`. FIFO names that `getcmd.cpp` knows can go via `holoagent_navigation_signal` (`chat_signal_pub`). |
| FSR-VLN / HMSG / OVO mapping | `agentic_robot/fsr_vln/` (`FsrVlnClient.query` in `api.py`; Go2/G1 configs under `configs/Go2`, `configs/G1`) | Spatial memory (`dimos/perception/spatial_perception.py`), door/landmark memory, `memory2/` | Hierarchical floor→room→object scene graph + OVO instance mapping is not in DimOS. In-process `FsrVlnClient` needs their graph, CLIP, SAM, CUDA env. | **Skip vendoring.** Use `holoagent_semantic_nav` against a running HoloAgent mapper. Next files if we ever port query locally: `fsr_vln/api.py`, `memory/hmsg/graph/graph.py`. |
| FAST-LIVO mapping / reloc | `agentic_robot/core/src/fast_livo` | DimOS native mapping/nav (`dimos/navigation/`, `dimos/mapping/`) | FAST-LIVO license mismatch (GPL vs BSD) per HoloAgent notices. | **Skip.** Do not vendor. |
| Nav2 bringup / executors | `agentic_robot/core/src/nav_bringup`, `.../navigation` | DimOS planners (`dimos/navigation/replanning_a_star/`, frontier exploration) | ROS 2 Humble Nav2 vs DimOS-native. | **Reuse DimOS.** |
| Perception ROS nodes | `agentic_robot/core/src/perception` | `dimos/perception/` (detectors, tracking, VLM) | GPU inference stack is ROS-specific. | **Reuse DimOS.** |
| Chatbot / Doubao ASR+TTS | `agentic_robot/chatbot/` (`CHATBOT_ARK_API_KEY`, …) | `SpeakSkill` (`dimos/agents/skills/speak_skill.py`), G1 greeter audio (`dimos/robot/unitree/g1/`) | Vendor-locked Doubao keys. | **Skip.** Reuse DimOS TTS/ASR. |
| Unitree adapters | `robots/unitree/src/{g1_arm,g1_move,robot_odom}` | `dimos/robot/unitree/go2/`, `dimos/robot/unitree/g1/` | HoloAgent adapters are ROS 2 packages; DimOS uses WebRTC/DDS/LCM. | **Reuse DimOS.** |
| HexFellow + HoloBrain | `robots/hexfellow/`, external [HoloBrain](https://github.com/HorizonRobotics/RoboOrchardLab) | xArm / Piper manipulation (`dimos/manipulation/`) | Not a Topsun Go2/G1 target. | **Skip.** |
| HoloMotion whole-body | External [HoloMotion](https://github.com/HorizonRobotics/HoloMotion) | G1 skill container + connection | Separate repo, not in HoloAgent tree. | **Skip** until there is a concrete G1 motion-tracking need. |
| Multi-robot HTTP fan-out | `agentic_robot/services/src/multi_robot_ctl/` port 8080 | Go2 fleet (`dimos/robot/unitree/go2/fleet_connection.py`, `unitree_go2_fleet`) | Different control-center protocol. | **Skip** this slice. |

## What this PR implements

A **client + skill shim**, not a port of FSR-VLN or robot_bridge:

| DimOS file | Role |
| --- | --- |
| `dimos/agents/skills/holoagent_client.py` | HTTP client matching `bridge_config.yaml` |
| `dimos/agents/skills/holoagent.py` | `@skill` methods for MCP / LLM |
| `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_holoagent.py` | Go2 agentic + HoloAgent nav skills |
| `dimos/robot/unitree/g1/blueprints/agentic/unitree_g1_holoagent.py` | G1 agentic + HoloAgent nav skills |
| `GlobalConfig.holoagent_url` | `--holoagent-url` / `DIMOS_HOLOAGENT_URL` / `HOLOAGENT_URL` |

Default blueprints (`unitree-go2-agentic`, `unitree-g1-agentic`) are unchanged.

## Run

Start HoloAgent `robot_bridge` on the robot (their default is `0.0.0.0:8000`).
`--holoagent-url` is a root `GlobalConfig` flag (same as `--replay`); put it
**before** `run`. `run` does not declare it, so Click/Typer rejects
`dimos run … --holoagent-url …`.

```bash
# Go2
dimos --holoagent-url http://127.0.0.1:8000 run unitree-go2-holoagent

# G1
dimos --holoagent-url http://127.0.0.1:8000 run unitree-g1-holoagent

# Equivalent environment-variable override (also HOLOAGENT_URL):
# DIMOS_HOLOAGENT_URL=http://127.0.0.1:8000 dimos run unitree-go2-holoagent

# After the MCP-enabled stack is up:
dimos mcp call holoagent_health
dimos mcp call holoagent_semantic_nav --arg object_name="coffee machine" --arg floor=unknown --arg room=unknown
```

`holoagent_arm` is **not exposed**. HoloAgent `bridge_config.yaml` maps
`POST /api/arm/{skill}` to `arm_signal_pub`, but
`robots/unitree/src/g1_arm/src/getcmd.cpp` only subscribes to
`chat_signal_pub`. Use native `execute_arm_command`, or
`holoagent_navigation_signal` for FIFO names that `getcmd.cpp` actually
handles (that HTTP path publishes `chat_signal_pub`).

HoloAgent nav skills publish to ROS and return when the HTTP call is
accepted. They do **not** wait for `waypoint_reached`. They hold
`CAP_MOVEMENT` until `holoagent_stop_nav` (or `holoagent_navigation_signal`
with name `stop`), so a later native or HoloAgent movement skill is refused
until that stop. Module shutdown (`dimos stop`) best-effort POSTs
`/api/navigation/stop` before dropping the HTTP client.

`unitree-go2-holoagent` / `unitree-g1-holoagent` replace the nested
`McpClient` prompt with the robot prompt plus `HOLOAGENT_SKILLS_PROMPT`,
so a user stop request calls `holoagent_stop_nav` as well as
`stop_all_motion`. Native `stop_all_motion` still does not cancel the
bridge by itself.

Relative moves are short adjustments: finite values, at least one non-zero
axis, `|forward|`/`|left|` ≤ 3.0 m, `|rotation|` ≤ 180°. Longer goals should
use `holoagent_semantic_nav` or native DimOS navigation.

Compose the same nav skills into an existing Go2 blueprint without a new file:

```python skip
from dimos.agents.skills.holoagent import HoloAgentNavSkillContainer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic import unitree_go2_agentic

my_stack = autoconnect(unitree_go2_agentic, HoloAgentNavSkillContainer.blueprint())
```

G1 uses the same `HoloAgentNavSkillContainer`.

## Payload contract (do not invent)

HoloAgent `bridge_config.yaml` maps HTTP JSON key `cmd` onto `std_msgs/String.data`:

```text
POST /api/semantic_nav     {"cmd": "1F,pantry,coffee machine"}
POST /api/relative_nav     {"cmd": "1.0,0.0,90"}
POST /api/navigation/stop
POST /api/navigation/{name}
POST /api/arm/{skill}
GET  /health
```

The helper scripts `semantic_nav.py` / `relative_move.py` currently POST
structured keys (`floor`/`room`/`object`, `forward`/`left`/`rotation`).
`robot_bridge._build_msg` does `json_body.get("cmd")`, so those scripts do
**not** match the live bridge. DimOS follows the YAML + `SKILL.md` contract.

## Next files to touch (not in this PR)

1. Optional: load a prebuilt HMSG and call `FsrVlnClient.query` in-process
   (`agentic_robot/fsr_vln/api.py`) — only if Topsun ships HoloAgent maps
   and accepts the CUDA/SAM/OVO dependency.
2. Optional: in-process wait for HoloAgent `waypoint_reached` (the HTTP
   path is still publish-only; `CAP_MOVEMENT` is held until
   `holoagent_stop_nav`).
3. Do **not** start from an upstream `dimensionalOS/dimos` merge for this
   slice — `@skill`, MCP, and Go2/G1 blueprints already exist on
   `topsun-bot/topsun_dimos` main. No open upstream-merge PR was found.

## License

HoloAgent repository-owned code is Apache-2.0. This shim is original DimOS
code that calls their documented HTTP API. Do not copy `fast_livo` or other
vendored third-party trees.
