"use strict";

const $ = (id) => document.getElementById(id);
let captureId = null;
let calibrationPoint = null;
let captureAction = "build_building";
let captureStage = "open";
let initialized = false;
let conversationLastId = 0;
let activeConversationId = null;
let loadedConversationId = null;
let campaignCatalog = null;
let campaignEditorMode = "create";
let campaignEditorConversationId = null;
let currentJobRunning = false;
let modelTemplates = [];
let selectedTemplateId = "custom";
let catalogActionType = "build_building";
let catalogSelectionKey = null;
let renderedStrategyConversationId = null;
let executionSettingsInitialized = false;

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
  if (district) stageSelect.value = "command";
  stageSelect.querySelector('option[value="open"]').disabled = district;
  $("calibration-stage-label").classList.toggle("single-step", district);
  const actionLabels = {
    build_building: "建筑（川陀）：建设指令中继",
    build_district: "主区划（川陀）：发电区划载体",
    build_zone: "区划特化（川陀）：工程学研究特化唯一载体",
    upgrade_building: "建筑升级（川陀）：升级指令中继",
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
  const locked = templateId !== "custom";
  const config = template.config || {};
  if (applyDefaults && locked) {
    $("provider").value = config.provider || "chat_completions_compatible";
    $("base-url").value = config.base_url || "";
    $("model").value = config.model || "";
    $("thinking-enabled").checked = config.thinking?.type === "enabled";
    $("reasoning-effort").value = config.reasoning_effort || "high";
    if (config.model_context_window_tokens) {
      $("context-window-tokens").value = config.model_context_window_tokens;
    }
    if (config.context_output_reserve_tokens) {
      $("context-output-reserve").value = config.context_output_reserve_tokens;
    }
    if (config.context_compression_trigger_percent) {
      $("context-compression-trigger").value = config.context_compression_trigger_percent;
    }
    if (config.context_compression_target_percent) {
      $("context-compression-target").value = config.context_compression_target_percent;
    }
  }
  for (const id of ["provider", "base-url", "model", "thinking-enabled", "reasoning-effort"]) {
    $(id).disabled = locked;
  }
}

function initializeModel(model) {
  modelTemplates = model.templates || [];
  const select = $("model-template");
  select.replaceChildren();
  for (const template of modelTemplates) {
    const option = document.createElement("option");
    option.value = template.id;
    option.textContent = template.label;
    select.appendChild(option);
  }
  const availableIds = new Set(modelTemplates.map((item) => item.id));
  selectedTemplateId = availableIds.has(model.template_id)
    ? model.template_id
    : "custom";
  select.value = selectedTemplateId;
  $("provider").value = model.provider || "responses_compatible";
  $("base-url").value = model.base_url || "";
  $("model").value = model.model || "";
  $("thinking-enabled").checked = Boolean(model.thinking_enabled);
  $("reasoning-effort").value = model.reasoning_effort || "high";
  $("temperature").value = model.temperature ?? 0.15;
  $("timeout-seconds").value = model.timeout_seconds ?? 120;
  $("context-window-tokens").value =
    model.model_context_window_tokens ?? 128000;
  $("context-output-reserve").value =
    model.context_output_reserve_tokens ?? 8192;
  $("context-compression-enabled").checked =
    model.context_compression_enabled !== false;
  $("context-compression-trigger").value =
    model.context_compression_trigger_percent ?? 80;
  $("context-compression-target").value =
    model.context_compression_target_percent ?? 35;
  $("request-body-overrides").value = JSON.stringify(
    model.request_body_overrides || {},
    null,
    2,
  );
  $("web-research-enabled").checked = Boolean(model.web_research_enabled);
  $("searxng-url").value = model.searxng_url || "http://127.0.0.1:8080";
  $("crawl4ai-url").value = model.crawl4ai_url || "http://127.0.0.1:11235";
  $("stellaris-wiki-api-url").value = model.stellaris_wiki_api_url ||
    "https://stellaris.paradoxwikis.com/api.php";
  $("web-allowed-domains").value =
    (model.web_search_allowed_domains || []).join("\n");
  $("web-direct-fallback-enabled").checked = Boolean(
    model.web_fetch_direct_fallback_enabled,
  );
  setTemplateFields(selectedTemplateId, false);
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

function renderExecutionSettings(settings, running = false) {
  if (!executionSettingsInitialized) {
    $("fresh-save-seconds").value =
      settings.require_fresh_save_seconds ?? 900;
    $("autonomy-fresh-save-seconds").value =
      settings.autonomy_require_fresh_save_seconds ?? 900;
    $("maximum-constructions").value = String(
      settings.maximum_constructions_per_turn ?? 3,
    );
    $("inconclusive-rewrite-policy").value =
      settings.inconclusive_rewrite_policy || "block_until_save";
    executionSettingsInitialized = true;
  }
  const pending = Number(settings.pending_confirmation_count || 0);
  $("pending-confirmation-state").textContent = pending
    ? pending + " 项等待存档核验"
    : "无待核验动作";
  $("pending-confirmation-state").className =
    "status-tag " + (pending ? "warn" : "good");
  $("save-execution-settings").disabled = running;
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
  const exactSlot = action.type === "upgrade_building"
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
  $("job-state").textContent = running
    ? job.kind + " 运行中"
    : (job.message || job.state || "空闲");
  for (const id of ["plan-button", "send-message", "review-button", "save-autonomy"]) {
    $(id).disabled = running;
  }
  if (!status.model?.conversation_supported) {
    $("send-message").disabled = true;
    $("review-button").disabled = true;
  }
  $("plan-button").disabled = running || !executionAllowed;
  $("review-button").disabled = running || !executionAllowed;
  $("execute-button").disabled = running || !executionAllowed ||
    !run.manifest || manifest.action?.type === "noop";

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
  $("key-state").textContent =
    status.model?.api_key_configured ? "密钥已配置" : "未配置密钥";
  $("key-state").className =
    "status-tag " + (status.model?.api_key_configured ? "good" : "warn");
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
  $("next-review").textContent = nextReview.source_game_date
    ? nextReview.source_game_date + " 起 " + nextReview.next_review_months + " 个月后复查"
    : "尚未安排复查";
  renderCampaignCatalog(status.conversations, running);
  renderAutonomy(status);
  renderStrategy(
    status.strategy || {},
    status.conversation?.conversation_id || activeConversationId,
    running,
  );
  renderExecutionSettings(status.execution_settings || {}, running);

  if (!initialized) initializeModel(status.model || {});
  $("last-refresh").textContent = "刷新 " + new Date().toLocaleTimeString();
  initialized = true;
}

function conversationRoleLabel(item) {
  if (item.kind === "autonomy_trigger") return "自主巡检";
  if (item.role === "user") return "玩家";
  if (item.role === "assistant") return "灰风";
  if (item.role === "tool") return "工具审计";
  return "系统";
}

function appendConversationMessage(item) {
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
}

async function loadConversation(reset = false) {
  if (
    reset ||
    (activeConversationId && loadedConversationId !== activeConversationId)
  ) {
    conversationLastId = 0;
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

$("send-message").addEventListener("click", async () => {
  const text = $("chat-input").value.trim();
  if (!text) return;
  try {
    await post("/api/conversation/message", {text});
    $("chat-input").value = "";
    message("消息已发送，灰风正在处理");
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
    await post("/api/conversation/review");
    message("已触发一次立即巡检");
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

$("save-model").addEventListener("click", async () => {
  try {
    await post("/api/model", {
      template_id: $("model-template").value,
      provider: $("provider").value,
      base_url: $("base-url").value,
      model: $("model").value,
      thinking_enabled: $("thinking-enabled").checked,
      reasoning_effort: $("reasoning-effort").value,
      temperature: Number($("temperature").value),
      timeout_seconds: Number($("timeout-seconds").value),
      model_context_window_tokens: Number($("context-window-tokens").value),
      context_output_reserve_tokens: Number($("context-output-reserve").value),
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
      tool_calling_enabled: true,
      api_key: $("api-key").value,
    });
    $("api-key").value = "";
    $("crawl4ai-token").value = "";
    initialized = false;
    message("模型连接配置已保存");
    await refresh();
  } catch (error) {
    message(error.message, true);
  }
});

$("save-execution-settings").addEventListener("click", async () => {
  try {
    await post("/api/execution-settings", {
      require_fresh_save_seconds: Number($("fresh-save-seconds").value),
      autonomy_require_fresh_save_seconds: Number($("autonomy-fresh-save-seconds").value),
      maximum_constructions_per_turn: Number($("maximum-constructions").value),
      inconclusive_rewrite_policy: $("inconclusive-rewrite-policy").value,
    });
    executionSettingsInitialized = false;
    message("执行时效与确认策略已保存");
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
  Promise.all([refresh(), loadConversation()])
    .catch((error) => message(error.message, true));
}, 2000);
