# Infrastructure

基础设施适配器连接外部服务，不承载游戏领域规则。

- `llm/` 处理模型 API、兼容 Base URL、请求参数和模板。
- `research/` 处理网页、Wiki 与本地检索服务。

外部返回内容一律是不可信输入，必须经过 Application 判断和本地候选验证后才能影响游戏动作。
