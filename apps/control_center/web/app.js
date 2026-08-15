"use strict";

const $ = (id) => document.getElementById(id);
let captureId = null;
let calibrationPoint = null;
let initialized = false;

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

function message(text, isError = false) {
  $("footer-message").textContent = text;
  $("footer-message").style.color = isError ? "var(--red)" : "var(--muted)";
}

function formatAction(manifest) {
  const action = manifest?.action;
  if (!action || action.type === "noop") return "本轮不建设";
  const target = action.planet_name_key || ("planet " + (action.planet_id ?? "?"));
  const object = action.building_id || action.zone_type || action.type;
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

function renderStatus(status) {
  const network = status.network || {};
  const level = confidence[network.confidence] || confidence.offline;
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
  $("port-verdict").textContent = verification.summary_zh || "尚未确认本局端口";
  $("port-verdict").className =
    "port-verdict " + (verification.severity || "offline");
  $("host-ip").textContent =
    telemetry.host_ip || network.configured_host_ip || endpoint.remote_ip || "--";
  $("local-port").textContent = ports.local_udp_port || endpoint.local_port || "--";
  $("host-out-port").textContent =
    ports.host_destination_port || endpoint.remote_port || "--";
  $("host-in-port").textContent = ports.host_source_port || "--";
  $("packet-count").textContent =
    (telemetry.outbound_packets || 0) + " → / ← " +
    (telemetry.inbound_packets || 0);
  $("interceptor-phase").textContent = telemetry.phase || "未武装";
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
  $("save-name").textContent = save.name || "--";
  $("save-time").textContent = save.modified_at || "--";
  $("save-freshness").textContent = formatSaveFreshness(save, snapshot);
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
    : "尚未检索";
  $("plan-action").textContent = formatAction(manifest);
  $("plan-reason").textContent =
    plan.reasoning_zh ||
    "读取同步存档后，代理会按经济承受能力逐项执行有界建设批次。";

  const job = status.job || {};
  $("job-state").textContent = job.state === "running"
    ? job.kind + " 运行中"
    : (job.message || job.state || "空闲");
  $("plan-button").disabled = job.state === "running";
  $("execute-button").disabled =
    job.state === "running" || !run.manifest || manifest.action?.type === "noop";

  $("calibration-state").textContent = status.calibration ? "已校准" : "未校准";
  $("calibration-state").className =
    "status-tag " + (status.calibration ? "good" : "warn");
  $("test-button").disabled = !status.calibration;
  $("key-state").textContent =
    status.model?.api_key_configured ? "密钥已配置" : "未配置密钥";
  $("key-state").className =
    "status-tag " + (status.model?.api_key_configured ? "good" : "warn");

  if (!initialized) {
    $("instruction").value = status.instruction || "";
    $("provider").value = status.model?.provider || "responses_compatible";
    $("base-url").value = status.model?.base_url || "";
    $("model").value = status.model?.model || "";
  }
  $("last-refresh").textContent =
    "刷新 " + new Date().toLocaleTimeString();
  initialized = true;
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

async function post(path, body = {}) {
  return api(path, {method: "POST", body: JSON.stringify(body)});
}

$("save-instruction").addEventListener("click", async () => {
  try {
    await post("/api/instruction", {text: $("instruction").value});
    message("本局指令已保存");
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

$("save-model").addEventListener("click", async () => {
  try {
    await post("/api/model", {
      provider: $("provider").value,
      base_url: $("base-url").value,
      model: $("model").value,
      api_key: $("api-key").value,
    });
    $("api-key").value = "";
    message("模型连接配置已保存");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("plan-button").addEventListener("click", async () => {
  try {
    await post("/api/instruction", {text: $("instruction").value});
    await post("/api/cycle/plan");
    message("规划任务已启动");
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
    message("已请求紧急停止", true);
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("capture-button").addEventListener("click", async () => {
  try {
    const capture = await post("/api/calibration/capture");
    captureId = capture.capture_id;
    calibrationPoint = null;
    const frame = $("calibration-frame");
    frame.classList.add("ready");
    frame.classList.remove("marked");
    $("calibration-image").src =
      "/api/calibration/image/" + captureId + "?t=" + Date.now();
    $("commit-button").disabled = true;
    message("窗口已捕获，请在研究实验室按钮上单击");
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
      ...calibrationPoint,
    });
    message("固定点击位置与防误点模板已保存");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("test-button").addEventListener("click", async () => {
  try {
    const result = await post("/api/calibration/test");
    message("鼠标已移动到校准位置，模板得分 " + result.guard_score);
  } catch (error) {
    message(error.message, true);
  }
});

Promise.all([loadPrompt(), refresh()])
  .catch((error) => message(error.message, true));
setInterval(refresh, 2000);
