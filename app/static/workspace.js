/* Royce AI workspace. Talks only to /api/ws/* on this server. Model and task text is rendered with
   textContent only (never innerHTML). The owner token lives in sessionStorage for this browser tab. */
(function () {
  "use strict";

  const TOKEN_KEY = "royce.ws.token";
  const POLL_MS = 2500;
  const $ = (id) => document.getElementById(id);
  const state = {
    token: sessionStorage.getItem(TOKEN_KEY) || "",
    view: "workspace",
    convId: null,
    convs: [],
    messages: [],
    lastSent: null,
    sending: false,
    taskId: null,
    pollTimer: null,
    approvalsTimer: null,
    statusTimer: null,
  };

  // ---------- network ----------
  async function api(method, path, body) {
    const headers = { "x-workspace-token": state.token };
    const opts = { method, headers };
    if (body !== undefined) {
      headers["content-type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let r;
    try {
      r = await fetch("/api/ws" + path, opts);
    } catch (e) {
      throw new ApiError(0, "cannot reach the Royce AI server. check that it is running.");
    }
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) {
      const msg = pickDetail(data) || `request failed (${r.status})`;
      throw new ApiError(r.status, msg, data);
    }
    return data;
  }

  class ApiError extends Error {
    constructor(status, message, data) { super(message); this.status = status; this.data = data; }
  }

  function pickDetail(data) {
    if (!data || data.detail === undefined) return "";
    const d = data.detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object") return d.message || d.error || "";
    return "";
  }

  // ---------- helpers ----------
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function toast(msg, bad) {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (bad ? " err" : "");
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 3800);
  }

  function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function badge(status) {
    return el("span", "badge " + String(status || "").replace(/[^a-z_]/g, ""), String(status || "unknown").replace(/_/g, " "));
  }

  function requireToken() {
    if (!state.token) {
      showView("settings");
      toast("paste your owner token in Settings first", true);
      return false;
    }
    return true;
  }

  // ---------- chat ----------
  function renderMessages() {
    const box = $("messages");
    box.replaceChildren();
    if (!state.messages.length && !state.sending) {
      const empty = el("div", "empty");
      empty.append(el("h2", "", "Royce AI"), el("div", "", "ask for an ad hook, a script, a report, or a multi-step task."));
      box.append(empty);
      return;
    }
    for (const m of state.messages) box.append(messageNode(m));
    if (state.sending) {
      const w = el("div", "msg royce");
      w.append(el("div", "who", "Royce AI"));
      const t = el("div", "thinking");
      t.append(el("span", "dot", "."), el("span", "dot", "."), el("span", "dot", "."));
      w.append(t);
      box.append(w);
    }
    box.scrollTop = box.scrollHeight;
  }

  function messageNode(m) {
    const cls = m.role === "user" ? "msg user" : (m.error ? "msg royce error" : "msg royce");
    const n = el("div", cls);
    n.append(el("div", "who", m.role === "user" ? "You" : "Royce AI"));
    const body = el("div", "body");
    body.textContent = m.text;
    n.append(body);
    return n;
  }

  function setChatError(text) {
    const bar = $("chatErr");
    if (!text) { bar.hidden = true; return; }
    $("chatErrText").textContent = text;
    bar.hidden = false;
  }

  async function sendMessage(text) {
    if (!requireToken()) return;
    if (state.sending) return;
    const content = text.trim();
    if (!content) return;
    state.lastSent = { text: content, convId: state.convId };
    state.sending = true;
    setChatError("");
    state.messages.push({ role: "user", text: content });
    $("send").disabled = true;
    renderMessages();
    try {
      const res = await api("POST", "/chat", { message: content, conversation_id: state.convId });
      state.convId = res.conversation_id;
      state.messages.push({ role: "assistant", text: res.reply || "(empty reply)" });
      state.lastSent = null;
      if (res.task_id) startTaskPolling(res.task_id);
      await refreshConversations();
    } catch (e) {
      // the user's message was not stored server-side on failure, so drop the local copy too
      state.messages.pop();
      const msg = e.status === 429 ? `budget reached for this hour. ${e.message}` : e.message;
      setChatError(msg);
      state.lastSent = { text: content, convId: state.convId };
    } finally {
      state.sending = false;
      $("send").disabled = false;
      renderMessages();
    }
  }

  function retrySend() {
    if (!state.lastSent) return;
    const { text } = state.lastSent;
    setChatError("");
    sendMessage(text);
  }

  // ---------- conversations ----------
  async function refreshConversations() {
    if (!state.token) return;
    const q = $("convSearch").value.trim();
    try {
      state.convs = await api("GET", "/conversations" + (q ? "?q=" + encodeURIComponent(q) : ""));
      renderConvList();
    } catch (e) { /* surfaced by the status pill when it matters */ }
  }

  function renderConvList() {
    const list = $("convList");
    list.replaceChildren();
    if (!state.convs.length) {
      list.append(el("div", "conv-empty", $("convSearch").value.trim() ? "no matches" : "no chats yet"));
      return;
    }
    for (const c of state.convs) {
      const b = el("button", "conv" + (c.id === state.convId ? " active" : ""), c.title || "untitled");
      b.title = c.title || "";
      b.addEventListener("click", () => openConversation(c.id));
      list.append(b);
    }
  }

  async function openConversation(id) {
    if (!requireToken()) return;
    try {
      const c = await api("GET", "/conversations/" + encodeURIComponent(id));
      state.convId = c.id;
      state.messages = c.messages.map((m) => ({ role: m.role, text: m.text }));
      state.lastSent = null;
      setChatError("");
      showView("workspace");
      renderMessages();
      renderConvList();
      const withTask = c.messages.filter((m) => m.task_id).pop();
      if (withTask) startTaskPolling(withTask.task_id);
    } catch (e) { toast(e.message, true); }
  }

  function newChat() {
    state.convId = null;
    state.messages = [];
    state.lastSent = null;
    stopTaskPolling();
    setChatError("");
    showView("workspace");
    renderMessages();
    renderConvList();
    $("input").focus();
  }

  // ---------- tasks (planner state) ----------
  function renderTaskPanel(task) {
    const panel = $("taskPanel");
    const body = $("taskBody");
    body.replaceChildren();
    if (!task) { panel.hidden = true; return; }
    panel.hidden = false;
    const head = el("div", "");
    head.append(badge(task.status), el("div", "muted", task.goal || ""));
    body.append(head);
    for (const s of task.steps) {
      const cls = s.status === "done" ? "done" : s.status === "failed" ? "failed" : (s.n === task.current_step ? "current" : "");
      const row = el("div", "step " + cls);
      const mark = s.status === "done" ? "✓" : s.status === "failed" ? "✕" : s.status === "waiting_approval" ? "!" : (s.n === task.current_step ? "›" : "·");
      row.append(el("span", "mark", mark));
      const text = el("div", "");
      text.append(el("div", "", `step ${s.n} (${s.engine || "step"})`), badge(s.status));
      if (s.error) text.append(el("div", "muted", s.error));
      if (s.status === "waiting_approval") text.append(el("div", "muted", "waiting for your approval below"));
      row.append(text);
      body.append(row);
    }
    if (task.summary) body.append(el("div", "muted", task.summary));
  }

  function stopTaskPolling() {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    state.taskId = null;
  }

  function startTaskPolling(taskId) {
    stopTaskPolling();
    state.taskId = taskId;
    const tick = async () => {
      try {
        const t = await api("GET", "/tasks/" + encodeURIComponent(taskId));
        renderTaskPanel(t);
        if (["completed", "failed", "stopped"].includes(t.status)) stopTaskPolling();
      } catch (e) {
        stopTaskPolling();
        toast("task status unavailable: " + e.message, true);
      }
    };
    tick();
    state.pollTimer = setInterval(tick, POLL_MS);
  }

  // ---------- approvals ----------
  async function refreshApprovals() {
    if (!state.token) return;
    let items;
    try { items = await api("GET", "/approvals"); } catch (e) { return; }
    const pending = items.filter((a) => a.status === "pending");
    const box = $("approvals");
    const list = $("approvalList");
    list.replaceChildren();
    box.hidden = pending.length === 0;
    for (const a of pending) {
      const row = el("div", "approval");
      row.append(el("div", "", a.action_class || "action"), el("div", "summary", a.summary || "no summary provided"));
      const acts = el("div", "acts");
      const yes = el("button", "btn primary sm", "Approve");
      const no = el("button", "btn ghost sm", "Reject");
      yes.addEventListener("click", () => decideApproval(a.id, "approve"));
      no.addEventListener("click", () => decideApproval(a.id, "reject"));
      acts.append(yes, no);
      row.append(acts);
      list.append(row);
    }
  }

  async function decideApproval(id, verb) {
    try {
      await api("POST", `/approvals/${encodeURIComponent(id)}/${verb}`);
      toast(verb === "approve" ? "approved" : "rejected");
    } catch (e) {
      toast(e.message, true);
    }
    await refreshApprovals();
    if (state.taskId) startTaskPolling(state.taskId);
  }

  // ---------- views ----------
  const TITLES = { workspace: "Workspace", tasks: "Tasks", memory: "Memory", connections: "Connections", settings: "Settings" };

  function showView(name) {
    state.view = name;
    for (const v of ["workspace", "tasks", "memory", "connections", "settings"]) {
      $("view-" + v).hidden = v !== name;
    }
    document.querySelectorAll(".nav-row").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    $("viewTitle").textContent = TITLES[name];
    if (name === "tasks") loadTasks();
    if (name === "memory") loadMemory();
    if (name === "connections") loadConnections();
    if (name === "settings") loadSettings();
    if (name === "workspace") $("taskPanel").hidden = !state.taskId;
  }

  async function loadTasks() {
    if (!requireToken()) return;
    const box = $("tasksList");
    try {
      const tasks = await api("GET", "/tasks");
      box.replaceChildren();
      if (!tasks.length) { box.append(el("div", "muted", "no tasks yet. ask for a multi-step job in chat.")); return; }
      for (const t of tasks) {
        const item = el("div", "item");
        item.append(el("div", "title", t.goal || "task"), badge(t.status), el("div", "meta", `${t.steps_done} of ${t.steps_total} steps · ${fmtTime(t.created_at)}`));
        item.addEventListener("click", () => { showView("workspace"); startTaskPolling(t.id); });
        box.append(item);
      }
    } catch (e) { box.replaceChildren(el("div", "muted", "could not load tasks: " + e.message)); }
  }

  async function loadMemory() {
    if (!requireToken()) return;
    const box = $("memList");
    const q = $("memSearch").value.trim();
    try {
      const items = await api("GET", "/memory" + (q ? "?q=" + encodeURIComponent(q) : ""));
      box.replaceChildren();
      if (!items.length) { box.append(el("div", "muted", q ? "no memories match" : "no memories saved yet")); return; }
      for (const m of items) {
        const item = el("div", "item");
        const head = el("div", "row");
        head.append(el("div", "title", m.key), el("span", "badge", m.scope || "project"));
        const forget = el("button", "btn danger sm", "Forget");
        forget.style.marginLeft = "auto";
        forget.addEventListener("click", () => forgetMemory(m.key));
        head.append(forget);
        item.append(head, el("div", "", m.text), el("div", "meta", fmtTime(m.created_at)));
        box.append(item);
      }
    } catch (e) { box.replaceChildren(el("div", "muted", "could not load memory: " + e.message)); }
  }

  async function forgetMemory(key) {
    try {
      await api("DELETE", "/memory/" + encodeURIComponent(key));
      toast("forgotten");
      loadMemory();
    } catch (e) { toast(e.message, true); }
  }

  async function loadConnections() {
    if (!requireToken()) return;
    const box = $("connList");
    try {
      const s = await api("GET", "/status");
      box.replaceChildren();
      const card = el("div", "card");
      card.append(el("div", "card-title", "Collectors"));
      for (const [key, c] of Object.entries(s.collectors || {})) {
        const row = el("div", "row", "");
        row.style.justifyContent = "space-between";
        row.style.padding = "4px 0";
        row.append(el("span", "", c.label || key), badge(c.configured ? "configured" : "not configured"));
        card.append(row);
      }
      box.append(card);
    } catch (e) { box.replaceChildren(el("div", "muted", "could not load connections: " + e.message)); }
  }

  async function loadSettings() {
    const box = $("settingsBody");
    if (!requireToken()) return;
    try {
      const s = await api("GET", "/status");
      box.replaceChildren(el("div", "card-title", "Providers"));
      for (const [pid, p] of Object.entries(s.providers || {})) {
        const row = el("div", "kv");
        row.append(el("div", "", pid), el("div", "", `${p.state_text || p.state || "unknown"} · model ${p.model || "?"}`));
        box.append(row);
      }
      const b = s.budget || {};
      box.append(el("div", "card-title", "Budget"));
      const kv = el("div", "kv");
      kv.append(el("div", "", "used this hour"), el("div", "", String(b.used_this_hour ?? "?")));
      kv.append(el("div", "", "remaining this hour"), el("div", "", String(b.remaining_this_hour ?? "?")));
      box.append(kv);
      box.append(el("div", "muted", `default provider: ${s.default_provider || "?"}`));
    } catch (e) {
      box.replaceChildren(el("div", "muted", "could not load settings: " + e.message));
    }
  }

  // ---------- status pill ----------
  async function refreshStatus() {
    const pill = $("statusPill");
    if (!state.token) { pill.textContent = "set owner token in Settings"; pill.className = "status-pill"; return; }
    try {
      const s = await api("GET", "/status");
      const provs = Object.entries(s.providers || {});
      const usable = provs.filter(([, p]) => p.state === "ok" || p.state === "validated");
      const any = usable.length > 0;
      pill.textContent = any
        ? `${usable.map(([n]) => n).join(", ")} ready · ${s.budget.remaining_this_hour} calls left this hour`
        : "no provider ready: " + provs.map(([n, p]) => `${n} ${p.state_text || p.state}`).join("; ");
      pill.className = "status-pill " + (any ? "ok" : "bad");
    } catch (e) {
      pill.textContent = e.status === 401 ? "owner token rejected" : "server unreachable";
      pill.className = "status-pill bad";
    }
  }

  // ---------- wiring ----------
  function setCollapsed(on) {
    $("app").classList.toggle("collapsed", on);
    $("toggle").setAttribute("aria-label", on ? "Expand sidebar" : "Collapse sidebar");
    localStorage.setItem("royce.ws.collapsed", on ? "1" : "0");
  }

  function init() {
    $("toggle").addEventListener("click", () => setCollapsed(!$("app").classList.contains("collapsed")));
    setCollapsed(localStorage.getItem("royce.ws.collapsed") === "1" || window.innerWidth <= 820);

    $("newChat").addEventListener("click", newChat);
    document.querySelectorAll(".nav-row").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

    $("composer").addEventListener("submit", (e) => {
      e.preventDefault();
      const text = $("input").value;
      $("input").value = "";
      autosize();
      sendMessage(text);
    });
    $("input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        $("composer").requestSubmit();
      }
    });
    $("input").addEventListener("input", autosize);
    $("retry").addEventListener("click", retrySend);

    let searchT;
    $("convSearch").addEventListener("input", () => {
      clearTimeout(searchT);
      searchT = setTimeout(refreshConversations, 250);
    });
    let memT;
    $("memSearch").addEventListener("input", () => {
      clearTimeout(memT);
      memT = setTimeout(loadMemory, 250);
    });

    $("saveToken").addEventListener("click", () => {
      const v = $("tokenInput").value.trim();
      if (!v) { toast("enter the owner token", true); return; }
      state.token = v;
      sessionStorage.setItem(TOKEN_KEY, v);
      $("tokenInput").value = "";
      toast("token saved for this tab");
      boot();
    });
    $("clearToken").addEventListener("click", () => {
      state.token = "";
      sessionStorage.removeItem(TOKEN_KEY);
      stopTaskPolling();
      toast("token forgotten");
      boot();
    });

    showView("workspace");
    renderMessages();
    boot();
  }

  function autosize() {
    const t = $("input");
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 180) + "px";
  }

  function boot() {
    renderMessages();
    refreshStatus();
    refreshConversations();
    refreshApprovals();
    clearInterval(state.approvalsTimer);
    clearInterval(state.statusTimer);
    if (state.token) {
      state.approvalsTimer = setInterval(refreshApprovals, 4000);
      state.statusTimer = setInterval(refreshStatus, 15000);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
