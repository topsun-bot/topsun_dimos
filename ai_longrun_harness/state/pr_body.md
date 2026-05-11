## Summary

Spatial Memory & Embodied RAG for Unitree robots (Go2 + G1).

### New Features
- **Spatial Landmark System**: Unified data model (SpatialRecord) for doors, rooms, and generic landmarks with JSON persistence
- **Door Navigation Skills**: record_door, record_room, query_door, navigate_to_door agent skills
- **Topological Navigation**: A* path planning through known doors and rooms for efficient multi-waypoint navigation
- **Automatic Door Detection**: YOLO + CLIP zero-shot pipeline (door detection + open/closed state)
- **DeepSeek V4 Pro Integration**: Full agentic blueprints for both Go2 and G1 robots

### Blueprints Added
| Blueprint | Description |
|-----------|-------------|
| unitree-go2-agentic-deepseek | Go2 quadruped + DeepSeek V4 Pro |
| unitree-g1-agentic-deepseek | G1 humanoid + DeepSeek V4 Pro (real) |
| unitree-g1-agentic-sim-deepseek | G1 humanoid + DeepSeek V4 Pro (sim) |

### Infrastructure
- **McpClient**: Added model_provider and model_kwargs config for flexible LLM backend switching
- **AI Development Pipeline**: ai_longrun_harness/ multi-agent dev loop + PR delivery pipeline

### Testing
- 34 unit tests (all passing)
- Tested in replay mode with Go2 recorded data
- Tested in G1 MuJoCo simulation
