<!-- SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

## 变更

说明行为变化、原因和影响范围。

## 验证

- [ ] 已运行相关自动测试
- [ ] 未包含密钥、存档、抓包、私人 IP、个人路径或运行时状态
- [ ] 新增执行动作具有合法候选、确认语义和失败测试
- [ ] Application 只依赖 semantic action / Execution Broker，未新增 transport/runtime 直连
- [ ] 新 backend 复用公共 contract，并提供 request-id、ordered sequence 与机器证据测试
- [ ] 原生 action 自述所属 Application 与严格参数；未归类能力使用 `etc`
- [ ] 已运行 `python scripts/validation/run_tests.py --quiet`
- [ ] 所有提交均包含 `Signed-off-by`（DCO 1.1）
