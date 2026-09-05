"use strict";

const $ = (id) => document.getElementById(id);
let captureId = null;
let calibrationPoint = null;
let captureAction = "build_building";
let captureStage = "open";
let initialized = false;
let conversationLastId = 0;
const renderedConversationMessageIds = new Set();
let activeConversationId = null;
let loadedConversationId = null;
let campaignCatalog = null;
let campaignEditorMode = "create";
let campaignEditorConversationId = null;
let currentJobRunning = false;
let latestStatus = null;
let selectedConversationApplicationId = "economy_governance";
let modelTemplates = [];
let selectedTemplateId = "custom";
let modelPools = [];
let applicationModelProfiles = [];
let applicationModelBindings = {};
let registeredApplications = [];
let selectedApplicationId = null;
let selectedApplicationProfileId = null;
let selectedCatalogPoolId = null;
let endpointEditorDraft = null;
let endpointEditorOriginalId = null;
let applicationProfileDraft = false;
let catalogActionType = "build_building";
let catalogSelectionKey = null;
let renderedStrategyConversationId = null;
let executionSettingsInitialized = false;
let protocolSuitePayload = null;
let selectedProtocolScenarioId = null;
let protocolSuiteBusy = false;
let protocolSuitePollInFlight = false;

function gameDateFromMonthIndex(value) {
  const index = Number(value);
  if (!Number.isInteger(index) || index < 0) return null;
  const year = Math.floor(index / 12);
  const month = (index % 12) + 1;
  return year + "." + String(month).padStart(2, "0") + ".01";
}

const calibrationCopy = {
  "build_building:open": {
    label: "川陀建筑入口",
    target: "建筑命令舱的唯一空槽",
    prompt: "让川陀保持统一建设控制台基础画面，捕获后选择建筑命令舱的唯一空槽。",
  },
  "build_building:command": {
    label: "川陀建筑载体首项",
    target: "建设指令中继唯一候选",
    prompt: "先在游戏内打开川陀建筑列表，捕获后选择唯一的建设指令中继候选。",
  },
  "build_district:command": {
    label: "川陀主区划载体",
    target: "发电区划",
    prompt: "让川陀保持统一建设控制台基础画面，捕获后选择发电区划的建造按钮。",
  },
  "build_zone:open": {
    label: "川陀特化入口",
    target: "控制台的区划特化槽",
    prompt: "让川陀保持统一建设控制台基础画面，捕获后选择区划特化槽。",
  },
  "build_zone:command": {
    label: "川陀特化载体首项",
    target: "工程学研究特化唯一候选",
    prompt: "先在游戏内打开川陀特化列表，捕获后选择唯一的工程学研究特化候选。",
  },
  "upgrade_building:open": {
    label: "川陀升级载体槽",
    target: "预置升级指令中继所在的建筑槽",
    prompt: "让川陀保持统一建设控制台基础画面，捕获后选择预置升级指令中继所在的建筑槽。",
  },
  "upgrade_building:command": {
    label: "川陀升级按钮",
    target: "升级指令中继的升级按钮",
    prompt: "先在游戏内打开升级指令中继详情，捕获后选择它的升级按钮。",
  },
  "replace_building:open": {
    label: "川陀替换载体槽",
    target: "预置替换指令中继所在的建筑槽",
    prompt: "让川陀保持统一建设控制台基础画面，捕获后选择预置替换指令中继所在的建筑槽。",
  },
  "replace_building:replace": {
    label: "川陀替换按钮",
    target: "建筑详情中的替换按钮",
    prompt: "先在游戏内打开替换指令中继详情，捕获后选择建筑替换按钮。",
  },
  "replace_building:command": {
    label: "川陀替换载体候选",
    target: "替换指令中继目标候选",
    prompt: "先在游戏内打开替换建筑列表，捕获后选择替换指令中继目标候选。",
  },
};

function selectedCalibrationAction() {
  return $("calibration-action").value || "build_building";
}

function selectedCalibrationStage() {
  return selectedCalibrationAction() === "build_district"
    ? "command"
    : ($("calibration-stage").value || "open");
}

function selectedCalibrationKey() {
  return selectedCalibrationAction() + ":" + selectedCalibrationStage();
}

function updateCalibrationCopy() {
  const action = selectedCalibrationAction();
  const stageSelect = $("calibration-stage");
  const district = action === "build_district";
  const replacement = action === "replace_building";
  if (district) stageSelect.value = "command";
  if (!replacement && stageSelect.value === "replace") {
    stageSelect.value = district ? "command" : "open";
  }
  stageSelect.querySelector('option[value="open"]').disabled = district;
  stageSelect.querySelector('option[value="replace"]').disabled = !replacement;
  $("calibration-stage-label").classList.toggle("single-step", district);
  const actionLabels = {
    build_building: "建筑（川陀）：建设指令中继",
    build_district: "主区划（川陀）：发电区划载体",
    build_zone: "区划特化（川陀）：工程学研究特化唯一载体",
    upgrade_building: "建筑升级（川陀）：升级指令中继",
    replace_building: "建筑替换（川陀）：替换指令中继",
  };
  for (const option of $("calibration-action").options) {
    option.textContent = actionLabels[option.value] || option.textContent;
  }
  const copy = calibrationCopy[selectedCalibrationKey()];
  $("calibration-placeholder").textContent = copy.prompt;
  $("calibration-hint").textContent =
    "在截图中的" + copy.target + "上单击。该步骤保存独立坐标和局部防误点模板，不调用视觉模型。";
}

function resetCalibrationCapture() {
  captureId = null;
  calibrationPoint = null;
  const frame = $("calibration-frame");
  frame.classList.remove("ready", "marked");
  $("calibration-image").removeAttribute("src");
  $("commit-button").disabled = true;
}

const confidence = {
  brokered_flow_candidate: ["代理链路已发现", "warn", "FLOW CANDIDATE"],
  offline: ["等待 Stellaris", "offline", "OFFLINE"],
  process_detected: ["进程已发现", "warn", "PROCESS"],
  socket_detected: ["候选端口已发现", "warn", "SOCKET"],
  peer_detected: ["房主端点已发现", "warn", "PEER"],
  bidirectional_flow_observed: ["双向流量已捕捉", "good", "FLOW VERIFIED"],
  carrier_command_verified: ["载体命令已捕捉", "good", "CARRIER VERIFIED"],
  authoritative_confirmed: ["房主权威回包已确认", "good", "HOST CONFIRMED"],
  port_conflict: ["端口冲突，禁止执行", "bad", "PORT CONFLICT"],
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || ("HTTP " + response.status));
  return value;
}

async function post(path, body = {}) {
  return api(path, {method: "POST", body: JSON.stringify(body)});
}

function message(text, isError = false) {
  $("footer-message").textContent = text;
  $("footer-message").style.color = isError ? "var(--red)" : "var(--muted)";
}

async function downloadPairedHostBridge(event) {
  event.preventDefault();
  const link = $("download-host-bridge");
  if (link.getAttribute("aria-disabled") === "true") return;
  const status = $("host-bridge-download-status");
  const originalText = link.textContent;
  const downloadPath = link.getAttribute("href");
  link.setAttribute("aria-disabled", "true");
  link.textContent = "正在生成配对包...";
  status.textContent = "正在准备当前 Agent 的地址、证书指纹和连接令牌，请稍候。";
  try {
    const probeUrl = new URL(downloadPath, window.location.href);
    probeUrl.searchParams.set("prepare", Date.now().toString());
    const response = await fetch(probeUrl, {
      method: "HEAD",
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }

    const trigger = document.createElement("a");
    trigger.href = probeUrl.href;
    trigger.download = "IAGHostBridge-paired-windows-x64.zip";
    trigger.hidden = true;
    document.body.appendChild(trigger);
    trigger.click();
    trigger.remove();
    status.textContent = "配对包已就绪，浏览器下载已经开始。";
    message("Windows 房主执行桥下载已开始");
  } catch (error) {
    status.textContent = "配对包下载失败：" + error.message + "。可以再次点击重试。";
    message("房主执行桥下载失败：" + error.message, true);
  } finally {
    link.removeAttribute("aria-disabled");
    link.textContent = originalText;
  }
}

function formatAction(manifest) {
  const action = manifest?.action;
  if (!action || action.type === "noop") return "本轮不建设";
  const target = action.planet_name_key || ("planet " + (action.planet_id ?? "?"));
  const object = action.to_building_id || action.building_id ||
    action.zone_type || action.type;
  return target + " · " + object;
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 1}).format(Number(value));
}

function renderResource(snapshot, key, elementSuffix) {
  const country = snapshot.country || {};
  const stockpile = country.stockpile || {};
  const balance = country.monthly_balance || {};
  let stored = stockpile[key];
  let monthly = balance[key];
  if (key === "research_total") {
    stored = ["physics_research", "society_research", "engineering_research"]
      .reduce((sum, item) => sum + Number(stockpile[item] || 0), 0);
    monthly = balance.research_total;
  }
  $("resource-" + elementSuffix).textContent = formatNumber(stored);
  const balanceElement = $("balance-" + elementSuffix);
  balanceElement.textContent = monthly === null || monthly === undefined
    ? "--/月"
    : (Number(monthly) >= 0 ? "+" : "") + formatNumber(monthly) + "/月";
  balanceElement.className = monthly === null || monthly === undefined
    ? ""
    : (Number(monthly) >= 0 ? "positive" : "negative");
}

function formatSaveFreshness(save, snapshot) {
  if (!save.modified_at) return "--";
  const seconds = Math.max((Date.now() - Date.parse(save.modified_at)) / 1000, 0);
  const age = seconds < 120
    ? Math.round(seconds) + " 秒"
    : (seconds < 7200 ? Math.round(seconds / 60) + " 分钟" : Math.round(seconds / 3600) + " 小时");
  const sameSource = snapshot.source_save?.path === save.path;
  return age + (sameSource ? " · 已载入" : " · 尚未载入规划");
}

function templateById(templateId) {
  return modelTemplates.find((item) => item.id === templateId);
}

function setTemplateFields(templateId, applyDefaults) {
  const template = templateById(templateId);
  if (!template) return;
  selectedTemplateId = templateId;
  $("template-description").textContent = template.description || "";
  const config = template.config || {};
  if (applyDefaults && templateId !== "custom") {
    $("model-transport").value = config.model_transport || "openai_sdk";
    $("provider").value = config.provider || "chat_completions_compatible";
    $("base-url").value = config.base_url || "";
    $("model").value = config.model || "";
    if (!$("endpoint-logical-model-id").value.trim() && config.model) {
      $("endpoint-logical-model-id").value = config.model;
    }
    $("supports-reasoning").checked = config.supports_reasoning !== false;
    $("supports-tools").checked = config.tool_calling_enabled !== false;
    if (config.models_path !== undefined) {
      $("models-path").value = config.models_path || "";
    }
    if (config.model_context_window_tokens) {
      $("context-window-tokens").value = config.model_context_window_tokens;
    }
    if (config.context_output_reserve_tokens) {
      $("context-output-reserve").value = config.context_output_reserve_tokens;
    }
  }
  for (const id of ["model-transport", "provider", "base-url", "model", "supports-reasoning", "supports-tools"]) {
    $(id).disabled = false;
  }
}

function cloneValue(value) {
  return JSON.parse(JSON.stringify(value));
}

function generatedIdentifier(prefix, existingValues = []) {
  const occupied = new Set(existingValues);
  const seed = Date.now().toString(36);
  let suffix = 0;
  let value = prefix + "-" + seed;
  while (occupied.has(value)) {
    suffix += 1;
    value = prefix + "-" + seed + "-" + suffix;
  }
  return value;
}

function modelPoolById(poolId) {
  return modelPools.find((item) => item.pool_id === poolId) || null;
}

function selectedCatalogPool() {
  return modelPoolById(selectedCatalogPoolId);
}

function selectedApplication() {
  return registeredApplications.find(
    (item) => item.application_id === selectedApplicationId,
  ) || null;
}

function selectedApplicationProfile() {
  return applicationModelProfiles.find(
    (item) => item.profile_id === selectedApplicationProfileId,
  ) || null;
}

function logicalModels(pool) {
  const values = [];
  for (const endpoint of pool?.endpoints || []) {
    let item = values.find((candidate) => candidate.model_id === endpoint.model_id);
    if (!item) {
      item = {model_id: endpoint.model_id, endpoints: [], enabled: 0};
      values.push(item);
    }
    item.endpoints.push(endpoint);
    if (endpoint.enabled) item.enabled += 1;
  }
  return values;
}

const endpointHealthLabels = {
  available: "可用",
  reachable_unconfirmed: "可达，模型未确认",
  authentication_failed: "鉴权失败",
  rate_limited: "额度冷却中",
  probe_unsupported: "不支持模型列表检测",
  temporarily_unhealthy: "服务暂时异常",
  unreachable: "无法连接",
  model_unavailable: "模型不可用",
  request_rejected: "请求被拒绝",
  unknown_error: "未知错误",
  unknown: "尚未检测",
};

function renderModelEndpointHealth(endpoint = endpointEditorDraft) {
  const box = $("model-endpoint-health");
  const health = endpoint?.health || {status: "unknown"};
  box.dataset.state = health.status || "unknown";
  box.querySelector("strong").textContent =
    endpointHealthLabels[health.status] || health.status;
  const details = [];
  if (health.detail) details.push(health.detail);
  if (health.http_status) details.push("HTTP " + health.http_status);
  if (health.cooldown_until) details.push("冷却至 " + health.cooldown_until);
  if (health.last_checked_at) details.push("检测 " + health.last_checked_at);
  box.querySelector("span").textContent =
    details.join(" · ") || "保存后可读取 /models 验证可达性。";
}

function writeModelEndpointForm(endpoint) {
  if (!endpoint) return;
  endpointEditorDraft = cloneValue(endpoint);
  $("model-endpoint-id").value = endpoint.endpoint_id;
  $("model-endpoint-id").readOnly = endpointEditorOriginalId !== null;
  $("model-endpoint-display-name").value =
    endpoint.display_name || endpoint.endpoint_id;
  $("endpoint-logical-model-id").value = endpoint.model_id || endpoint.model || "";
  $("model-endpoint-priority").value = endpoint.priority ?? 0;
  $("model-endpoint-enabled").checked = endpoint.enabled !== false;
  $("supports-tools").checked = endpoint.supports_tools !== false;
  $("supports-reasoning").checked = endpoint.supports_reasoning !== false;
  $("model-transport").value = endpoint.model_transport || "openai_sdk";
  $("provider").value = endpoint.provider || "chat_completions_compatible";
  $("base-url").value = endpoint.base_url || "";
  $("model").value = endpoint.model || "";
  $("auth-mode").value = endpoint.auth_mode || "bearer";
  $("api-key").disabled = $("auth-mode").value === "none";
  $("api-key").value = "";
  $("api-key").placeholder = endpoint.api_key_configured
    ? "已配置；留空则保留当前端点密钥"
    : "输入当前端点 API Key";
  $("clear-api-key").checked = false;
  $("models-path").value = endpoint.models_path || "";
  $("probe-timeout-seconds").value = endpoint.probe_timeout_seconds ?? 10;
  $("rate-limit-cooldown-seconds").value =
    endpoint.rate_limit_cooldown_seconds ?? 300;
  $("timeout-seconds").value = endpoint.timeout_seconds ?? 120;
  $("sdk-max-retries").value = endpoint.sdk_max_retries ?? 2;
  $("context-window-tokens").value =
    endpoint.model_context_window_tokens ?? 128000;
  $("context-output-reserve").value = endpoint.max_output_tokens ?? 8192;
  $("chat-completions-path").value =
    endpoint.chat_completions_path || "/chat/completions";
  $("responses-path").value = endpoint.responses_path || "/responses";
  $("api-key-header").value = endpoint.api_key_header || "Authorization";
  $("api-key-prefix").value = endpoint.api_key_prefix ?? "Bearer ";
  $("endpoint-extra-headers").value = JSON.stringify(
    endpoint.extra_headers || {},
    null,
    2,
  );
  $("endpoint-editor-title").textContent = endpointEditorOriginalId
    ? "编辑端点 · " + (endpoint.display_name || endpoint.endpoint_id)
    : "创建新端点";
  selectedTemplateId = "custom";
  $("model-template").value = "custom";
  setTemplateFields("custom", false);
  renderModelEndpointHealth(endpoint);
}

function readModelEndpointForm() {
  if (!endpointEditorDraft) throw new Error("当前没有正在编辑的端点。");
  const endpoint = cloneValue(endpointEditorDraft);
  const endpointId = $("model-endpoint-id").value.trim();
  const displayName = $("model-endpoint-display-name").value.trim();
  const modelId = $("endpoint-logical-model-id").value.trim();
  if (!endpointId || !displayName || !modelId) {
    throw new Error("端点名称、技术标识和逻辑模型标识都不能为空。");
  }
  endpoint.endpoint_id = endpointId;
  endpoint.display_name = displayName;
  endpoint.model_id = modelId;
  endpoint.priority = Number($("model-endpoint-priority").value);
  endpoint.enabled = $("model-endpoint-enabled").checked;
  endpoint.supports_tools = $("supports-tools").checked;
  endpoint.supports_reasoning = $("supports-reasoning").checked;
  endpoint.model_transport = $("model-transport").value;
  endpoint.provider = $("provider").value;
  endpoint.base_url = $("base-url").value.trim();
  endpoint.model = $("model").value.trim();
  endpoint.auth_mode = $("auth-mode").value;
  endpoint.models_path = $("models-path").value.trim() || null;
  endpoint.probe_timeout_seconds = Number($("probe-timeout-seconds").value);
  endpoint.rate_limit_cooldown_seconds = Number(
    $("rate-limit-cooldown-seconds").value,
  );
  endpoint.timeout_seconds = Number($("timeout-seconds").value);
  endpoint.sdk_max_retries = Number($("sdk-max-retries").value);
  endpoint.model_context_window_tokens = Number(
    $("context-window-tokens").value,
  );
  endpoint.max_output_tokens = Number($("context-output-reserve").value);
  endpoint.chat_completions_path =
    $("chat-completions-path").value.trim() || "/chat/completions";
  endpoint.responses_path =
    $("responses-path").value.trim() || "/responses";
  endpoint.api_key_header = $("api-key-header").value.trim() || "Authorization";
  endpoint.api_key_prefix = $("api-key-prefix").value;
  try {
    endpoint.extra_headers = JSON.parse(
      $("endpoint-extra-headers").value.trim() || "{}",
    );
  } catch (error) {
    throw new Error("端点附加 Headers 不是有效 JSON：" + error.message);
  }
  if (
    !endpoint.extra_headers ||
    Array.isArray(endpoint.extra_headers) ||
    typeof endpoint.extra_headers !== "object"
  ) {
    throw new Error("端点附加 Headers 必须是 JSON 对象。");
  }
  const apiKey = $("api-key").value.trim();
  if (apiKey) endpoint.api_key = apiKey;
  endpoint.clear_api_key = $("clear-api-key").checked;
  return endpoint;
}

function commitSelectedPoolIdentity() {
  const pool = selectedCatalogPool();
  if (!pool) return;
  const displayName = $("catalog-pool-name").value.trim();
  if (!displayName) throw new Error("模型池名称不能为空。");
  pool.display_name = displayName;
}

function renderModelPoolSidebar() {
  const list = $("model-pool-list");
  list.replaceChildren();
  $("model-pool-count").textContent = modelPools.length;
  for (const pool of modelPools) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "model-pool-list-item" +
      (pool.pool_id === selectedCatalogPoolId ? " active" : "");
    const title = document.createElement("strong");
    title.textContent = pool.display_name;
    const detail = document.createElement("small");
    detail.textContent = pool.endpoints.length + " 端点 · " +
      logicalModels(pool).length + " 模型";
    button.append(title, detail);
    button.addEventListener("click", () => {
      try {
        if (!$("model-pool-overview").hidden) commitSelectedPoolIdentity();
        selectedCatalogPoolId = pool.pool_id;
        showModelPoolOverview();
      } catch (error) {
        message(error.message, true);
      }
    });
    list.appendChild(button);
  }
}

function renderCatalogEndpointList(pool) {
  const list = $("catalog-endpoint-list");
  list.replaceChildren();
  const endpoints = [...(pool?.endpoints || [])].sort(
    (left, right) => Number(left.priority) - Number(right.priority),
  );
  if (!endpoints.length) {
    const empty = document.createElement("div");
    empty.className = "catalog-endpoint-empty";
    empty.textContent = "这个模型池还没有端点。添加第一个供应来源后，Application 才能选择其中的逻辑模型。";
    list.appendChild(empty);
    return;
  }
  for (const endpoint of endpoints) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "catalog-endpoint-card";
    card.dataset.state = endpoint.health?.status || "unknown";
    const heading = document.createElement("div");
    heading.className = "catalog-endpoint-card-heading";
    const title = document.createElement("strong");
    title.textContent = endpoint.display_name || endpoint.endpoint_id;
    const priority = document.createElement("span");
    priority.textContent = "P" + Number(endpoint.priority || 0);
    heading.append(title, priority);
    const route = document.createElement("p");
    route.textContent = endpoint.model_id + " → " + endpoint.model;
    const status = document.createElement("small");
    const health = endpoint.health?.status || "unknown";
    status.textContent = (endpoint.enabled ? "已启用" : "已停用") + " · " +
      (endpointHealthLabels[health] || health) + " · " + endpoint.base_url;
    const editHint = document.createElement("span");
    editHint.className = "catalog-endpoint-edit-hint";
    editHint.textContent = "编辑参数 →";
    card.title = "编辑 " + (endpoint.display_name || endpoint.endpoint_id) + " 的端点参数";
    card.append(heading, route, status, editHint);
    card.addEventListener("click", () => openModelEndpointEditor(endpoint.endpoint_id));
    list.appendChild(card);
  }
}

function updateSelectedPoolHealth(pool) {
  const tag = $("selected-pool-health");
  const enabled = (pool?.endpoints || []).filter((item) => item.enabled);
  const available = enabled.filter(
    (item) => item.health?.status === "available",
  ).length;
  if (!enabled.length) {
    tag.textContent = "没有启用端点";
    tag.className = "status-tag warn";
  } else if (available) {
    tag.textContent = available + "/" + enabled.length + " 可用";
    tag.className = "status-tag good";
  } else {
    tag.textContent = enabled.length + " 个端点待检测";
    tag.className = "status-tag warn";
  }
}

function showModelPoolOverview() {
  const pool = selectedCatalogPool();
  if (!pool) return;
  endpointEditorDraft = null;
  endpointEditorOriginalId = null;
  $("model-endpoint-editor").hidden = true;
  $("model-pool-overview").hidden = false;
  $("catalog-pool-name").value = pool.display_name || pool.pool_id;
  $("catalog-pool-id").value = pool.pool_id;
  $("catalog-pool-id").readOnly = true;
  $("selected-pool-title").textContent = pool.display_name || pool.pool_id;
  $("delete-model-pool").disabled = modelPools.length <= 1;
  $("probe-selected-pool").disabled = !pool.endpoints.length;
  renderModelPoolSidebar();
  renderCatalogEndpointList(pool);
  updateSelectedPoolHealth(pool);
}

function defaultModelEndpoint(pool) {
  const endpointId = generatedIdentifier(
    "endpoint",
    pool.endpoints.map((item) => item.endpoint_id),
  );
  return {
    endpoint_id: endpointId,
    display_name: "新供应端点",
    model_id: "",
    model: "",
    model_transport: "openai_sdk",
    provider: "chat_completions_compatible",
    base_url: "https://api.example.com/v1",
    supports_reasoning: false,
    supports_tools: true,
    model_context_window_tokens: 128000,
    auth_mode: "bearer",
    max_output_tokens: 8192,
    priority: pool.endpoints.length,
    enabled: false,
    chat_completions_path: "/chat/completions",
    responses_path: "/responses",
    models_path: "/models",
    timeout_seconds: 120,
    probe_timeout_seconds: 10,
    rate_limit_cooldown_seconds: 300,
    sdk_max_retries: 2,
    extra_headers: {},
    api_key_header: "Authorization",
    api_key_prefix: "Bearer ",
    api_key_configured: false,
    health: {status: "unknown", eligible: false},
  };
}

function openModelEndpointEditor(endpointId = null) {
  const pool = selectedCatalogPool();
  if (!pool) return;
  const endpoint = endpointId
    ? pool.endpoints.find((item) => item.endpoint_id === endpointId)
    : defaultModelEndpoint(pool);
  if (!endpoint) throw new Error("找不到要编辑的模型端点。");
  endpointEditorOriginalId = endpointId;
  $("model-pool-overview").hidden = true;
  $("model-endpoint-editor").hidden = false;
  writeModelEndpointForm(endpoint);
}

function commitModelEndpointEditor() {
  const pool = selectedCatalogPool();
  if (!pool) throw new Error("尚未选择模型池。");
  const endpoint = readModelEndpointForm();
  const duplicate = pool.endpoints.some(
    (item) => item.endpoint_id === endpoint.endpoint_id &&
      item.endpoint_id !== endpointEditorOriginalId,
  );
  if (duplicate) throw new Error("端点技术标识在当前模型池中必须唯一。");
  if (endpointEditorOriginalId === null) {
    pool.endpoints.push(endpoint);
  } else {
    const index = pool.endpoints.findIndex(
      (item) => item.endpoint_id === endpointEditorOriginalId,
    );
    if (index < 0) throw new Error("原端点已不存在。");
    pool.endpoints[index] = endpoint;
  }
  showModelPoolOverview();
  renderApplicationModelSelect($("application-model-select").value);
  message("端点已应用到模型池草稿；点击“保存全部模型池”后写入本地配置。");
}

function modelEndpointPayload(endpoint) {
  const keys = [
    "endpoint_id", "display_name", "model_id", "priority", "enabled",
    "model", "model_transport", "provider", "base_url",
    "supports_reasoning", "supports_tools", "model_context_window_tokens",
    "auth_mode", "api_key", "max_output_tokens", "chat_completions_path",
    "responses_path", "models_path", "timeout_seconds",
    "probe_timeout_seconds", "rate_limit_cooldown_seconds",
    "sdk_max_retries", "extra_headers", "api_key_header",
    "api_key_prefix", "clear_api_key",
  ];
  const value = {};
  for (const key of keys) {
    if (endpoint[key] !== undefined) value[key] = endpoint[key];
  }
  return value;
}

function modelPoolsPayload() {
  commitSelectedPoolIdentity();
  return {
    model_pools: modelPools.map((pool) => ({
      pool_id: pool.pool_id,
      display_name: pool.display_name,
      endpoints: pool.endpoints.map(modelEndpointPayload),
    })),
  };
}

async function saveModelPools(announce = true) {
  const result = await post("/api/model/pools", modelPoolsPayload());
  $("api-key").value = "";
  initializeModel(result);
  if (announce) message("模型池与端点已经保存。");
  return result;
}

function renderApplicationSelect() {
  const select = $("application-select");
  select.replaceChildren();
  for (const application of registeredApplications) {
    const option = document.createElement("option");
    option.value = application.application_id;
    option.textContent = application.display_name_zh + " · " + application.application_id;
    select.appendChild(option);
  }
  select.value = selectedApplicationId || "";
}

function profilesForApplication(applicationId) {
  return applicationModelProfiles.filter(
    (item) => item.application_id === applicationId,
  );
}

function renderApplicationProfileSelect() {
  const profiles = profilesForApplication(selectedApplicationId);
  const select = $("application-profile-select");
  select.replaceChildren();
  for (const profile of profiles) {
    const option = document.createElement("option");
    option.value = profile.profile_id;
    option.textContent = profile.display_name + (profile._draft ? "（未保存）" : "");
    select.appendChild(option);
  }
  if (!profiles.some((item) => item.profile_id === selectedApplicationProfileId)) {
    selectedApplicationProfileId =
      applicationModelBindings[selectedApplicationId] || profiles[0]?.profile_id || null;
  }
  select.value = selectedApplicationProfileId || "";
  $("delete-application-profile").disabled = profiles.length <= 1;
}

function renderApplicationPoolSelect(preferredPoolId) {
  const select = $("application-pool-select");
  select.replaceChildren();
  for (const pool of modelPools) {
    const option = document.createElement("option");
    option.value = pool.pool_id;
    option.textContent = pool.display_name + " · " + pool.endpoints.length + " 端点";
    select.appendChild(option);
  }
  const poolId = modelPoolById(preferredPoolId)
    ? preferredPoolId
    : modelPools[0]?.pool_id || "";
  select.value = poolId;
}

function renderApplicationModelSelect(preferredModelId) {
  const select = $("application-model-select");
  const pool = modelPoolById($("application-pool-select").value);
  const models = logicalModels(pool);
  select.replaceChildren();
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.model_id;
    option.textContent = model.model_id + " · " + model.enabled + "/" +
      model.endpoints.length + " 端点启用";
    select.appendChild(option);
  }
  select.value = models.some((item) => item.model_id === preferredModelId)
    ? preferredModelId
    : models[0]?.model_id || "";
  renderApplicationModelRoute();
}

function renderApplicationModelRoute() {
  const route = $("application-model-route");
  const application = selectedApplication();
  const pool = modelPoolById($("application-pool-select").value);
  const modelId = $("application-model-select").value;
  const endpoints = (pool?.endpoints || [])
    .filter((item) => item.model_id === modelId && item.enabled)
    .sort((left, right) => Number(left.priority) - Number(right.priority));
  if (!application || !pool || !modelId || !endpoints.length) {
    route.dataset.state = "empty";
    $("application-model-route-title").textContent = "当前选择没有可用端点";
    $("application-model-route-detail").textContent =
      "请在模型配置面板中为该逻辑模型启用至少一个端点并配置鉴权。";
    return;
  }
  route.dataset.state = "ready";
  $("application-model-route-title").textContent =
    application.display_name_zh + " → " + pool.display_name + " / " + modelId;
  $("application-model-route-detail").textContent = endpoints.map(
    (item) => "P" + item.priority + " " + (item.display_name || item.endpoint_id),
  ).join(" → ") + "；按顺序执行保守换源。";
}

function writeApplicationProfileForm(profile) {
  if (!profile) return;
  applicationProfileDraft = Boolean(profile._draft);
  $("application-profile-name").value = profile.display_name || "";
  renderApplicationPoolSelect(profile.pool_id);
  renderApplicationModelSelect(profile.model_id);
  const request = profile.request_options || {};
  const options = profile.application_options || {};
  $("thinking-enabled").checked = request.thinking?.type === "enabled";
  $("reasoning-effort").value = request.reasoning_effort || "high";
  $("temperature").value = request.temperature ?? 0.15;
  $("context-compression-enabled").checked =
    options.context_compression_enabled !== false;
  $("context-compression-trigger").value =
    options.context_compression_trigger_percent ?? 80;
  $("context-compression-target").value =
    options.context_compression_target_percent ?? 35;
  $("request-body-overrides").value = JSON.stringify(
    request.request_body_overrides || {},
    null,
    2,
  );
  $("web-research-enabled").checked = Boolean(options.web_research_enabled);
  $("searxng-url").value = options.searxng_url || "http://127.0.0.1:8080";
  $("crawl4ai-url").value = options.crawl4ai_url || "http://127.0.0.1:11235";
  $("crawl4ai-token").value = "";
  $("stellaris-wiki-api-url").value = options.stellaris_wiki_api_url ||
    "https://stellaris.paradoxwikis.com/api.php";
  $("web-allowed-domains").value =
    (options.web_search_allowed_domains || []).join("\n");
  $("web-direct-fallback-enabled").checked = Boolean(
    options.web_fetch_direct_fallback_enabled,
  );
}

function renderApplicationConfiguration() {
  if (!registeredApplications.length) return;
  if (!registeredApplications.some(
    (item) => item.application_id === selectedApplicationId,
  )) {
    selectedApplicationId = registeredApplications[0].application_id;
  }
  renderApplicationSelect();
  renderApplicationProfileSelect();
  writeApplicationProfileForm(selectedApplicationProfile());
}

function applicationProfilePayload() {
  const profile = selectedApplicationProfile();
  if (!profile) throw new Error("尚未选择 Application 模型配置。");
  const displayName = $("application-profile-name").value.trim();
  const poolId = $("application-pool-select").value;
  const modelId = $("application-model-select").value;
  if (!displayName || !poolId || !modelId) {
    throw new Error("配置名称、模型池和逻辑模型都不能为空。");
  }
  return {
    profile_id: profile.profile_id,
    display_name: displayName,
    application_id: selectedApplicationId,
    pool_id: poolId,
    model_id: modelId,
    thinking_enabled: $("thinking-enabled").checked,
    reasoning_effort: $("reasoning-effort").value,
    temperature: Number($("temperature").value),
    context_compression_enabled: $("context-compression-enabled").checked,
    context_compression_trigger_percent: Number($("context-compression-trigger").value),
    context_compression_target_percent: Number($("context-compression-target").value),
    request_body_overrides: $("request-body-overrides").value,
    web_research_enabled: $("web-research-enabled").checked,
    searxng_url: $("searxng-url").value,
    crawl4ai_url: $("crawl4ai-url").value,
    crawl4ai_api_token: $("crawl4ai-token").value,
    stellaris_wiki_api_url: $("stellaris-wiki-api-url").value,
    web_search_allowed_domains: $("web-allowed-domains").value,
    web_fetch_direct_fallback_enabled: $("web-direct-fallback-enabled").checked,
  };
}

function initializeModel(model) {
  const previousApplicationId = selectedApplicationId;
  const previousProfileId = selectedApplicationProfileId;
  const previousPoolId = selectedCatalogPoolId;
  modelTemplates = cloneValue(model.templates || []);
  modelPools = cloneValue(model.model_pools || []);
  applicationModelProfiles = cloneValue(model.application_model_profiles || []);
  applicationModelBindings = cloneValue(model.application_model_bindings || {});
  registeredApplications = cloneValue(model.applications || []);
  selectedApplicationId = registeredApplications.some(
    (item) => item.application_id === previousApplicationId,
  ) ? previousApplicationId : (model.active_application_id || registeredApplications[0]?.application_id);
  selectedApplicationProfileId = applicationModelProfiles.some(
    (item) => item.profile_id === previousProfileId &&
      item.application_id === selectedApplicationId,
  ) ? previousProfileId :
    (applicationModelBindings[selectedApplicationId] || model.active_profile_id);
  selectedCatalogPoolId = modelPoolById(previousPoolId)
    ? previousPoolId
    : (model.model_pool?.pool_id || modelPools[0]?.pool_id || null);

  const templateSelect = $("model-template");
  templateSelect.replaceChildren();
  for (const template of modelTemplates) {
    const option = document.createElement("option");
    option.value = template.id;
    option.textContent = template.label;
    templateSelect.appendChild(option);
  }
  if (!modelTemplates.some((item) => item.id === "custom")) {
    const option = document.createElement("option");
    option.value = "custom";
    option.textContent = "自定义";
    templateSelect.prepend(option);
  }
  templateSelect.value = "custom";
  renderApplicationConfiguration();
  renderModelPoolSidebar();
  if (selectedCatalogPool()) showModelPoolOverview();
}

function mergeModelPoolHealth(model) {
  const catalog = model?.model_pool_health_catalog || {};
  for (const pool of modelPools) {
    const healthById = new Map(
      (catalog[pool.pool_id]?.endpoints || []).map(
        (item) => [item.endpoint_id, item],
      ),
    );
    for (const endpoint of pool.endpoints) {
      if (healthById.has(endpoint.endpoint_id)) {
        endpoint.health = cloneValue(healthById.get(endpoint.endpoint_id));
      }
    }
  }
  renderApplicationModelRoute();
  if ($("model-catalog-dialog").open && !$("model-pool-overview").hidden) {
    renderModelPoolSidebar();
    renderCatalogEndpointList(selectedCatalogPool());
    updateSelectedPoolHealth(selectedCatalogPool());
  }
}

function syncStrategyTextarea(element, text, recordId, force = false) {
  const serverText = text || "";
  const selectedId = recordId || "none";
  const unchangedLocally = element.value === (element.dataset.serverText || "");
  if (force || element.dataset.recordId !== selectedId || unchangedLocally) {
    element.value = serverText;
    element.dataset.serverText = serverText;
    element.dataset.recordId = selectedId;
  }
}

function renderStrategy(strategy, conversationId, running = false) {
  const decade = strategy?.decade || {};
  const emergency = strategy?.emergency || {};
  const current = decade.current || null;
  const next = decade.next_draft || null;
  const force = renderedStrategyConversationId !== conversationId;
  renderedStrategyConversationId = conversationId;

  syncStrategyTextarea(
    $("decade-plan"),
    current?.text || "",
    current?.plan_id || "none",
    force,
  );
  syncStrategyTextarea(
    $("next-decade-plan"),
    next?.text || "",
    next?.plan_id || "none",
    force,
  );
  $("decade-period").textContent = current
    ? current.start_year + " - " + current.end_year
    : (strategy?.game_date ? "从 " + strategy.game_date + " 建立" : "等待绑定存档");
  $("next-decade-period").textContent = next
    ? next.start_year + " - " + next.end_year
    : "编写窗口已开放";
  $("next-decade-block").hidden = !decade.renewal_open;

  const state = $("strategy-state");
  if (emergency.active) {
    state.textContent = "紧急状态 · 十年计划暂停";
    state.className = "status-tag bad";
  } else if (!current) {
    state.textContent = "等待建立计划";
    state.className = "status-tag warn";
  } else if (decade.renewal_open) {
    state.textContent = "下一周期编写窗口已开放";
    state.className = "status-tag warn";
  } else {
    state.textContent = "计划执行中";
    state.className = "status-tag good";
  }

  const emergencyBlock = $("strategic-emergency-block");
  emergencyBlock.classList.toggle("active", Boolean(emergency.active));
  $("strategic-emergency-state").textContent = emergency.active
    ? "已启用 · " + (emergency.activated_game_date || "日期未知")
    : "未启用";
  if (force || emergency.active) {
    $("strategic-emergency-title").value = emergency.title || "";
    $("strategic-emergency-directive").value = emergency.directive || "";
  }
  $("save-decade-plan").disabled = running || !strategy?.game_date;
  $("save-next-decade-plan").disabled = running || !decade.renewal_open;
  $("activate-strategic-emergency").disabled = running;
  $("end-strategic-emergency").disabled = running || !emergency.active;
}

function updateExecutionModeVisibility() {
  const proxyMode = $("execution-mode").value === "session_proxy";
  $("session-proxy-settings").hidden = !proxyMode;
  $("carrier-calibration-panel").hidden = proxyMode;
  $("fixed-click-guard-row").hidden = proxyMode;
  $("fixed-click-guard-hint").hidden = proxyMode;
  $("fixed-click-guard-enabled").disabled = proxyMode;
}

function renderExecutionSettings(settings, proxy = {}, running = false) {
  if (!executionSettingsInitialized) {
    $("execution-mode").value = settings.execution_mode || "session_proxy";
    $("fixed-click-guard-enabled").checked =
      settings.fixed_click_guard_enabled !== false;
    $("maximum-source-save-lag-versions").value =
      settings.maximum_source_save_lag_versions ?? 2;
    $("fresh-save-seconds").value =
      settings.require_fresh_save_seconds ?? 900;
    $("autonomy-fresh-save-seconds").value =
      settings.autonomy_require_fresh_save_seconds ?? 900;
    $("maximum-constructions").value = String(
      settings.maximum_constructions_per_turn ?? 3,
    );
    $("inconclusive-rewrite-policy").value =
      settings.inconclusive_rewrite_policy || "block_until_save";
    $("session-proxy-local-ip").value = settings.session_proxy_local_ip || "";
    $("session-proxy-host-ip").value = settings.session_proxy_host_ip || "";
    $("session-proxy-source-actor").value =
      settings.session_proxy_source_actor ?? 0;
    $("session-proxy-host-actor").value =
      settings.session_proxy_host_actor ?? 1;
    $("session-proxy-acknowledged").checked =
      settings.session_proxy_acknowledged === true;
    $("experimental-fleet-tools-enabled").checked =
      settings.experimental_fleet_tools_enabled === true;
    $("experimental-fleet-attack-enabled").checked =
      settings.experimental_fleet_attack_enabled === true;
    $("experimental-fleet-coordinate-tools-enabled").checked =
      settings.experimental_fleet_coordinate_tools_enabled === true;
    $("fleet-coordinate-max-abs").value =
      settings.fleet_coordinate_max_abs ?? 1000;
    $("experimental-ship-design-tools-enabled").checked =
      settings.experimental_ship_design_tools_enabled === true;
    $("experimental-fleet-reinforcement-tools-enabled").checked =
      settings.experimental_fleet_reinforcement_tools_enabled === true;
    $("maximum-fleet-reinforcement-increase").value =
      settings.maximum_fleet_reinforcement_increase ?? 5;
    $("experimental-new-fleet-tools-enabled").checked =
      settings.experimental_new_fleet_tools_enabled === true;
    $("maximum-new-fleet-initial-ships").value =
      settings.maximum_new_fleet_initial_ships ?? 5;
    $("experimental-research-tools-enabled").checked =
      settings.experimental_research_tools_enabled === true;
    $("experimental-research-reselection-enabled").checked =
      settings.experimental_research_reselection_enabled === true;
    updateExecutionModeVisibility();
    executionSettingsInitialized = true;
  }
  const pending = Number(settings.pending_confirmation_count || 0);
  $("pending-confirmation-state").textContent = pending
    ? pending + " 项等待存档核验"
    : "无待核验动作";
  $("pending-confirmation-state").className =
    "status-tag " + (pending ? "warn" : "good");
  const protocolSuiteActive =
    protocolSuitePayload?.state?.live_active === true;
  $("save-execution-settings").disabled = running || protocolSuiteActive;
  const proxyRunning = proxy.running === true;
  const flow = proxy.flow || {};
  const flowCandidates = Array.isArray(proxy.flow_candidates)
    ? proxy.flow_candidates
    : [];
  const bidirectionalCandidates = flowCandidates.filter((candidate) =>
    candidate.bidirectional === true ||
    (Number(candidate.outbound_packets || 0) > 0 &&
      Number(candidate.inbound_packets || 0) > 0));
  const oneWayReliableCandidates = flowCandidates.filter((candidate) =>
    Number(candidate.reliable_outbound_packets || 0) +
      Number(candidate.reliable_inbound_packets || 0) > 0 &&
    !(candidate.bidirectional === true ||
      (Number(candidate.outbound_packets || 0) > 0 &&
        Number(candidate.inbound_packets || 0) > 0)));
  $("session-proxy-state").textContent = !proxyRunning
    ? "未启动"
    : (proxy.flow
      ? "可靠流已锁定"
      : (flowCandidates.length
        ? "已启动 · 正在确认双向流"
        : "已启动 · 等待游戏流量"));
  $("session-proxy-state").className =
    "status-tag " + (!proxyRunning ? "" : (proxy.flow ? "good" : "warn"));
  $("session-proxy-detail").textContent = proxyRunning
    ? (proxy.flow
      ? "PID " + (proxy.pid || "--") +
        " · " + (flow.local_ip || "--") + ":" + (flow.local_port || "--") +
        " → " + (flow.host_ip || "--") + ":" + (flow.host_port || "--") +
        " · " + (flow.route || "未知路径") +
        " · actor " + (proxy.source_actor || "等待自然命令") +
        " · 已注入 " + Number(proxy.insertion_count || 0) + " 条"
      : "PID " + (proxy.pid || "--") +
        " · 双向候选 " + bidirectionalCandidates.length +
        " · 单向可靠 " + oneWayReliableCandidates.length +
        " · 游戏/Steam UDP 端口 " +
        Number((proxy.stellaris_udp_ports || []).length || 0))
    : "尚未建立代理进程。";
  $("start-session-proxy").disabled =
    running || proxyRunning || protocolSuiteActive;
  $("stop-session-proxy").disabled =
    running || !proxyRunning || protocolSuiteActive;
  $("open-protocol-suite").disabled = running;
}

function renderFleetState(fleetState = {}, running = false) {
  const list = $("fleet-permission-list");
  list.replaceChildren();
  const fleets = Array.isArray(fleetState.fleets) ? fleetState.fleets : [];
  $("fleet-summary-state").textContent = fleetState.available
    ? fleets.length + " 支 · 总军力 " + Math.round(Number(fleetState.total_military_power || 0))
    : "等待存档";
  $("fleet-summary-state").className =
    "status-tag " + (fleetState.available ? "good" : "warn");
  if (!fleetState.available) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = fleetState.error || "尚未读取到玩家舰队。";
    list.append(empty);
    return;
  }
  for (const fleet of fleets) {
    const row = document.createElement("article");
    row.className = "fleet-permission-row";
    const identity = document.createElement("div");
    identity.className = "fleet-identity";
    const title = document.createElement("strong");
    title.textContent = fleet.display_name_hint || fleet.name_key ||
      ("Fleet " + fleet.fleet_id);
    const detail = document.createElement("small");
    detail.textContent = "ID " + fleet.fleet_id + " · 军力 " +
      Math.round(Number(fleet.military_power || 0)) + " · " +
      (fleet.availability || "UNKNOWN");
    identity.append(title, detail);
    const controls = document.createElement("div");
    controls.className = "fleet-permission-controls";
    for (const [field, labelText] of [
      ["allow_move", "移动"],
      ["allow_attack", "攻击"],
      ["allow_reinforce", "编制增援"],
    ]) {
      const label = document.createElement("label");
      label.className = "check-line";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = fleet.permission?.[field] === true;
      const movementPermission = field === "allow_move" || field === "allow_attack";
      const reinforcementPermission = field === "allow_reinforce";
      const hasVerifiedMovement = fleet.d32c_move_verified_family === true;
      const hasFleetTemplate = fleet.fleet_template_id !== null &&
        fleet.fleet_template_id !== undefined;
      input.disabled = running ||
        (movementPermission && !hasVerifiedMovement) ||
        (reinforcementPermission && !hasFleetTemplate);
      if (movementPermission && !hasVerifiedMovement) {
        input.title = "该船队不属于当前已验证的军用 d32c 移动命令族";
      } else if (reinforcementPermission && !hasFleetTemplate) {
        input.title = "最新存档中没有找到该舰队的 Fleet Manager 模板";
      }
      input.addEventListener("change", async () => {
        const moveInput = controls.querySelector('[data-permission="allow_move"]');
        const attackInput = controls.querySelector('[data-permission="allow_attack"]');
        const reinforceInput = controls.querySelector('[data-permission="allow_reinforce"]');
        try {
          await post("/api/fleet-permission", {
            fleet_id: fleet.fleet_id,
            allow_move: moveInput.checked,
            allow_attack: attackInput.checked,
            allow_reinforce: reinforceInput.checked,
          });
          message("舰队 " + fleet.fleet_id + " 的调用权限已保存");
          await refresh();
        } catch (error) {
          message(error.message, true);
          await refresh();
        }
      });
      input.dataset.permission = field;
      label.append(input, document.createTextNode(labelText));
      controls.append(label);
    }
    row.append(identity, controls);
    list.append(row);
  }
}

function activeCampaignItem(catalog = campaignCatalog) {
  return (catalog?.conversations || []).find(
    (item) => item.conversation_id === catalog.active_conversation_id,
  );
}

function closeCampaignEditor() {
  $("campaign-editor").hidden = true;
  campaignEditorConversationId = null;
  $("campaign-title-input").value = "";
}

function openCampaignEditor(mode, item = null) {
  campaignEditorMode = mode;
  campaignEditorConversationId = item?.conversation_id || null;
  $("campaign-editor").hidden = false;
  $("campaign-title-input").value = item?.title || "";
  $("campaign-bind-option").hidden = mode === "rename";
  $("campaign-bind-current").checked = false;
  $("campaign-editor-save").textContent =
    mode === "rename" ? "保存名称" : "建立会话";
  $("campaign-title-input").focus();
}

async function switchCampaignConversation(conversationId) {
  if (!conversationId || conversationId === activeConversationId) return;
  const value = await post("/api/conversations/switch", {
    conversation_id: conversationId,
  });
  renderCampaignCatalog(value, false);
  loadedConversationId = null;
  await Promise.all([loadConversation(true), refresh()]);
  message("已切换战役会话");
}

function campaignBadge(item) {
  if (item.active) return "ACTIVE";
  if (item.archived) return "ARCHIVED";
  if (item.is_current_save) return "CURRENT SAVE";
  return "";
}

function renderCampaignCatalog(catalog, running = currentJobRunning) {
  if (!catalog) return;
  campaignCatalog = catalog;
  activeConversationId = catalog.active_conversation_id || null;
  const allConversations = catalog.conversations || [];
  const conversations = $("show-archived-campaigns").checked
    ? allConversations
    : allConversations.filter((item) => !item.archived);
  const binding = catalog.binding || {};
  const active = activeCampaignItem(catalog);

  $("campaign-count").textContent =
    conversations.filter((item) => !item.archived).length + " 局";
  $("conversation-title").textContent =
    "灰风 · " + (active?.title || "本局上下文");

  const bindingCard = $("campaign-binding-card");
  bindingCard.dataset.state = binding.state || "unbound";
  const bindingLabels = {
    ready: "当前存档 · " + (binding.current_campaign_label || "未命名存档"),
    unbound: "当前存档未绑定 · " +
      (binding.current_campaign_label || "未命名存档"),
    assigned_elsewhere: "当前存档已绑定到 · " +
      (binding.current_bound_conversation_title || "其他会话"),
    no_current_save: "等待房主存档",
  };
  $("campaign-binding-label").textContent =
    bindingLabels[binding.state] || "等待存档绑定";
  $("campaign-binding-message").textContent =
    binding.message || "每局游戏使用独立的上下文、规划与审计记录。";

  const bindingTarget = $("campaign-binding-target");
  bindingTarget.replaceChildren();
  const unboundOption = document.createElement("option");
  unboundOption.value = "";
  unboundOption.textContent = "暂不绑定任何会话";
  bindingTarget.appendChild(unboundOption);
  for (const item of allConversations.filter((entry) => !entry.archived)) {
    const option = document.createElement("option");
    option.value = item.conversation_id;
    option.textContent =
      item.title +
      (item.campaign_label
        ? " · 当前绑定 " + item.campaign_label
        : " · 未绑定");
    bindingTarget.appendChild(option);
  }
  bindingTarget.value = binding.current_bound_conversation_id || "";
  bindingTarget.disabled =
    running || binding.state === "no_current_save";

  const applyBinding = $("apply-campaign-binding");
  applyBinding.disabled =
    running ||
    binding.state === "no_current_save" ||
    (binding.current_bound_conversation_id || "") === bindingTarget.value;
  const openBound = $("open-bound-campaign");
  openBound.hidden =
    !binding.current_bound_conversation_id ||
    binding.current_bound_conversation_id === activeConversationId;
  openBound.disabled = running;

  const list = $("campaign-list");
  $("new-campaign").disabled = running;
  $("show-archived-campaigns").disabled = running;
  list.replaceChildren();
  for (const item of conversations) {
    const card = document.createElement("article");
    card.className = "campaign-card";
    if (item.active) card.classList.add("active");
    if (item.archived) card.classList.add("archived");
    card.dataset.conversationId = item.conversation_id;
    if (!item.active && !item.archived) {
      card.addEventListener("click", () => {
        switchCampaignConversation(item.conversation_id)
          .catch((error) => message(error.message, true));
      });
    }

    const titleRow = document.createElement("div");
    titleRow.className = "campaign-card-title";
    const title = document.createElement("strong");
    title.textContent = item.title || "未命名战役";
    const badge = document.createElement("span");
    badge.className = "campaign-card-badge";
    badge.textContent = campaignBadge(item);
    titleRow.append(title, badge);

    const metadata = document.createElement("div");
    metadata.className = "campaign-card-meta";
    const gameDate = item.last_game_date || "日期未知";
    const campaignLabel = item.campaign_label || "未绑定存档";
    metadata.textContent =
      campaignLabel + " · " + gameDate + " · " +
      (item.stored_messages || 0) + " 条记录";

    const actions = document.createElement("div");
    actions.className = "campaign-card-actions";
    const rename = document.createElement("button");
    rename.type = "button";
    rename.textContent = "重命名";
    rename.disabled = running;
    rename.addEventListener("click", (event) => {
      event.stopPropagation();
      openCampaignEditor("rename", item);
    });
    const archive = document.createElement("button");
    archive.type = "button";
    archive.textContent = item.archived ? "恢复" : "归档";
    archive.disabled = running;
    archive.addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        const value = await post("/api/conversations/archive", {
          conversation_id: item.conversation_id,
          archived: !item.archived,
        });
        renderCampaignCatalog(value, false);
        loadedConversationId = null;
        await Promise.all([loadConversation(true), refresh()]);
        message(item.archived ? "战役会话已恢复" : "战役会话已归档");
      } catch (error) {
        message(error.message, true);
      }
    });
    actions.append(rename, archive);
    card.append(titleRow, metadata, actions);
    list.appendChild(card);
  }

  if (!conversations.length) {
    const empty = document.createElement("p");
    empty.className = "conversation-empty";
    empty.textContent = "没有符合条件的战役会话。";
    list.appendChild(empty);
  }
}

async function loadCampaignCatalog() {
  const includeArchived = $("show-archived-campaigns").checked ? "1" : "0";
  const value = await api(
    "/api/conversations?include_archived=" + includeArchived,
  );
  renderCampaignCatalog(value, currentJobRunning);
}

async function applyCurrentCampaignBinding() {
  const binding = campaignCatalog?.binding || {};
  const selectedId = $("campaign-binding-target").value || null;
  const conversations = campaignCatalog?.conversations || [];
  const selected = conversations.find(
    (item) => item.conversation_id === selectedId,
  );
  const currentHolder = conversations.find(
    (item) =>
      item.conversation_id ===
      (
        binding.current_bound_conversation_id ||
        binding.campaign_id_holder_conversation_id
      ),
  );
  const warnings = [];
  if (currentHolder && currentHolder.conversation_id !== selectedId) {
    warnings.push("当前存档将解除与“" + currentHolder.title + "”的绑定。");
  }
  if (
    selected?.campaign_id &&
    selected.campaign_id !== binding.current_campaign_id
  ) {
    warnings.push(
      "“" + selected.title + "”将解除与 " +
      (selected.campaign_label || "原存档") + " 的绑定。",
    );
  }
  if (
    warnings.length &&
    !window.confirm(warnings.join("\n") + "\n\n确认应用新的对应关系？")
  ) {
    return;
  }
  const value = await post("/api/conversations/bind-current", {
    conversation_id: selectedId,
  });
  renderCampaignCatalog(value, false);
  await refresh();
  message("当前存档的会话绑定已更新；当前打开会话未自动切换");
}

const roleLabels = {
  amenities: "舒适度",
  alloys: "合金",
  consumer_goods: "消费品",
  crime: "治安",
  energy: "能源",
  food: "食物",
  fortress: "防御",
  housing: "住房",
  minerals: "矿物",
  research: "科研",
  trade: "贸易",
  unity: "凝聚力",
};

function catalogGroup(catalog, actionType) {
  return (catalog?.groups || []).find(
    (item) => item.action_type === actionType,
  );
}

function catalogEntryState(entry) {
  if (entry.selected) return ["本轮目标", "selected"];
  if (!entry.enabled) return ["等待抓包验证", "pending"];
  if (!entry.planner_enabled) return ["传输已验证 · 规划待开放", "guarded"];
  if (entry.legal_candidate_count > 0) {
    return [entry.legal_candidate_count + " 个合法落点", "ready"];
  }
  return ["当前存档无合法落点", "idle"];
}

function renderConstructionCatalog(catalog, manifest) {
  if (!catalog) return;
  const selectedType = catalog.selected_action_type;
  const selectedGroup = catalogGroup(catalog, selectedType);
  const selectedId = selectedGroup?.selected_object_id || "";
  const nextSelectionKey = selectedType && selectedId
    ? selectedType + ":" + selectedId
    : null;
  if (nextSelectionKey && nextSelectionKey !== catalogSelectionKey) {
    catalogActionType = selectedType;
    catalogSelectionKey = nextSelectionKey;
  }

  for (const group of catalog.groups || []) {
    const suffix = group.action_type.replaceAll("_", "-");
    const count = $("catalog-count-" + suffix);
    if (count) count.textContent = String(group.entries?.length || 0);
  }

  const group = catalogGroup(catalog, catalogActionType) ||
    catalog.groups?.[0];
  if (!group) return;
  const carrier = group.carrier || {};
  const calibration = carrier.calibration || {};
  $("catalog-carrier-source").textContent =
    (carrier.planet_name_zh || "川陀") + " · " +
    (carrier.source_label_zh || carrier.command || "--");
  $("catalog-carrier-route").textContent =
    (carrier.source_position_zh || "固定载体") + " · " +
    (calibration.calibrated_step_count || 0) + "/" +
    (calibration.required_step_count || carrier.navigation_step_count || 1) +
    " 步已校准";
  $("catalog-ready-state").textContent = carrier.sequence_ready
    ? "载体序列已就绪"
    : "载体序列未完成";
  $("catalog-ready-state").className =
    "status-tag " + (carrier.sequence_ready ? "good" : "warn");

  const selectedEntry = (selectedGroup?.entries || []).find(
    (item) => item.selected,
  );
  $("catalog-current-target").textContent = selectedEntry
    ? selectedEntry.label_zh + " · " + selectedEntry.object_id
    : "本轮不建设";
  const action = manifest?.action || {};
  const exactSlot = ["upgrade_building", "replace_building"].includes(action.type)
    ? " · 区域 " + action.zone_id + " / 槽位 " + action.building_position +
      " / 对象 " + action.building_object_id
    : "";
  $("catalog-current-location").textContent = selectedEntry
    ? (action.planet_name_key || ("planet " + (action.planet_id ?? "?"))) +
      exactSlot + " · 已自动置顶"
    : "模型选择目标后，它会显示在所属目录第一位。";

  for (const tab of document.querySelectorAll(".catalog-tab")) {
    tab.classList.toggle(
      "active",
      tab.dataset.catalogAction === group.action_type,
    );
  }

  const list = $("construction-catalog-list");
  list.replaceChildren();
  for (const entry of group.entries || []) {
    const [stateLabel, stateClass] = catalogEntryState(entry);
    const card = document.createElement("article");
    card.className = "construction-catalog-item " + stateClass;
    const heading = document.createElement("div");
    heading.className = "construction-catalog-item-heading";
    const title = document.createElement("strong");
    title.textContent = entry.label_zh;
    const badge = document.createElement("span");
    badge.textContent = stateLabel;
    heading.append(title, badge);
    const objectId = document.createElement("code");
    objectId.textContent = entry.object_id;
    const meta = document.createElement("p");
    meta.textContent =
      (roleLabels[entry.role] || entry.role || "通用") + " · " +
      (entry.verified_transport || "尚无传输说明");
    card.append(heading, objectId, meta);
    list.appendChild(card);
  }
  if (!list.children.length) {
    const empty = document.createElement("p");
    empty.className = "catalog-empty";
    empty.textContent = "此类建设尚未登记任何经过本地验证的目标。";
    list.appendChild(empty);
  }
}

function renderAutonomy(status) {
  const autonomy = status.autonomy || {};
  const mode = autonomy.mode || "paused";
  const labels = {
    paused: "自动巡检已暂停",
    advisory: "自主分析 · 不自动点击",
    execute: "自主建设已启用",
  };
  if (document.activeElement !== $("autonomy-mode")) {
    $("autonomy-mode").value = mode;
  }
  const interval = String(status.save_ingest?.review_interval_months || 1);
  if (document.activeElement !== $("save-review-interval")) {
    $("save-review-interval").value = interval;
  }
  $("autonomy-state").textContent = labels[mode] || mode;
  $("autonomy-state").className =
    "status-tag " + (mode === "execute" ? "good" : (mode === "advisory" ? "warn" : ""));
}

function renderConversationApplicationSelector(model, running) {
  const select = $("conversation-application");
  const routes = model?.application_agents || [];
  const previous = selectedConversationApplicationId || select.value;
  select.replaceChildren();
  for (const route of routes) {
    const option = document.createElement("option");
    option.value = route.application_id;
    const suffixes = [];
    if (route.binding_mode === "inherited") suffixes.push("沿用经济模型");
    if (!route.tools_enabled) suffixes.push("功能未启用");
    if (!route.conversation_supported) suffixes.push("模型不支持工具");
    option.textContent = route.display_name +
      (suffixes.length ? " · " + suffixes.join(" · ") : "");
    option.disabled = !route.tools_enabled || !route.conversation_supported;
    select.appendChild(option);
  }
  const preferred = routes.find(
    (route) => route.application_id === previous &&
      route.tools_enabled && route.conversation_supported,
  );
  const fallback = routes.find(
    (route) => route.application_id === "economy_governance",
  ) || routes.find((route) => route.tools_enabled && route.conversation_supported);
  selectedConversationApplicationId =
    preferred?.application_id || fallback?.application_id || "economy_governance";
  select.value = selectedConversationApplicationId;
  select.disabled = running || !routes.length;
  const selected = routes.find(
    (route) => route.application_id === selectedConversationApplicationId,
  );
  $("chat-input").placeholder = selected
    ? "向" + selected.display_name + "下达要求或询问本领域状态。Enter 发送，Shift+Enter 换行。"
    : "告诉灰风新的要求。Enter 发送，Shift+Enter 换行。";
  return selected;
}

function renderStatus(status) {
  latestStatus = status;
  const network = status.network || {};
  const bridgeClient = status.save_ingest?.client || {};
  const bridgeReady = Boolean(
    bridgeClient.connected &&
    bridgeClient.host_executor_ready &&
    bridgeClient.source_ip
  );
  const networkConfirmed = [
    "bidirectional_flow_observed",
    "carrier_command_verified",
    "authoritative_confirmed",
  ].includes(network.confidence);
  const bridgeWaitingForCarrier = bridgeReady && !networkConfirmed;
  const level = bridgeWaitingForCarrier
    ? ["房主执行桥已连接", "warn", "BRIDGE READY"]
    : (confidence[network.confidence] || confidence.offline);
  $("connection-label").textContent = level[0];
  $("connection-pill").dataset.state = level[1];
  $("network-confidence").textContent = level[2];
  $("network-confidence").className = "status-tag " + level[1];

  const telemetry = network.telemetry || {};
  const endpoint = network.selected_endpoint || {};
  const verification = network.port_verification || {};
  const livePorts = network.live_ports || {};
  const ports = verification.telemetry_fresh ? livePorts : {};
  $("process-state").textContent = network.processes?.length
    ? "已发现 PID " + network.processes.map((item) => item.pid).join(", ")
    : "未发现";
  $("port-verdict").textContent = bridgeWaitingForCarrier
    ? "房主执行桥已认证；UDP 端口将在载体命令到达时锁定"
    : (verification.summary_zh || "尚未确认本局端口");
  $("port-verdict").className =
    "port-verdict " + (verification.severity || "offline");
  $("host-ip").textContent =
    telemetry.host_ip || network.configured_host_ip || endpoint.remote_ip ||
    bridgeClient.source_ip || "--";
  $("local-port").textContent =
    ports.local_udp_port || endpoint.local_port ||
    (bridgeWaitingForCarrier ? "待载体" : "--");
  $("host-out-port").textContent =
    ports.host_destination_port || endpoint.remote_port ||
    (bridgeWaitingForCarrier ? "待载体" : "--");
  $("host-in-port").textContent =
    ports.host_source_port || (bridgeWaitingForCarrier ? "待载体" : "--");
  $("packet-count").textContent =
    (telemetry.outbound_packets || 0) + " → / ← " +
    (telemetry.inbound_packets || 0);
  $("interceptor-phase").textContent = telemetry.phase ||
    (bridgeWaitingForCarrier ? "房主桥待命" : "未武装");
  $("telemetry-time").textContent = telemetry.updated_at || "--";
  if (telemetry.phase === "passive_read_only") {
    $("interceptor-phase").textContent = "只读监听（不改包）";
  }

  const save = status.save || {};
  const run = status.latest_run || {};
  const snapshot = run.snapshot || {};
  const plan = run.plan || {};
  const manifest = run.manifest || {};
  $("game-date").textContent = snapshot.game_date || "--";
  $("planet-count").textContent = snapshot.planet_count ?? "--";
  $("risk-level").textContent = plan.assessment?.risk_level || "--";
  $("save-name").textContent = save.original_name || save.name || "--";
  $("save-time").textContent = save.received_at || save.modified_at || "--";
  $("save-freshness").textContent = formatSaveFreshness(save, snapshot);
  const saveIngest = status.save_ingest || {};
  const lastUpload = saveIngest.last_upload || {};
  const saveClient = saveIngest.client || {};
  $("save-upload-state").textContent = saveClient.connected
    ? "GUI 在线，等待或接收 autosave"
    : (lastUpload.received_at
      ? "GUI 已离线 · 保留最后存档"
      : (saveIngest.upload_ready ? "接收器就绪，等待 GUI" : "上传令牌未配置"));
  $("save-client-connection").textContent = saveClient.connected
    ? (saveClient.hostname || "Windows 房主") + " @ " + (saveClient.source_ip || "--")
    : (saveClient.last_seen_at ? "已断开 · " + saveClient.last_seen_at : "未连接");
  $("host-bridge-state").textContent = saveClient.connected
    ? (saveClient.host_executor_ready
      ? "READY · 房主入站 WinDivert 可用"
      : "客户端过旧或未以管理员身份运行 · 禁止载体点击")
    : "未连接 · 禁止载体点击";
  $("save-campaign").textContent =
    save.campaign_label || lastUpload.campaign_label || saveClient.campaign_label || "--";
  renderResource(snapshot, "energy", "energy");
  renderResource(snapshot, "minerals", "minerals");
  renderResource(snapshot, "food", "food");
  renderResource(snapshot, "consumer_goods", "consumer-goods");
  renderResource(snapshot, "alloys", "alloys");
  renderResource(snapshot, "unity", "unity");
  renderResource(snapshot, "research_total", "research");
  const localRules = run.local_rules || {};
  const localVersion = localRules.install?.normalized_version;
  $("rules-version").textContent = localVersion
    ? localVersion + (localRules.version_matches_save === false ? " · 与存档不匹配" : "")
    : "尚未解析";
  const webResearch = run.web_research || {};
  const webResultCount = (webResearch.searches || [])
    .reduce((sum, item) => sum + (item.results?.length || 0), 0);
  $("web-research-state").textContent = webResearch.status
    ? webResearch.status + " · " + webResultCount + " 条来源"
    : "已暂停";
  $("plan-action").textContent = formatAction(manifest);
  $("plan-reason").textContent =
    plan.reasoning_zh ||
    "灰风会读取状态，并在本地上限内逐项准备、执行和确认建设；数量由当前经济决定。";

  const job = status.job || {};
  const running = job.state === "running";
  currentJobRunning = running;
  const binding = status.campaign_binding || {};
  const executionAllowed = Boolean(binding.execution_allowed);
  const selectedConversationRoute = renderConversationApplicationSelector(
    status.model || {},
    running,
  );
  $("job-state").textContent = running
    ? job.kind + " 运行中"
    : (job.message || job.state || "空闲");
  for (const id of ["plan-button", "send-message", "review-button", "save-autonomy"]) {
    $(id).disabled = running;
  }
  if (!selectedConversationRoute?.conversation_supported) {
    $("send-message").disabled = true;
    $("review-button").disabled = true;
  }
  $("plan-button").disabled = running || !executionAllowed;
  $("review-button").disabled = running || !executionAllowed;
  $("execute-button").disabled = running || !executionAllowed ||
    !run.manifest || manifest.action?.type === "noop";
  const emergencyStopRequested = Boolean(status.emergency_stop_requested);
  $("stop-button").disabled = emergencyStopRequested;
  $("clear-stop-button").disabled = running || !emergencyStopRequested;
  $("execution-interlock-state").textContent = emergencyStopRequested
    ? "紧急停止已锁定"
    : "执行锁未启用";
  $("execution-interlock-state").className =
    "status-tag " + (emergencyStopRequested ? "bad" : "good");

  const calibrationAction = selectedCalibrationAction();
  const calibrationStage = selectedCalibrationStage();
  const actionCalibration = status.calibration_steps?.[calibrationAction] || {};
  const calibration = actionCalibration.steps?.[calibrationStage]?.profile;
  $("calibration-state").textContent = calibration
    ? "此步已校准 · " +
      (actionCalibration.calibrated_step_count || 0) + "/" +
      (actionCalibration.required_step_count || 1)
    : "此步未校准";
  $("calibration-state").className =
    "status-tag " + (calibration ? "good" : "warn");
  $("test-button").disabled = !calibration;
  renderConstructionCatalog(status.construction_catalog, manifest);
  mergeModelPoolHealth(status.model || {});
  const endpointTotal = status.model?.configured_endpoint_total ??
    status.model?.model_pool?.endpoints?.length ?? 0;
  const configuredEndpoints = status.model?.configured_endpoint_count ??
    (status.model?.api_key_configured ? 1 : 0);
  $("key-state").textContent =
    configuredEndpoints + "/" + endpointTotal + " 端点凭据就绪";
  $("key-state").className =
    "status-tag " + (configuredEndpoints > 0 ? "good" : "warn");
  $("thinking-state").textContent =
    status.model?.thinking_enabled
      ? "思考模式 · " + (status.model.reasoning_effort || "default")
      : "标准模式";
  $("thinking-state").className =
    "status-tag " + (status.model?.thinking_enabled ? "good" : "");
  $("tool-state").textContent = status.model?.conversation_supported
    ? "持久工具会话已启用"
    : "需 Chat Completions 工具协议";
  $("tool-state").className =
    "status-tag " + (status.model?.conversation_supported ? "good" : "warn");
  $("research-tool-state").textContent = status.model?.web_research_enabled
    ? "联网研究工具已启用"
    : "联网研究已关闭";
  $("research-tool-state").className =
    "status-tag " + (status.model?.web_research_enabled ? "good" : "");
  const conversation = status.conversation || {};
  $("conversation-count").textContent = conversation.stored_messages ?? 0;
  const nextReview = conversation.next_review || {};
  const dueGameDate = gameDateFromMonthIndex(nextReview.due_month_index);
  const coalesced = Number(nextReview.coalesced_missed_intervals || 0);
  $("next-review").textContent = dueGameDate
    ? "下次复查：" + dueGameDate + (coalesced ? "（已合并 " + coalesced + " 个过期触发点）" : "")
    : "尚未安排复查";
  renderCampaignCatalog(status.conversations, running);
  renderAutonomy(status);
  renderStrategy(
    status.strategy || {},
    status.conversation?.conversation_id || activeConversationId,
    running,
  );
  renderExecutionSettings(
    status.execution_settings || {},
    status.session_proxy || {},
    running,
  );
  renderFleetState(status.fleet_state || {}, running);

  if (!initialized) initializeModel(status.model || {});
  $("last-refresh").textContent = "刷新 " + new Date().toLocaleTimeString();
  initialized = true;
}

function conversationRoleLabel(item) {
  const applicationName = item.metadata?.application_display_name;
  if (item.kind === "autonomy_trigger") {
    return applicationName ? applicationName + "巡检" : "自主巡检";
  }
  if (item.role === "user") {
    return applicationName ? "玩家 → " + applicationName : "玩家";
  }
  if (item.role === "assistant") return applicationName || "灰风";
  if (item.role === "tool") {
    return applicationName ? applicationName + "工具审计" : "工具审计";
  }
  return "系统";
}

function appendConversationMessage(item) {
  const messageKey = String(loadedConversationId || "unknown") + ":" + item.id;
  if (renderedConversationMessageIds.has(messageKey)) return false;
  renderedConversationMessageIds.add(messageKey);
  const log = $("conversation-log");
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  const article = document.createElement("article");
  article.className = "conversation-message role-" + item.role + " kind-" + item.kind;
  article.dataset.messageId = item.id;

  const heading = document.createElement("div");
  heading.className = "conversation-message-heading";
  const role = document.createElement("strong");
  role.textContent = conversationRoleLabel(item);
  const time = document.createElement("time");
  const parsed = new Date(item.created_at);
  time.textContent = Number.isNaN(parsed.getTime())
    ? item.created_at
    : parsed.toLocaleString("zh-CN");
  heading.append(role, time);

  const body = document.createElement("p");
  body.textContent = item.content || "";
  article.append(heading, body);
  log.appendChild(article);
  if (nearBottom) log.scrollTop = log.scrollHeight;
  return true;
}

async function loadConversation(reset = false) {
  if (
    reset ||
    (activeConversationId && loadedConversationId !== activeConversationId)
  ) {
    conversationLastId = 0;
    renderedConversationMessageIds.clear();
    $("conversation-log").replaceChildren();
  }
  const requestedAfterId = conversationLastId;
  const value = await api(
    "/api/conversation?after_id=" + requestedAfterId + "&limit=500",
  );
  if (
    loadedConversationId !== null &&
    value.conversation_id !== loadedConversationId
  ) {
    loadedConversationId = value.conversation_id;
    conversationLastId = 0;
    renderedConversationMessageIds.clear();
    $("conversation-log").replaceChildren();
    if (requestedAfterId !== 0) return loadConversation(false);
  } else {
    loadedConversationId = value.conversation_id;
  }
  const entries = value.messages || [];
  if (entries.length) $("conversation-empty")?.remove();
  if (!entries.length && conversationLastId === 0 && !$("conversation-empty")) {
    const empty = document.createElement("p");
    empty.className = "conversation-empty";
    empty.id = "conversation-empty";
    empty.textContent = "会话尚未建立。";
    $("conversation-log").appendChild(empty);
  }
  for (const item of entries) {
    appendConversationMessage(item);
    conversationLastId = Math.max(conversationLastId, Number(item.id) || 0);
  }
}

function protocolScenarioById(scenarioId, payload = protocolSuitePayload) {
  return (payload?.plan?.scenarios || []).find(
    (item) => item.id === scenarioId,
  );
}

function protocolSpecByAction(action, payload = protocolSuitePayload) {
  return (payload?.catalog?.commands || []).find(
    (item) => item.action === action,
  );
}

function protocolReportItem(scenarioId, payload = protocolSuitePayload) {
  return (payload?.report?.scenarios || []).find(
    (item) => item.id === scenarioId,
  );
}

function commitProtocolScenarioEditor() {
  const plan = protocolSuitePayload?.plan;
  if (!plan || !protocolSuitePayload?.actions?.can_edit_plan) return plan;
  plan.game_version = $("protocol-game-version").value.trim();
  plan.game_build = $("protocol-game-build").value.trim();
  const scenario = protocolScenarioById(selectedProtocolScenarioId);
  if (!scenario) return plan;
  let target;
  try {
    target = JSON.parse($("protocol-scenario-target").value || "{}");
  } catch (error) {
    throw new Error("当前命令目标不是有效 JSON：" + error.message);
  }
  if (!target || Array.isArray(target) || typeof target !== "object") {
    throw new Error("当前命令目标必须是 JSON 对象。");
  }
  scenario.enabled = $("protocol-scenario-enabled").checked;
  scenario.acknowledge_side_effects = $("protocol-scenario-ack").checked;
  scenario.target = target;
  const template = $("protocol-template-record-hex").value.trim();
  if (template) scenario.template_record_hex = template;
  else delete scenario.template_record_hex;
  protocolSuitePayload.offline_current = false;
  return plan;
}

function protocolScenarioResult(scenario, reportItem) {
  if (!scenario.enabled) return ["未启用", ""];
  if (!reportItem) return ["等待离线检查", "warn"];
  if (
    reportItem.network_stage === "network_confirmed" &&
    reportItem.operator_verdict === "passed"
  ) return ["联机通过", "good"];
  if (
    ["action_not_applied", "unexpected_result", "desync_or_stalled_clock"]
      .includes(reportItem.operator_verdict) ||
    ["controller_error", "authoritative_response_not_matched",
      "host_ack_or_authoritative_response_missing", "proxy_result_unrecognized"]
      .includes(reportItem.network_stage)
  ) return ["验收失败", "bad"];
  if (reportItem.operator_verdict === "skipped") return ["已跳过", "warn"];
  if (reportItem.network_stage === "network_confirmed") {
    return ["等待玩家核对", "warn"];
  }
  if (reportItem.offline_status === "passed") return ["离线通过", "good"];
  if (reportItem.offline_status === "unconfigured") {
    return ["待填写联机目标", "warn"];
  }
  if (reportItem.offline_status === "build_failed") return ["构包失败", "bad"];
  return [reportItem.offline_status || "等待检查", "warn"];
}

function renderProtocolSuite(payload) {
  protocolSuitePayload = payload;
  const plan = payload?.plan;
  const state = payload?.state || {};
  const actions = payload?.actions || {};
  const liveActive = state.live_active === true;
  const enabledCount = (plan?.scenarios || []).filter(
    (item) => item.enabled === true,
  ).length;
  if (liveActive && state.current_scenario_id) {
    selectedProtocolScenarioId = state.current_scenario_id;
  } else if (
    !selectedProtocolScenarioId ||
    !protocolScenarioById(selectedProtocolScenarioId, payload)
  ) {
    selectedProtocolScenarioId = plan?.scenarios?.[0]?.id || null;
  }

  $("protocol-game-version").value = plan?.game_version || "";
  $("protocol-game-build").value = plan?.game_build || "";
  const detected = payload?.game_install?.normalized_version || "未检测到";
  $("protocol-detected-version").textContent = detected;
  $("protocol-plan-integrity").textContent = !plan
    ? "尚未创建计划"
    : (payload.offline_current
      ? (state.live_targets_ready
        ? "离线检查有效，联机目标已就绪"
        : "离线检查有效，仍有联机目标待填写")
      : "计划尚未通过离线检查");
  $("protocol-enabled-count").textContent =
    enabledCount + " / " +
    Number(payload?.catalog?.commands?.length || 0);

  const reportById = new Map(
    (payload?.report?.scenarios || []).map((item) => [item.id, item]),
  );
  const commandList = $("protocol-command-list");
  commandList.replaceChildren();
  for (const scenario of plan?.scenarios || []) {
    const spec = protocolSpecByAction(scenario.action, payload) || {};
    const result = protocolScenarioResult(scenario, reportById.get(scenario.id));
    const button = document.createElement("button");
    button.type = "button";
    button.className = "protocol-command-item";
    if (scenario.id === selectedProtocolScenarioId) button.classList.add("active");
    button.dataset.enabled = String(scenario.enabled === true);
    button.dataset.result = result[1] === "good"
      ? "passed"
      : (result[1] === "bad" ? "failed" : "pending");
    const title = document.createElement("strong");
    title.textContent = spec.title || scenario.action;
    const detail = document.createElement("small");
    detail.textContent = scenario.action + " · " + result[0];
    button.append(title, detail);
    button.addEventListener("click", () => {
      try {
        commitProtocolScenarioEditor();
        selectedProtocolScenarioId = scenario.id;
        renderProtocolSuite(protocolSuitePayload);
      } catch (error) {
        message(error.message, true);
      }
    });
    commandList.append(button);
  }

  const scenario = protocolScenarioById(selectedProtocolScenarioId, payload);
  const spec = protocolSpecByAction(scenario?.action, payload) || {};
  const reportItem = scenario ? reportById.get(scenario.id) : null;
  const scenarioResult = scenario
    ? protocolScenarioResult(scenario, reportItem)
    : ["未配置", ""];
  $("protocol-scenario-title").textContent =
    spec.title || scenario?.action || "请选择一个命令";
  $("protocol-scenario-group").textContent =
    (spec.group || "COMMAND").toUpperCase();
  $("protocol-scenario-action").textContent = scenario?.action || "--";
  $("protocol-scenario-wire-family").textContent = spec.wire_family_hex || "--";
  $("protocol-scenario-verification").textContent = spec.verification_state || "--";
  $("protocol-scenario-setup").textContent = scenario?.setup || spec.setup || "--";
  $("protocol-scenario-check").textContent =
    scenario?.operator_check || spec.operator_check || "--";
  $("protocol-scenario-enabled").checked = scenario?.enabled === true;
  $("protocol-scenario-ack").checked =
    scenario?.acknowledge_side_effects === true;
  $("protocol-scenario-target").value = scenario
    ? JSON.stringify(scenario.target || {}, null, 2)
    : "";
  $("protocol-template-record-hex").value =
    scenario?.template_record_hex || "";
  $("protocol-scenario-status").textContent = scenarioResult[0];
  $("protocol-scenario-status").className =
    "status-tag " + (scenarioResult[1] || "");

  const planEditorDisabled = protocolSuiteBusy || !actions.can_edit_plan;
  $("protocol-game-version").disabled = planEditorDisabled;
  $("protocol-game-build").disabled = planEditorDisabled;
  const editorDisabled = planEditorDisabled || !scenario;
  for (const id of [
    "protocol-scenario-enabled", "protocol-scenario-ack",
    "protocol-scenario-target", "protocol-template-record-hex",
  ]) $(id).disabled = editorDisabled;

  const phaseLabels = {
    idle: "尚未开始",
    plan_ready: "计划待检查",
    offline_running: "离线检查中",
    offline_passed: "离线检查通过",
    offline_failed: "离线检查失败",
    waiting_for_room: "等待进房锁流",
    ready_for_action: "当前项可执行",
    executing_action: "等待房主回包",
    awaiting_operator_verdict: "等待玩家核对",
    halted: "异常停止",
    completed: "启用项已完成",
    finished: "验收已结束",
  };
  $("protocol-phase-label").textContent =
    phaseLabels[state.phase] || state.phase || "尚未开始";
  $("protocol-workflow-message").textContent =
    state.workflow_message || "创建计划后开始检查。";
  const proxy = payload?.proxy || {};
  $("protocol-proxy-summary").textContent = !proxy.running
    ? "代理未运行"
    : (proxy.flow_locked
      ? "代理运行中 · 已锁流 · " + (proxy.route || "未知路径") +
        " · 已注入 " + Number(proxy.insertion_count || 0) + " 条"
      : "代理运行中 · 等待双向可靠流");

  $("new-protocol-plan").disabled = protocolSuiteBusy || liveActive;
  $("save-protocol-plan").disabled =
    protocolSuiteBusy || !actions.can_edit_plan || !plan;
  $("enable-all-protocol-scenarios").disabled =
    protocolSuiteBusy || !actions.can_edit_plan || !plan;
  $("run-protocol-offline-check").disabled =
    protocolSuiteBusy || !actions.can_run_offline;
  $("start-protocol-suite").disabled =
    protocolSuiteBusy || !actions.can_start_live ||
    !$("protocol-disposable-authorized").checked;
  $("confirm-protocol-room").disabled =
    protocolSuiteBusy || !actions.can_confirm_room;
  $("execute-protocol-action").disabled =
    protocolSuiteBusy || !actions.can_execute;
  $("skip-protocol-action").disabled =
    protocolSuiteBusy || !actions.can_skip;
  $("protocol-verdict-actions").hidden = !actions.can_record_verdict;
  for (const button of document.querySelectorAll("[data-protocol-verdict]")) {
    button.disabled = protocolSuiteBusy || !actions.can_record_verdict;
  }
  $("finish-protocol-suite").disabled =
    protocolSuiteBusy || !actions.can_finish;
  $("reset-protocol-suite").disabled =
    protocolSuiteBusy || !actions.can_reset;
  $("download-protocol-json").hidden = !payload?.report;
  $("download-protocol-markdown").hidden = !payload?.report;
}

async function refreshProtocolSuite() {
  if (protocolSuitePollInFlight || protocolSuiteBusy) return protocolSuitePayload;
  protocolSuitePollInFlight = true;
  try {
    const payload = await api("/api/protocol-compatibility");
    renderProtocolSuite(payload);
    return payload;
  } finally {
    protocolSuitePollInFlight = false;
  }
}

async function runProtocolSuiteOperation(operation, successMessage) {
  protocolSuiteBusy = true;
  if (protocolSuitePayload) renderProtocolSuite(protocolSuitePayload);
  try {
    const payload = await operation();
    renderProtocolSuite(payload);
    if (successMessage) message(successMessage);
    return payload;
  } catch (error) {
    message(error.message, true);
    throw error;
  } finally {
    protocolSuiteBusy = false;
    if (protocolSuitePayload) renderProtocolSuite(protocolSuitePayload);
  }
}

async function saveProtocolSuitePlan() {
  const plan = commitProtocolScenarioEditor();
  if (!plan?.game_version) throw new Error("目标游戏版本不能为空。");
  return post("/api/protocol-compatibility/plan/save", {plan});
}

async function refresh() {
  try {
    renderStatus(await api("/api/status"));
  } catch (error) {
    message("状态刷新失败：" + error.message, true);
  }
}

async function loadPrompt() {
  const value = await api("/api/prompt");
  $("prompt").value = value.text || "";
  $("prompt-version-count").textContent =
    (value.versions?.length || 0) + " 历史版本";
}

$("download-host-bridge").addEventListener("click", downloadPairedHostBridge);

$("new-campaign").addEventListener("click", () => {
  openCampaignEditor("create");
});

$("campaign-editor-cancel").addEventListener("click", closeCampaignEditor);

$("campaign-editor").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = $("campaign-title-input").value.trim();
  try {
    let value;
    if (campaignEditorMode === "rename") {
      value = await post("/api/conversations/rename", {
        conversation_id: campaignEditorConversationId,
        title,
      });
    } else {
      value = await post("/api/conversations/create", {
        title,
        bind_current: $("campaign-bind-current").checked,
      });
      loadedConversationId = null;
    }
    closeCampaignEditor();
    renderCampaignCatalog(value, false);
    await Promise.all([
      loadConversation(campaignEditorMode === "create"),
      refresh(),
    ]);
    message(
      campaignEditorMode === "rename"
        ? "战役会话名称已更新"
        : "新的战役会话已建立",
    );
  } catch (error) {
    message(error.message, true);
  }
});

$("apply-campaign-binding").addEventListener("click", () => {
  applyCurrentCampaignBinding()
    .catch((error) => message(error.message, true));
});

$("open-bound-campaign").addEventListener("click", () => {
  const conversationId =
    campaignCatalog?.binding?.current_bound_conversation_id;
  switchCampaignConversation(conversationId)
    .catch((error) => message(error.message, true));
});

$("campaign-binding-target").addEventListener("change", () => {
  const selectedId = $("campaign-binding-target").value || "";
  const currentId =
    campaignCatalog?.binding?.current_bound_conversation_id || "";
  $("apply-campaign-binding").disabled =
    currentJobRunning ||
    campaignCatalog?.binding?.state === "no_current_save" ||
    selectedId === currentId;
});

$("show-archived-campaigns").addEventListener("change", () => {
  loadCampaignCatalog()
    .catch((error) => message(error.message, true));
});

$("conversation-application").addEventListener("change", (event) => {
  selectedConversationApplicationId = event.currentTarget.value;
  const route = (latestStatus?.model?.application_agents || []).find(
    (item) => item.application_id === selectedConversationApplicationId,
  );
  if (route) {
    $("chat-input").placeholder =
      "向" + route.display_name + "下达要求或询问本领域状态。" +
      "Enter 发送，Shift+Enter 换行。";
  }
});

$("send-message").addEventListener("click", async () => {
  const text = $("chat-input").value.trim();
  if (!text) return;
  try {
    await post("/api/conversation/message", {
      text,
      application_id: selectedConversationApplicationId,
    });
    $("chat-input").value = "";
    message("消息已发送，所选 Agent 正在处理");
    await Promise.all([refresh(), loadConversation()]);
  } catch (error) {
    message(error.message, true);
  }
});

$("chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("send-message").click();
  }
});

$("review-button").addEventListener("click", async () => {
  try {
    await post("/api/conversation/review", {
      application_id: selectedConversationApplicationId,
    });
    message("已触发所选领域的立即巡检");
    await Promise.all([refresh(), loadConversation()]);
  } catch (error) {
    message(error.message, true);
  }
});

$("save-autonomy").addEventListener("click", async () => {
  const mode = $("autonomy-mode").value;
  const reviewIntervalMonths = Number($("save-review-interval").value);
  if (
    mode === "execute" &&
    !window.confirm("启用后，灰风可在新同步存档到期时自动执行一项经校验建设。确认继续？")
  ) return;
  try {
    await post("/api/autonomy", {
      mode,
      review_interval_months: reviewIntervalMonths,
    });
    message("自动巡检策略与存档读取周期已更新");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("save-prompt").addEventListener("click", async () => {
  try {
    await post("/api/prompt", {text: $("prompt").value});
    await loadPrompt();
    message("Prompt 已保存并建立版本");
  } catch (error) {
    message(error.message, true);
  }
});

$("model-template").addEventListener("change", (event) => {
  setTemplateFields(event.currentTarget.value, true);
});

function applyModelPoolHealth(poolId, health) {
  const pool = modelPoolById(poolId);
  if (!pool) return;
  const byId = new Map(
    (health?.endpoints || []).map((item) => [item.endpoint_id, item]),
  );
  for (const endpoint of pool.endpoints) {
    if (byId.has(endpoint.endpoint_id)) {
      endpoint.health = cloneValue(byId.get(endpoint.endpoint_id));
    }
  }
  if (pool.pool_id === selectedCatalogPoolId) showModelPoolOverview();
  renderApplicationModelRoute();
}

$("open-model-catalog").addEventListener("click", () => {
  if (!selectedCatalogPoolId && modelPools.length) {
    selectedCatalogPoolId = modelPools[0].pool_id;
  }
  if (selectedCatalogPool()) showModelPoolOverview();
  $("model-catalog-dialog").showModal();
});

$("close-model-catalog").addEventListener("click", () => {
  $("model-catalog-dialog").close();
});

$("model-catalog-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

$("open-protocol-suite").addEventListener("click", async () => {
  $("protocol-suite-dialog").showModal();
  try {
    await refreshProtocolSuite();
  } catch (error) {
    message(error.message, true);
  }
});

$("close-protocol-suite").addEventListener("click", () => {
  $("protocol-suite-dialog").close();
});

$("protocol-suite-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

$("new-protocol-plan").addEventListener("click", async () => {
  if (
    protocolSuitePayload?.plan &&
    !window.confirm("新建计划会丢弃尚未保存的当前编辑。确认继续？")
  ) return;
  try {
    selectedProtocolScenarioId = null;
    await runProtocolSuiteOperation(
      () => post("/api/protocol-compatibility/plan/new", {
        game_version: $("protocol-game-version").value.trim(),
        game_build: $("protocol-game-build").value.trim(),
      }),
      "已创建安全模板；所有真实动作默认关闭。",
    );
  } catch (_) {
    // The shared operation helper already surfaced the operator-facing error.
  }
});

$("save-protocol-plan").addEventListener("click", async () => {
  try {
    await runProtocolSuiteOperation(
      saveProtocolSuitePlan,
      "协议验收计划已保存。",
    );
  } catch (_) {
    // Error already displayed.
  }
});

$("enable-all-protocol-scenarios").addEventListener("click", () => {
  if (!protocolSuitePayload?.plan) return;
  if (!$("protocol-disposable-authorized").checked) {
    message("先确认这是所有参与者已授权的可丢弃测试房间。", true);
    return;
  }
  if (!window.confirm("将十四个命令全部纳入联机验收并确认副作用？每项仍会逐条执行。")) {
    return;
  }
  try {
    commitProtocolScenarioEditor();
    for (const scenario of protocolSuitePayload.plan.scenarios) {
      scenario.enabled = true;
      scenario.acknowledge_side_effects = true;
    }
    renderProtocolSuite(protocolSuitePayload);
    message("已在本地启用全部命令；请逐项填写当前会话目标后保存。 ");
  } catch (error) {
    message(error.message, true);
  }
});

$("run-protocol-offline-check").addEventListener("click", async () => {
  try {
    await runProtocolSuiteOperation(async () => {
      let payload = await saveProtocolSuitePlan();
      renderProtocolSuite(payload);
      payload = await post("/api/protocol-compatibility/offline-check", {});
      return payload;
    }, "离线全量构包检查已经完成；未触碰游戏网络。 ");
  } catch (_) {
    // Error already displayed.
  }
});

$("protocol-disposable-authorized").addEventListener("change", () => {
  if (protocolSuitePayload) renderProtocolSuite(protocolSuitePayload);
});

for (const id of ["protocol-scenario-enabled", "protocol-scenario-ack"]) {
  $(id).addEventListener("change", () => {
    try {
      commitProtocolScenarioEditor();
      renderProtocolSuite(protocolSuitePayload);
    } catch (error) {
      message(error.message, true);
    }
  });
}

$("start-protocol-suite").addEventListener("click", async () => {
  if (!window.confirm(
    "控制台将保存计划、执行离线全量构包，然后在进房前启动 WinDivert 代理。" +
    "联机命令不会连发，仍需逐项确认。继续？",
  )) return;
  try {
    await runProtocolSuiteOperation(async () => {
      let payload = await saveProtocolSuitePlan();
      renderProtocolSuite(payload);
      payload = await post("/api/protocol-compatibility/offline-check", {});
      renderProtocolSuite(payload);
      if (!payload.offline_current) {
        throw new Error("离线构包检查失败；未启动会话代理。 ");
      }
      return post("/api/protocol-compatibility/live/start", {
        disposable_authorized: $("protocol-disposable-authorized").checked,
      });
    }, "代理已在进房前启动。现在进入可丢弃测试房间。 ");
  } catch (_) {
    // Error already displayed.
  }
});

$("confirm-protocol-room").addEventListener("click", async () => {
  try {
    await runProtocolSuiteOperation(
      () => post("/api/protocol-compatibility/room/confirm", {}),
      "双向可靠流与合作端 actor 已确认。 ",
    );
  } catch (_) {
    // Error already displayed.
  }
});

$("execute-protocol-action").addEventListener("click", async () => {
  const scenario = protocolScenarioById(
    protocolSuitePayload?.state?.current_scenario_id,
  );
  const spec = protocolSpecByAction(scenario?.action) || {};
  if (!scenario) return;
  if (!window.confirm(
    "只执行当前一项：" + (spec.title || scenario.action) + "\n\n" +
    JSON.stringify(scenario.target, null, 2),
  )) return;
  try {
    await runProtocolSuiteOperation(
      () => post("/api/protocol-compatibility/action/execute", {}),
      "网络阶段已完成；请立即核对游戏内实际结果。 ",
    );
  } catch (_) {
    // Error already displayed.
  }
});

$("skip-protocol-action").addEventListener("click", async () => {
  if (!window.confirm("跳过当前命令？最终报告会标记为不完整。")) return;
  try {
    await runProtocolSuiteOperation(
      () => post("/api/protocol-compatibility/action/skip", {}),
      "当前命令已跳过。 ",
    );
  } catch (_) {
    // Error already displayed.
  }
});

for (const button of document.querySelectorAll("[data-protocol-verdict]")) {
  button.addEventListener("click", async () => {
    const verdict = button.dataset.protocolVerdict;
    if (
      verdict !== "passed" &&
      !window.confirm("该结论会立即停止后续命令。确认记录？")
    ) return;
    try {
      await runProtocolSuiteOperation(
        () => post("/api/protocol-compatibility/action/verdict", {verdict}),
        verdict === "passed"
          ? "当前命令已通过双重确认。 "
          : "异常已记录，后续命令已经停止。 ",
      );
    } catch (_) {
      // Error already displayed.
    }
  });
}

$("finish-protocol-suite").addEventListener("click", async () => {
  if (!window.confirm("确认合作端已经退出多人房间？确认后才会停止代理。")) return;
  try {
    await runProtocolSuiteOperation(
      () => post("/api/protocol-compatibility/finish", {room_exited: true}),
      "协议验收已结束，代理已停止，报告可供下载。 ",
    );
  } catch (_) {
    // Error already displayed.
  }
});

$("reset-protocol-suite").addEventListener("click", async () => {
  if (!window.confirm("重置验收工作流？已生成的报告文件仍会保留。")) return;
  try {
    await runProtocolSuiteOperation(
      () => post("/api/protocol-compatibility/reset", {}),
      "验收工作流已重置。 ",
    );
  } catch (_) {
    // Error already displayed.
  }
});

$("create-model-pool").addEventListener("click", () => {
  try {
    if (selectedCatalogPool() && !$("model-pool-overview").hidden) {
      commitSelectedPoolIdentity();
    }
    const poolId = generatedIdentifier(
      "pool",
      modelPools.map((item) => item.pool_id),
    );
    modelPools.push({
      pool_id: poolId,
      display_name: "新模型池",
      model_ids: [],
      endpoints: [],
    });
    selectedCatalogPoolId = poolId;
    showModelPoolOverview();
    $("catalog-pool-name").focus();
    $("catalog-pool-name").select();
    message("新的模型池草稿已经建立，请命名并添加端点。");
  } catch (error) {
    message(error.message, true);
  }
});

$("catalog-pool-name").addEventListener("input", (event) => {
  const pool = selectedCatalogPool();
  if (!pool) return;
  pool.display_name = event.currentTarget.value;
  $("selected-pool-title").textContent = pool.display_name || "未命名模型池";
  renderModelPoolSidebar();
});

$("create-model-endpoint").addEventListener("click", () => {
  openModelEndpointEditor();
});

$("back-to-model-pool").addEventListener("click", () => {
  showModelPoolOverview();
});

$("commit-model-endpoint").addEventListener("click", () => {
  try {
    commitModelEndpointEditor();
  } catch (error) {
    message(error.message, true);
  }
});

$("delete-model-endpoint").addEventListener("click", () => {
  const pool = selectedCatalogPool();
  if (!pool) return;
  if (endpointEditorOriginalId === null) {
    showModelPoolOverview();
    return;
  }
  pool.endpoints = pool.endpoints.filter(
    (item) => item.endpoint_id !== endpointEditorOriginalId,
  );
  showModelPoolOverview();
  renderApplicationModelSelect($("application-model-select").value);
  message("端点已从模型池草稿移除；保存模型池后生效。");
});

$("delete-model-pool").addEventListener("click", () => {
  const pool = selectedCatalogPool();
  if (!pool || modelPools.length <= 1) return;
  const references = applicationModelProfiles.filter(
    (profile) => profile.pool_id === pool.pool_id,
  );
  if (references.length) {
    message("这个模型池仍被 " + references.length + " 份 Application 配置引用，不能删除。", true);
    return;
  }
  if (!window.confirm("确认从草稿删除模型池“" + pool.display_name + "”？")) return;
  modelPools = modelPools.filter((item) => item.pool_id !== pool.pool_id);
  selectedCatalogPoolId = modelPools[0].pool_id;
  showModelPoolOverview();
  renderApplicationConfiguration();
  message("模型池已从草稿移除；保存后生效。");
});

$("save-model-pools").addEventListener("click", async () => {
  try {
    await saveModelPools(true);
    initialized = true;
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("probe-selected-pool").addEventListener("click", async () => {
  try {
    const poolId = selectedCatalogPoolId;
    await saveModelPools(false);
    const result = await post("/api/model/probe", {pool_id: poolId});
    applyModelPoolHealth(poolId, result.model_pool_health);
    message("模型池端点检测完成。");
  } catch (error) {
    message(error.message, true);
  }
});

$("save-and-probe-model-endpoint").addEventListener("click", async () => {
  try {
    const endpoint = readModelEndpointForm();
    const endpointId = endpoint.endpoint_id;
    commitModelEndpointEditor();
    const poolId = selectedCatalogPoolId;
    await saveModelPools(false);
    const result = await post("/api/model/probe", {
      pool_id: poolId,
      endpoint_id: endpointId,
    });
    applyModelPoolHealth(poolId, result.model_pool_health);
    message("端点已经保存并完成检测。");
  } catch (error) {
    message(error.message, true);
  }
});

$("application-select").addEventListener("change", (event) => {
  selectedApplicationId = event.currentTarget.value;
  selectedApplicationProfileId = applicationModelBindings[selectedApplicationId] ||
    profilesForApplication(selectedApplicationId)[0]?.profile_id || null;
  renderApplicationConfiguration();
});

$("application-profile-select").addEventListener("change", (event) => {
  selectedApplicationProfileId = event.currentTarget.value;
  writeApplicationProfileForm(selectedApplicationProfile());
});

$("application-pool-select").addEventListener("change", () => {
  renderApplicationModelSelect(null);
});

$("application-model-select").addEventListener("change", () => {
  renderApplicationModelRoute();
});

$("new-application-profile").addEventListener("click", () => {
  const current = selectedApplicationProfile();
  const profileId = generatedIdentifier(
    "profile",
    applicationModelProfiles.map((item) => item.profile_id),
  );
  const pool = modelPoolById($("application-pool-select").value) || modelPools[0];
  const modelId = $("application-model-select").value ||
    logicalModels(pool)[0]?.model_id || "";
  const profile = {
    profile_id: profileId,
    display_name: "新 Application 配置",
    application_id: selectedApplicationId,
    pool_id: pool?.pool_id || "",
    model_id: modelId,
    request_options: cloneValue(current?.request_options || {temperature: 0.15}),
    application_options: cloneValue(current?.application_options || {}),
    _draft: true,
  };
  applicationModelProfiles.push(profile);
  selectedApplicationProfileId = profileId;
  renderApplicationProfileSelect();
  writeApplicationProfileForm(profile);
  $("application-profile-name").focus();
  $("application-profile-name").select();
  message("新的 Application 配置草稿已经建立。");
});

$("save-application-profile").addEventListener("click", async () => {
  try {
    const profileId = selectedApplicationProfileId;
    const result = await post(
      "/api/model/application-profile",
      applicationProfilePayload(),
    );
    selectedApplicationProfileId = profileId;
    $("crawl4ai-token").value = "";
    initializeModel(result);
    initialized = true;
    message("Application 模型配置已经保存并启用。");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("delete-application-profile").addEventListener("click", async () => {
  const profile = selectedApplicationProfile();
  if (!profile) return;
  if (profile._draft) {
    applicationModelProfiles = applicationModelProfiles.filter(
      (item) => item.profile_id !== profile.profile_id,
    );
    selectedApplicationProfileId = applicationModelBindings[selectedApplicationId] ||
      profilesForApplication(selectedApplicationId)[0]?.profile_id || null;
    renderApplicationConfiguration();
    message("未保存的配置草稿已删除。");
    return;
  }
  if (!window.confirm("确认删除 Application 配置“" + profile.display_name + "”？")) return;
  try {
    const result = await post("/api/model/application-profile/delete", {
      profile_id: profile.profile_id,
    });
    selectedApplicationProfileId = null;
    initializeModel(result);
    initialized = true;
    message("Application 模型配置已经删除。");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("auth-mode").addEventListener("change", () => {
  $("api-key").disabled = $("auth-mode").value === "none";
});

$("save-execution-settings").addEventListener("click", async () => {
  try {
    await post("/api/execution-settings", {
      execution_mode: $("execution-mode").value,
      fixed_click_guard_enabled: $("fixed-click-guard-enabled").checked,
      maximum_source_save_lag_versions: Number(
        $("maximum-source-save-lag-versions").value,
      ),
      require_fresh_save_seconds: Number($("fresh-save-seconds").value),
      autonomy_require_fresh_save_seconds: Number($("autonomy-fresh-save-seconds").value),
      maximum_constructions_per_turn: Number($("maximum-constructions").value),
      inconclusive_rewrite_policy: $("inconclusive-rewrite-policy").value,
      session_proxy_local_ip: $("session-proxy-local-ip").value.trim(),
      session_proxy_host_ip: $("session-proxy-host-ip").value.trim(),
      session_proxy_source_actor: Number($("session-proxy-source-actor").value || 0),
      session_proxy_host_actor: Number($("session-proxy-host-actor").value || 1),
      session_proxy_acknowledged: $("session-proxy-acknowledged").checked,
      experimental_fleet_tools_enabled: $("experimental-fleet-tools-enabled").checked,
      experimental_fleet_attack_enabled: $("experimental-fleet-attack-enabled").checked,
      experimental_fleet_coordinate_tools_enabled:
        $("experimental-fleet-coordinate-tools-enabled").checked,
      fleet_coordinate_max_abs: Number($("fleet-coordinate-max-abs").value || 1000),
      experimental_ship_design_tools_enabled:
        $("experimental-ship-design-tools-enabled").checked,
      experimental_fleet_reinforcement_tools_enabled:
        $("experimental-fleet-reinforcement-tools-enabled").checked,
      maximum_fleet_reinforcement_increase: Number(
        $("maximum-fleet-reinforcement-increase").value || 5,
      ),
      experimental_new_fleet_tools_enabled:
        $("experimental-new-fleet-tools-enabled").checked,
      maximum_new_fleet_initial_ships: Number(
        $("maximum-new-fleet-initial-ships").value || 5,
      ),
      experimental_research_tools_enabled:
        $("experimental-research-tools-enabled").checked,
      experimental_research_reselection_enabled:
        $("experimental-research-reselection-enabled").checked,
    });
    executionSettingsInitialized = false;
    message("执行时效与确认策略已保存");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("execution-mode").addEventListener("change", updateExecutionModeVisibility);

$("start-session-proxy").addEventListener("click", async () => {
  try {
    await post("/api/session-proxy/start", {});
    message("会话代理已启动；现在可以让合作端加入房间。 ");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("stop-session-proxy").addEventListener("click", async () => {
  if (!window.confirm("请确认合作端已经退出多人房间。仍在房间时停止代理会破坏同步。")) return;
  try {
    await post("/api/session-proxy/stop", {room_exited: true});
    message("会话代理已停止。 ");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("save-decade-plan").addEventListener("click", async () => {
  try {
    await post("/api/strategy/decade", {
      target: "current",
      text: $("decade-plan").value,
    });
    $("decade-plan").dataset.serverText = $("decade-plan").value;
    message("当前十年计划已保存");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("save-next-decade-plan").addEventListener("click", async () => {
  try {
    await post("/api/strategy/decade", {
      target: "next",
      text: $("next-decade-plan").value,
    });
    $("next-decade-plan").dataset.serverText = $("next-decade-plan").value;
    message("下一十年计划草案已保存");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("activate-strategic-emergency").addEventListener("click", async () => {
  const title = $("strategic-emergency-title").value.trim();
  const directive = $("strategic-emergency-directive").value.trim();
  if (!window.confirm("启用后，灰风会暂停十年计划并优先执行这项紧急指令。确认继续？")) return;
  try {
    await post("/api/strategy/emergency/activate", {title, directive});
    message("紧急状态已启用，十年计划已暂停");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("end-strategic-emergency").addEventListener("click", async () => {
  if (!window.confirm("确认结束紧急状态并恢复当前十年计划？")) return;
  try {
    await post("/api/strategy/emergency/end");
    message("紧急状态已结束，十年计划恢复执行");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("plan-button").addEventListener("click", async () => {
  try {
    await post("/api/cycle/plan");
    message("兼容规划任务已启动");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("execute-button").addEventListener("click", async () => {
  const confirmed = window.confirm(
    "确认执行最新规划？系统只会点击一次载体，并等待房主权威确认。",
  );
  if (!confirmed) return;
  try {
    await post("/api/cycle/execute");
    message("执行任务已启动：正在等待拦截器 READY");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("stop-button").addEventListener("click", async () => {
  try {
    await post("/api/emergency-stop");
    message("已请求中止当前执行", true);
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("clear-stop-button").addEventListener("click", async () => {
  try {
    await post("/api/emergency-stop/clear");
    message("紧急停止已解除；新的执行请求可以再次启动");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("capture-button").addEventListener("click", async () => {
  try {
    const capture = await post("/api/calibration/capture");
    captureAction = selectedCalibrationAction();
    captureStage = selectedCalibrationStage();
    captureId = capture.capture_id;
    calibrationPoint = null;
    const frame = $("calibration-frame");
    frame.classList.add("ready");
    frame.classList.remove("marked");
    $("calibration-image").src =
      "/api/calibration/image/" + captureId + "?t=" + Date.now();
    $("commit-button").disabled = true;
    message(
      "窗口已捕获，请选择" + calibrationCopy[captureAction + ":" + captureStage].target,
    );
  } catch (error) {
    message(error.message, true);
  }
});

$("calibration-image").addEventListener("click", (event) => {
  const rect = event.currentTarget.getBoundingClientRect();
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  calibrationPoint = {x_ratio: x, y_ratio: y};
  const marker = $("calibration-marker");
  marker.style.left = (x * 100) + "%";
  marker.style.top = (y * 100) + "%";
  $("calibration-frame").classList.add("marked");
  $("commit-button").disabled = false;
});

$("commit-button").addEventListener("click", async () => {
  if (!captureId || !calibrationPoint) return;
  try {
    await post("/api/calibration/commit", {
      capture_id: captureId,
      action_type: captureAction,
      stage: captureStage,
      ...calibrationPoint,
    });
    message(calibrationCopy[captureAction + ":" + captureStage].label + "已校准");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("test-button").addEventListener("click", async () => {
  try {
    const actionType = selectedCalibrationAction();
    const stage = selectedCalibrationStage();
    const result = await post("/api/calibration/test", {
      action_type: actionType,
      stage,
    });
    message(calibrationCopy[actionType + ":" + stage].label + "鼠标测试完成，模板得分 " + result.guard_score);
  } catch (error) {
    message(error.message, true);
  }
});

$("calibration-action").addEventListener("change", async () => {
  resetCalibrationCapture();
  updateCalibrationCopy();
  await refresh();
});

$("calibration-stage").addEventListener("change", async () => {
  resetCalibrationCapture();
  updateCalibrationCopy();
  await refresh();
});

for (const tab of document.querySelectorAll(".catalog-tab")) {
  tab.addEventListener("click", () => {
    catalogActionType = tab.dataset.catalogAction || "build_building";
    refresh();
  });
}

updateCalibrationCopy();
Promise.all([loadPrompt(), refresh(), loadConversation(true)])
  .catch((error) => message(error.message, true));
setInterval(() => {
  const tasks = [refresh(), loadConversation()];
  if (
    $("protocol-suite-dialog").open &&
    protocolSuitePayload?.state?.live_active === true
  ) tasks.push(refreshProtocolSuite());
  Promise.all(tasks)
    .catch((error) => message(error.message, true));
}, 2000);
