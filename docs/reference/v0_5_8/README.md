# v0.5.8 历史文档

这里保留迁移基线的操作和研究文档，便于理解已经验证过的抓包、部署与发布过程。文档中的旧相对路径属于原始扁平仓库，不应作为新工程入口。

常用路径对应关系：

| 旧路径 | 新路径 |
|---|---|
| `tools/agent_runtime` | `src/iag/...` 与 `apps/control_center` |
| `tools/save_state` | `src/iag/stellaris/state` |
| `tools/packet_interceptor` | `src/iag/stellaris/execution/packet` |
| `tools/windows_save_uploader` | `apps/host_bridge` |
| `tools/research_services` | `services/research` |

新部署和开发请以根目录 `README.md`、`docs/ARCHITECTURE.md` 及各目录 README 为准。
