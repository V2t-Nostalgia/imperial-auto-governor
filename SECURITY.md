<!-- SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 安全政策

请不要在公开 issue 中粘贴 API 密钥、上传令牌、前端密码、完整存档、抓包、局域网拓扑或个人路径。报告漏洞时先提供经过脱敏的最小复现；维护者建立私密联系渠道前，不要公开可直接利用的细节。

支持范围以当前发布分支为准。房主桥必须以管理员权限运行 WinDivert，但 LLM API、网页控制台和存档解析不应以管理员身份运行。控制台绑定非回环地址时必须启用 TLS 和认证。

网页检索结果是不可信输入。任何网页文本均不得覆盖系统提示、本地候选引擎、对象标识、执行确认或网络改写安全条件。
