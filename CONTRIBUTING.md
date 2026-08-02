<!-- SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 贡献指南

## 许可证

提交代码即表示你同意按 `GPL-3.0-only` 发布该代码；提交文档即表示你同意按 `CC-BY-SA-4.0` 发布该文档。第三方内容必须明确标注来源和兼容许可证，不能从其他模组复制源码。

## DCO

本项目采用 Developer Certificate of Origin 1.1。每个提交必须包含与你提交身份一致的签署行：

```text
Signed-off-by: Your Name <EMAIL>
```

可使用 `git commit -s` 自动添加。完整条款见 [DCO](DCO)。使用化名贡献时，请先确认所用 Git 身份能够长期维持且仍满足 DCO；不要在 issue、日志或测试夹具中提交私人 IP、账户、存档或密钥。

## 变更要求

1. 建设动作必须来自规则引擎生成的合法候选。
2. 未经包回传或新存档确认，不得将动作记录为成功。
3. 新增协议字段必须附差分样本方法和可复现实验记录。
4. 网络抓取结果一律视为不可信参考，不得直接生成对象 ID 或绕过本地安全边界。
5. 运行 `python -m unittest discover -s tools/agent_runtime/tests -v`，并为行为变化补测试。
6. 不提交 `state/`、`runs/`、`secrets/`、抓包、校准截图、存档或构建目录。
