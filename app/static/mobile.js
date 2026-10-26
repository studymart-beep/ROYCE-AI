/* Royce AI mobile client. Same backend as /workspace: /api/ws/* with the x-workspace-token header.
   Rendering uses textContent only (never innerHTML). The owner token lives in localStorage on this phone
   and is sent only as a request header. Nothing here fakes an AI reply: offline or failed calls say so. */
(function () {
  "use strict";
  var TOKEN_KEY = "royce.m.token";
  var API = (window.ROYCE_API_BASE || "") + "/api/ws";
  var state = { tab: "chats", convId: null, token: localStorage.getItem(TOKEN_KEY) || "", pollTimer: null, busy: false };
  var $ = function (id) { return document.getElementById(id); };

  function toast(msg, isErr) {
    var t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (isErr ? " err" : "");
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { t.hidden = true; }, 3200);
  }

  function ApiError(status, message) { this.status = status; this.message = message; }

  function offline() { return !navigator.onLine; }

  async function api(method, path, body) {
    if (offline()) throw new ApiError(0, "you're offline. AI needs a connection.");
    var headers = { "x-workspace-token": state.token };
    var opts = { method: method, headers: headers, cache: "no-store" };
    if (body !== undefined) {
      headers["content-type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    var r;
    try {
      r = await fetch(API + path, opts); // every caller passes a path that starts with /, e.g. "/chat"
    } catch (e) {
      throw new ApiError(0, "you're offline or the server is unreachable. AI needs a connection.");
    }
    var data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) {
      var detail = data && (data.detail || data.message || data.error);
      if (detail && typeof detail === "object") detail = detail.message || detail.error;
      if (r.status === 401) detail = "owner token rejected. check it under more.";
      if (r.status === 503 && data && data.error === "offline") detail = data.message;
      throw new ApiError(r.status, detail || ("request failed (" + r.status + ")"));
    }
    return data;
  }

  function setConn(kind, text) {
    $("conn").className = "conn " + kind;
    $("connText").textContent = text;
  }

  function fmtTime(ts) {
    if (!ts) return "";
    var d = new Date(typeof ts === "number" && ts < 1e12 ? ts * 1000 : ts);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  /* ---------- tabs ---------- */
  var TITLES = { chats: "Chats", tasks: "Tasks", approvals: "Approvals", more: "More" };
  function showTab(name) {
    state.tab = name;
    document.querySelectorAll(".screen").forEach(function (s) { s.hidden = true; });
    var target = name === "chat" ? "scr-chat" : "scr-" + name;
    $(target).hidden = false;
    document.querySelectorAll(".tab").forEach(function (b) {
      b.setAttribute("aria-selected", b.getAttribute("data-tab") === name ? "true" : "false");
    });
    $("screenTitle").textContent = name === "chat" ? "Chat" : TITLES[name];
    var inChat = name === "chat";
    $("composerWrap").hidden = !inChat;
    $("composerWrap").dataset.hidden = inChat ? "0" : "1";
    document.getElementById("screens").classList.toggle("no-composer", !inChat);
    stopPoll();
    if (name === "chats") loadConversations();
    if (name === "tasks") loadTasks();
    if (name === "approvals") loadApprovals();
    if (name === "more") { loadStatus(); loadMemory(); }
    if (!state.token && name !== "more") {
      toast("add your owner token under more first", true);
    }
  }

  /* ---------- chats ---------- */
  async function loadConversations() {
    var list = $("convList");
    list.textContent = "";
    if (!state.token) { $("convEmpty").hidden = false; $("convEmpty").textContent = "add your owner token under more to load chats."; return; }
    try {
      var q = $("convSearch").value.trim();
      var items = await api("GET", "/conversations" + (q ? "?q=" + encodeURIComponent(q) : ""));
      $("convEmpty").hidden = items.length > 0;
      $("convEmpty").textContent = "no chats yet. tap new to start one.";
      items.forEach(function (c) {
        var b = el("button", "item" + (c.id === state.convId ? " active" : ""));
        b.type = "button";
        b.appendChild(el("span", "t", c.title || "untitled chat"));
        b.appendChild(el("span", "s", (c.message_count || 0) + " messages · " + fmtTime(c.updated_at)));
        b.addEventListener("click", function () { openConversation(c.id); });
        list.appendChild(b);
      });
      setConn("ok", "connected");
    } catch (e) { showError(e); }
  }

  async function openConversation(cid) {
    state.convId = cid;
    showTab("chat");
    var box = $("msgs");
    box.textContent = "";
    if (!cid) return;
    try {
      var conv = await api("GET", "/conversations/" + encodeURIComponent(cid));
      (conv.messages || []).forEach(function (m) { addBubble(m.role === "user" ? "user" : "assistant", m.text, m.ts); });
      scrollChat();
    } catch (e) { showError(e); }
  }

  function addBubble(kind, text, ts) {
    var wrap = el("div", "bubble " + kind, text);
    $("msgs").appendChild(wrap);
    return wrap;
  }

  function scrollChat() { var m = document.getElementById("scr-chat"); m.scrollTop = m.scrollHeight; }

  function showError(e) {
    if (e.status === 0 || offline()) setConn("bad", "offline");
    else if (e.status === 401 || e.status === 503) setConn("bad", "needs token");
    else setConn("bad", "error");
    toast(e.message || "something went wrong", true);
  }

  async function sendMessage(ev) {
    ev.preventDefault();
    if (state.busy) return;
    var input = $("input");
    var text = input.value.trim();
    if (!text) return;
    if (!state.token) { toast("add your owner token under more first", true); return; }
    state.busy = true;
    $("send").disabled = true;
    input.value = "";
    autoSize();
    addBubble("user", text);
    var pending = addBubble("assistant pending", "royce is thinking");
    scrollChat();
    try {
      var res = await api("POST", "/chat", { message: text, conversation_id: state.convId });
      state.convId = res.conversation_id;
      pending.remove();
      addBubble("assistant", res.reply || "");
      if (res.task_id) addBubble("assistant pending", "task " + res.task_id + " is running. see tasks.");
      scrollChat();
      setConn("ok", "connected");
    } catch (e) {
      pending.remove();
      // nothing was stored on the server for a failed send, so say so plainly
      var err = el("div", "bubble error", e.status === 429
        ? "you've hit the hourly limit. try again later. your message was not sent."
        : (e.message || "the message failed") + " your message was not saved.");
      $("msgs").appendChild(err);
      showError(e);
      scrollChat();
    } finally {
      state.busy = false;
      $("send").disabled = false;
    }
  }

  function autoSize() {
    var t = $("input");
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 132) + "px";
  }

  /* ---------- tasks (polling) ---------- */
  var TERMINAL = { completed: 1, done: 1, failed: 1, rejected: 1, cancelled: 1, canceled: 1 };
  function stopPoll() { clearInterval(state.pollTimer); state.pollTimer = null; }

  async function loadTasks() {
    var list = $("taskList");
    list.textContent = "";
    if (!state.token) { $("taskEmpty").hidden = false; return; }
    try {
      var tasks = await api("GET", "/tasks");
      $("taskEmpty").hidden = tasks.length > 0;
      tasks.forEach(function (t) { list.appendChild(taskCard(t)); });
      var anyRunning = tasks.some(function (t) { return !TERMINAL[t.status]; });
      if (state.tab === "tasks" && anyRunning) { stopPoll(); state.pollTimer = setInterval(loadTasks, 3000); }
      setConn("ok", "connected");
    } catch (e) { showError(e); }
  }

  function taskCard(t) {
    var card = el("div", "item");
    card.appendChild(el("span", "t", t.goal || "task"));
    card.appendChild(el("span", "s", (t.status || "unknown") + " · " + (t.steps_done || 0) + " of " + (t.steps_total || 0) + " steps"));
    if (t.current_step) card.appendChild(el("span", "s", "working on step " + t.current_step));
    if (t.summary) card.appendChild(el("span", "s", t.summary));
    return card;
  }

  /* ---------- approvals ---------- */
  async function loadApprovals() {
    var list = $("apprList");
    list.textContent = "";
    if (!state.token) { $("apprEmpty").hidden = false; return; }
    try {
      var items = await api("GET", "/approvals");
      var pending = items.filter(function (a) { return a.status === "pending"; });
      $("apprEmpty").hidden = pending.length > 0;
      $("apprBadge").hidden = pending.length === 0;
      $("apprBadge").textContent = String(pending.length);
      pending.forEach(function (a) { list.appendChild(approvalCard(a)); });
      setConn("ok", "connected");
    } catch (e) { showError(e); }
  }

  function approvalCard(a) {
    var card = el("div", "item");
    card.appendChild(el("span", "t", a.summary || a.action_class || "action"));
    card.appendChild(el("span", "s", "waiting for your approval · " + fmtTime(a.created_at)));
    var row = el("div", "btn-row");
    var ok = el("button", "btn-approve", "approve");
    ok.type = "button";
    var no = el("button", "btn-danger", "reject");
    no.type = "button";
    ok.addEventListener("click", function () { decide(a.id, "approve", ok, no); });
    no.addEventListener("click", function () { decide(a.id, "reject", ok, no); });
    row.appendChild(ok); row.appendChild(no);
    card.appendChild(row);
    return card;
  }

  async function decide(id, verb, ok, no) {
    ok.disabled = true; no.disabled = true;
    try {
      await api("POST", "/approvals/" + encodeURIComponent(id) + "/" + verb);
      toast(verb === "approve" ? "approved" : "rejected");
      loadApprovals();
    } catch (e) {
      ok.disabled = false; no.disabled = false;
      showError(e);
      if (e.status === 409) loadApprovals();
    }
  }

  /* ---------- more: provider, memory, token ---------- */
  async function loadStatus() {
    var card = $("providerCard");
    card.textContent = "";
    try {
      var ready = await fetch((window.ROYCE_API_BASE || "") + "/readyz", { cache: "no-store" }).then(function (r) { return r.json(); }).catch(function () { return null; });
      if (ready && ready.providers) {
        Object.keys(ready.providers).forEach(function (k) {
          var p = ready.providers[k];
          var row = el("div", "kv");
          row.appendChild(el("span", "", k));
          row.appendChild(el("span", p.healthy ? "state-ok" : "state-bad", p.configured ? (p.healthy ? "ready" : "configured, not responding") : "not configured"));
          card.appendChild(row);
        });
      } else {
        card.appendChild(el("p", "muted", "provider status unavailable right now."));
      }
      if (state.token) {
        var st = await api("GET", "/status");
        var b = st.budget || {};
        var row2 = el("div", "kv");
        row2.appendChild(el("span", "", "web budget this hour"));
        row2.appendChild(el("span", "", (b.used_this_hour != null ? b.used_this_hour : "?") + " used, " + (b.remaining_this_hour != null ? b.remaining_this_hour : "?") + " left"));
        card.appendChild(row2);
      }
    } catch (e) {
      card.appendChild(el("p", "muted", e.message || "status unavailable"));
    }
  }

  async function loadMemory() {
    var list = $("memList");
    list.textContent = "";
    if (!state.token) { $("memEmpty").hidden = false; return; }
    try {
      var q = $("memSearch").value.trim();
      var items = await api("GET", "/memory" + (q ? "?q=" + encodeURIComponent(q) : ""));
      $("memEmpty").hidden = items.length > 0;
      items.forEach(function (m) {
        var card = el("div", "item");
        card.appendChild(el("span", "t", m.text || m.key));
        card.appendChild(el("span", "s", (m.scope || "") + " " + fmtTime(m.created_at)));
        var del = el("button", "btn-danger", "forget");
        del.type = "button";
        del.addEventListener("click", function () { forgetMemory(m.key); });
        card.appendChild(del);
        list.appendChild(card);
      });
    } catch (e) { showError(e); }
  }

  async function forgetMemory(key) {
    if (!window.confirm("forget this memory? this can't be undone.")) return;
    try {
      await api("DELETE", "/memory/" + encodeURIComponent(key));
      toast("forgotten");
      loadMemory();
    } catch (e) { showError(e); }
  }

  /* ---------- wiring ---------- */
  function saveToken() {
    var v = $("tokenInput").value.trim();
    if (!v) { toast("enter the owner token", true); return; }
    state.token = v;
    localStorage.setItem(TOKEN_KEY, v);
    $("tokenInput").value = "";
    toast("token saved on this phone");
    loadStatus();
  }

  function forgetToken() {
    state.token = "";
    localStorage.removeItem(TOKEN_KEY);
    toast("token forgotten on this phone");
    loadStatus();
  }

  function updateOnline() {
    $("offlineBanner").hidden = !offline();
    if (offline()) setConn("bad", "offline");
  }

  // keep the composer above the on-screen keyboard (visualViewport where available)
  function trackKeyboard() {
    var vv = window.visualViewport;
    if (!vv) return;
    var fn = function () {
      var kb = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      document.documentElement.style.setProperty("--kb", kb + "px");
    };
    vv.addEventListener("resize", fn);
    vv.addEventListener("scroll", fn);
  }

  function init() {
    document.querySelectorAll(".tab").forEach(function (b) {
      b.addEventListener("click", function () { showTab(b.getAttribute("data-tab")); });
    });
    $("composer").addEventListener("submit", sendMessage);
    $("input").addEventListener("input", autoSize);
    $("input").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.shiftKey && window.matchMedia("(pointer:fine)").matches) {
        ev.preventDefault(); $("composer").requestSubmit();
      }
    });
    $("newChat").addEventListener("click", function () { openConversation(null); });
    var searchT;
    $("convSearch").addEventListener("input", function () { clearTimeout(searchT); searchT = setTimeout(loadConversations, 300); });
    $("memSearch").addEventListener("input", function () { clearTimeout(searchT); searchT = setTimeout(loadMemory, 300); });
    $("saveToken").addEventListener("click", saveToken);
    $("forgetToken").addEventListener("click", forgetToken);
    window.addEventListener("online", function () { updateOnline(); showTab(state.tab); });
    window.addEventListener("offline", updateOnline);
    trackKeyboard();
    updateOnline();
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
    }
    showTab("chats");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
