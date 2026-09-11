const state = { snapshot: null };

const elements = {
  serviceBadge: document.querySelector("#serviceBadge"),
  scheduleState: document.querySelector("#scheduleState"),
  nextRun: document.querySelector("#nextRun"),
  enabledCount: document.querySelector("#enabledCount"),
  masterToggle: document.querySelector("#masterToggle"),
  masterLabel: document.querySelector("#masterLabel"),
  dailyTime: document.querySelector("#dailyTime"),
  saveTime: document.querySelector("#saveTime"),
  addTickerForm: document.querySelector("#addTickerForm"),
  tickerInput: document.querySelector("#tickerInput"),
  tickerList: document.querySelector("#tickerList"),
  runHistory: document.querySelector("#runHistory"),
  refreshButton: document.querySelector("#refreshButton"),
  publisherState: document.querySelector("#publisherState"),
  lastPublished: document.querySelector("#lastPublished"),
  baselineCount: document.querySelector("#baselineCount"),
  lastPublisherScan: document.querySelector("#lastPublisherScan"),
  feishuToggle: document.querySelector("#feishuToggle"),
  feishuToggleLabel: document.querySelector("#feishuToggleLabel"),
  feishuConfigured: document.querySelector("#feishuConfigured"),
  feishuLastTest: document.querySelector("#feishuLastTest"),
  feishuLastSuccess: document.querySelector("#feishuLastSuccess"),
  feishuPending: document.querySelector("#feishuPending"),
  feishuExpired: document.querySelector("#feishuExpired"),
  feishuLastError: document.querySelector("#feishuLastError"),
  testFeishu: document.querySelector("#testFeishu"),
  retryFeishu: document.querySelector("#retryFeishu"),
  feishuSheetToggle: document.querySelector("#feishuSheetToggle"),
  feishuSheetToggleLabel: document.querySelector("#feishuSheetToggleLabel"),
  feishuSheetConfigured: document.querySelector("#feishuSheetConfigured"),
  feishuSheetLastTest: document.querySelector("#feishuSheetLastTest"),
  feishuSheetLastSuccess: document.querySelector("#feishuSheetLastSuccess"),
  feishuSheetPending: document.querySelector("#feishuSheetPending"),
  feishuSheetExpired: document.querySelector("#feishuSheetExpired"),
  feishuSheetLastError: document.querySelector("#feishuSheetLastError"),
  testFeishuSheet: document.querySelector("#testFeishuSheet"),
  retryFeishuSheet: document.querySelector("#retryFeishuSheet"),
  toast: document.querySelector("#toast"),
};

let toastTimer;

function showToast(message, error = false) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.className = `toast visible${error ? " error" : ""}`;
  toastTimer = setTimeout(() => { elements.toast.className = "toast"; }, 3000);
}

async function request(path, options = {}) {
  const headers = { "X-RogueTrader-Control": "1", ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function formatDate(value) {
  if (!value) return "尚未安排";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function tickerRow(ticker) {
  const row = document.createElement("div");
  row.className = "ticker-row";
  const symbol = document.createElement("span");
  symbol.className = "ticker-symbol";
  symbol.textContent = ticker.symbol;

  const actions = document.createElement("div");
  actions.className = "ticker-actions";
  const toggleLabel = document.createElement("label");
  toggleLabel.className = "mini-toggle";
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = ticker.enabled;
  toggle.setAttribute("aria-label", `${ticker.symbol} 启用状态`);
  toggle.addEventListener("change", async () => {
    try {
      await request(`/api/tickers/${encodeURIComponent(ticker.symbol)}`, {
        method: "PATCH", body: JSON.stringify({ enabled: toggle.checked }),
      });
      await refresh();
      showToast(`${ticker.symbol} 已${toggle.checked ? "启用" : "停用"}`);
    } catch (error) {
      toggle.checked = !toggle.checked;
      showToast(error.message, true);
    }
  });
  toggleLabel.append(toggle, document.createTextNode(ticker.enabled ? "已启用" : "已停用"));

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button danger";
  remove.textContent = "移除";
  remove.addEventListener("click", async () => {
    if (!window.confirm(`确认移除 ${ticker.symbol}？`)) return;
    try {
      await request(`/api/tickers/${encodeURIComponent(ticker.symbol)}`, { method: "DELETE" });
      await refresh();
      showToast(`${ticker.symbol} 已移除`);
    } catch (error) { showToast(error.message, true); }
  });
  actions.append(toggleLabel, remove);
  row.append(symbol, actions);
  return row;
}

function render(snapshot) {
  state.snapshot = snapshot;
  const { config, scheduler, recent_runs: recentRuns, publisher } = snapshot;
  const enabledTickers = config.tickers.filter((ticker) => ticker.enabled);
  elements.serviceBadge.textContent = scheduler.job_running
    ? `正在分析 ${scheduler.current_symbol || "任务"}` : "调度服务在线";
  elements.serviceBadge.className = `status-badge ${scheduler.job_running ? "busy" : "online"}`;
  elements.scheduleState.textContent = config.enabled ? "已开启" : "已关闭";
  elements.nextRun.textContent = formatDate(scheduler.next_run_at);
  elements.enabledCount.textContent = `${enabledTickers.length} / ${config.tickers.length}`;
  elements.masterToggle.checked = config.enabled;
  elements.masterLabel.textContent = config.enabled ? "已开启" : "已关闭";
  elements.dailyTime.value = config.daily_time;

  const publisherOnline = Boolean(publisher && publisher.service_running);
  elements.publisherState.textContent = publisherOnline ? "服务在线" : "服务未启动";
  elements.publisherState.className = `inline-state${publisherOnline ? " online" : ""}`;
  elements.lastPublished.textContent = `${publisher?.last_published || 0} 条`;
  elements.baselineCount.textContent = `${publisher?.baseline_count || 0} 条`;
  elements.lastPublisherScan.textContent = formatDate(publisher?.last_scan_at);

  const feishu = publisher?.feishu || {};
  elements.feishuToggle.checked = Boolean(feishu.enabled);
  elements.feishuToggle.disabled = feishu.enabled
    ? false : (!feishu.configured || feishu.test_required);
  elements.feishuToggleLabel.textContent = feishu.enabled ? "已开启" : "已关闭";
  elements.feishuConfigured.textContent = feishu.configured ? "已配置" : "未配置";
  elements.feishuLastTest.textContent = feishu.test_required
    ? "需要测试" : formatDate(feishu.last_test_at);
  elements.feishuLastSuccess.textContent = feishu.last_success_at
    ? formatDate(feishu.last_success_at) : "尚未送达";
  elements.feishuPending.textContent = `${feishu.pending || 0} 条`;
  elements.feishuExpired.textContent = `${feishu.expired || 0} 条`;
  elements.feishuLastError.textContent = feishu.last_error
    || feishu.configuration_error || "—";
  elements.testFeishu.disabled = !feishu.configured;
  elements.retryFeishu.disabled = !feishu.enabled || !(feishu.pending > 0);

  const feishuSheet = publisher?.feishu_sheet || {};
  elements.feishuSheetToggle.checked = Boolean(feishuSheet.enabled);
  elements.feishuSheetToggle.disabled = feishuSheet.enabled
    ? false : (!feishuSheet.configured || feishuSheet.test_required);
  elements.feishuSheetToggleLabel.textContent = feishuSheet.enabled ? "已开启" : "已关闭";
  elements.feishuSheetConfigured.textContent = feishuSheet.configured ? "已配置" : "未配置";
  elements.feishuSheetLastTest.textContent = feishuSheet.test_required
    ? "需要测试" : formatDate(feishuSheet.last_test_at);
  elements.feishuSheetLastSuccess.textContent = feishuSheet.last_success_at
    ? formatDate(feishuSheet.last_success_at) : "尚未同步";
  elements.feishuSheetPending.textContent = `${feishuSheet.pending || 0} 条`;
  elements.feishuSheetExpired.textContent = `${feishuSheet.expired || 0} 条`;
  elements.feishuSheetLastError.textContent = feishuSheet.last_error
    || feishuSheet.configuration_error || "—";
  elements.testFeishuSheet.disabled = !feishuSheet.configured;
  elements.retryFeishuSheet.disabled = !feishuSheet.enabled || !(feishuSheet.pending > 0);

  elements.tickerList.replaceChildren();
  if (!config.tickers.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "尚未添加标的。添加并启用至少一个标的后，任务才会排程。";
    elements.tickerList.append(empty);
  } else {
    config.tickers.forEach((ticker) => elements.tickerList.append(tickerRow(ticker)));
  }

  elements.runHistory.replaceChildren();
  if (!recentRuns.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "尚无项目调度器执行记录。";
    elements.runHistory.append(empty);
  } else {
    recentRuns.forEach((run) => {
      const row = document.createElement("div");
      row.className = "history-row";
      const title = document.createElement("strong");
      title.textContent = run.symbol || "调度器";
      const status = document.createElement("span");
      status.className = `run-${run.status}`;
      status.textContent = run.status === "ok" ? "成功" : run.status === "failed" ? "失败" : "调度错误";
      const detail = document.createElement("small");
      detail.textContent = `${run.trade_date || "—"} · ${formatDate(run.finished_at)}`;
      row.append(title, status, detail);
      elements.runHistory.append(row);
    });
  }
}

async function refresh() {
  try {
    render(await request("/api/status"));
  } catch (error) {
    elements.serviceBadge.textContent = "连接失败";
    elements.serviceBadge.className = "status-badge";
    showToast(error.message, true);
  }
}

elements.masterToggle.addEventListener("change", async () => {
  const enabled = elements.masterToggle.checked;
  try {
    await request("/api/schedule", { method: "POST", body: JSON.stringify({ enabled }) });
    await refresh();
    showToast(`每日任务已${enabled ? "开启" : "关闭"}`);
  } catch (error) {
    elements.masterToggle.checked = !enabled;
    showToast(error.message, true);
  }
});

elements.saveTime.addEventListener("click", async () => {
  try {
    await request("/api/schedule", {
      method: "POST", body: JSON.stringify({ daily_time: elements.dailyTime.value }),
    });
    await refresh();
    showToast(`执行时间已更新为 ${elements.dailyTime.value}`);
  } catch (error) { showToast(error.message, true); }
});

elements.addTickerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const symbol = elements.tickerInput.value.trim();
  if (!symbol) return;
  try {
    await request("/api/tickers", { method: "POST", body: JSON.stringify({ symbol }) });
    elements.tickerInput.value = "";
    await refresh();
    showToast(`${symbol.toUpperCase()} 已添加`);
  } catch (error) { showToast(error.message, true); }
});

elements.refreshButton.addEventListener("click", refresh);

elements.testFeishu.addEventListener("click", async () => {
  try {
    await request("/api/publisher/feishu/test", {
      method: "POST", body: JSON.stringify({}),
    });
    await refresh();
    showToast("飞书测试卡片已发送，请在群内确认");
  } catch (error) { showToast(error.message, true); }
});

elements.feishuToggle.addEventListener("change", async () => {
  const enabled = elements.feishuToggle.checked;
  try {
    await request("/api/publisher/feishu", {
      method: "POST", body: JSON.stringify({ enabled }),
    });
    await refresh();
    showToast(`飞书自动推送已${enabled ? "开启" : "关闭"}`);
  } catch (error) {
    elements.feishuToggle.checked = !enabled;
    showToast(error.message, true);
  }
});

elements.retryFeishu.addEventListener("click", async () => {
  try {
    const result = await request("/api/publisher/feishu/retry", {
      method: "POST", body: JSON.stringify({}),
    });
    showToast(`已唤醒 ${result.queued || 0} 条待重试消息`);
    await refresh();
  } catch (error) { showToast(error.message, true); }
});

elements.testFeishuSheet.addEventListener("click", async () => {
  try {
    await request("/api/publisher/feishu-sheet/test", {
      method: "POST", body: JSON.stringify({}),
    });
    await refresh();
    showToast("飞书表格连接与表头检查成功");
  } catch (error) { showToast(error.message, true); }
});

elements.feishuSheetToggle.addEventListener("change", async () => {
  const enabled = elements.feishuSheetToggle.checked;
  try {
    await request("/api/publisher/feishu-sheet", {
      method: "POST", body: JSON.stringify({ enabled }),
    });
    await refresh();
    showToast(`飞书表格自动同步已${enabled ? "开启" : "关闭"}`);
  } catch (error) {
    elements.feishuSheetToggle.checked = !enabled;
    showToast(error.message, true);
  }
});

elements.retryFeishuSheet.addEventListener("click", async () => {
  try {
    const result = await request("/api/publisher/feishu-sheet/retry", {
      method: "POST", body: JSON.stringify({}),
    });
    showToast(`已唤醒 ${result.queued || 0} 条表格同步任务`);
    await refresh();
  } catch (error) { showToast(error.message, true); }
});

refresh();
setInterval(refresh, 15000);
