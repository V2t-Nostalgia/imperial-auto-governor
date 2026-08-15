# v0.5.8 工程化迁移

## 基线

本目录从原始工作仓库的提交 `81bb66d2609c42136fdae35b6538dea28d4ba9d7` 建立。迁移脚本位于 `scripts/migration/migrate_from_v058.py`，逐项列出允许复制的生产文件，不会遍历并盲目搬运整个旧目录。

原始工作仓库在迁移过程中按只读方式处理。迁移脚本会在源仓库存在未提交修改时拒绝执行，且不会删除、移动或重写源文件。

## 纳入内容

- 可运行的经济治理 Agent 与控制台。
- 存档接收、解析和候选生成逻辑。
- 固定坐标点击、端口发现、包拦截、命令改写和确认逻辑。
- Windows Host Bridge 与 Ubuntu 部署脚本。
- 建设载体 Stellaris Mod。
- SearXNG/Crawl4AI 检索服务配置。
- 法律、起源、贡献和安全文档。

## 明确排除

- 旧仓库测试文件与实验性一次性探针。
- 虚拟环境、`vendor_runtime`、Python 缓存和构建缓存。
- 存档、运行状态、数据库、日志、抓包和截图。
- 密钥、证书、令牌与机器专属配置。
- 旧发行 ZIP、展开目录和临时 Host Bridge 包。

新工程为每个逻辑组件预留独立 `tests/` 目录；旧测试不会混入新结构，测试应按新模块 API 逐步重写。

## 迁移后调整

- 将扁平脚本导入改为 `iag.*` 或 `apps.*` 包导入。
- 把 `tools/save_state` 归入 `src/iag/stellaris/state`。
- 把抓包改写器归入 `src/iag/stellaris/execution/packet`。
- 把经济治理逻辑归入 `src/iag/applications/economy_governance`。
- 把模型和联网检索供应商归入 `src/iag/infrastructure`。
- 把原版 4.4 能力清单迁入只含声明数据的 Content Pack。
- 修正 PyInstaller 和 systemd 对旧目录结构的假设。
- 保留已验证执行行为，不在迁移阶段重写协议算法。

`MIGRATION_MANIFEST.json` 记录源文件、目标文件和初始复制哈希；`ENGINEERING_MANIFEST.json` 在整理完成后记录目标文件最终哈希。两者共同区分“从哪里来”和“整理后是什么”。
