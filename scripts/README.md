# Scripts

- `build/`：从当前 monorepo 构建 Windows Agent 和 Host Bridge。
- `deploy/`：在 Ubuntu 安装独立虚拟环境、启动控制台和安装 systemd 服务。
- `diagnostics/`：导出殖民地资料和分析被动 UDP 轨迹。
- `migration/`：从固定旧提交建立可审计迁移及生成代码地图。
- `validation/`：递归运行仓库测试并为本地、CI 与发布包提供同一验收入口。
- `release/`：从已提交源码重新构建、扫描、解压复验并生成五个公开附件。

构建脚本的清理范围被限制在本仓库 `build/` 下。部署脚本把可变状态写入 `runtime/`，不会修改源码配置模板。

发布构建要求工作树中的已跟踪文件保持干净：

```powershell
.\.venv\Scripts\python.exe scripts\release\build_release.py
```

最终附件只写入 `build/releases/v<版本>/final/`。脚本不创建 GPG 密钥、不创建 Tag，也不会发布 GitHub Release。
