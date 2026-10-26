/* Royce AI workspace. Talks only to /api/ws/* on this server with the owner token header.
   Every piece of model or user text is rendered with textContent only. */
(function () {
  "use strict";

  const TOKEN_KEY = "royce_ws_token";
  const ACCENT_KEY = "royce_accent";
  const $ = (id) => document.getElementById(id);
  const state = {
    token: sessionStorage.getItem(TOKEN_KEY) || "",
    page: "sessions",
    convs: [],
    convId: null,
    provider: null,
    sideQuery: "",
    selectedBot: null,
  };

  // ---------- DOM helpers (textContent only) ----------
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function btn(label, cls, onClick, aria) {
    const b = el("button", "btn " + (cls || ""), label);
    b.type = "button";
    if (aria) b.setAttribute("aria-label", aria);
    if (onClick) b.addEventListener("click", onClick);
    return b;
  }
  function chip(text, cls) { return el("span", "chip " + (cls || ""), text); }
  function toast(msg, bad) {
    const t = $("toast");
    t.textContent = String(msg);
    t.className = "toast" + (bad ? " bad" : "");
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 3600);
  }
  function notice(text) { return el("div", "notice", text); }
  function empty(text) { return el("div", "empty", text); }
  function toggle(on, label, onChange, disabled) {
    const b = el("button", "toggle");
    b.type = "button";
    b.setAttribute("role", "switch");
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.setAttribute("aria-label", label);
    if (disabled) b.disabled = true;
    b.addEventListener("click", () => {
      const next = b.getAttribute("aria-checked") !== "true";
      onChange(next, b);
    });
    return b;
  }
  function setPressed(root, selector, attr, value) {
    root.querySelectorAll(selector).forEach((n) => n.setAttribute(attr, n.dataset.value === value ? "true" : "false"));
  }

  // ---------- API ----------
  async function api(method, path, body) {
    const opts = { method, headers: { "x-workspace-token": state.token, "Content-Type": "application/json" } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch("/api/ws" + path, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (r.status === 401 || r.status === 503) {
      showAuth(r.status === 503 ? "The server has no WORKSPACE_TOKEN set. The workspace stays locked." : "Owner token rejected.");
    }
    if (!r.ok) {
      const d = data && data.detail !== undefined ? data.detail : data;
      const msg = (d && d.message) || (typeof d === "string" ? d : "") || ("request failed (" + r.status + ")");
      const err = new Error(String(msg));
      err.status = r.status;
      err.detail = d;
      throw err;
    }
    return data;
  }

  // ---------- auth ----------
  function showAuth(msg) {
    $("auth").hidden = false;
    $("tokenInput").focus();
    if (msg) toast(msg, true);
  }
  function hideAuth() { $("auth").hidden = true; }

  // ---------- pages ----------
  const PAGES = {
    bots: { title: "Bots", side: "Bots", render: renderBots, sideRender: sideBots },
    sessions: { title: "Sessions", side: "Sessions", render: renderSessions, sideRender: sideSessions },
    routines: { title: "Routines", side: "Routines", render: renderRoutines, sideRender: sideRoutines },
    artifacts: { title: "Artifacts", side: "Artifacts", render: renderArtifacts, sideRender: sideArtifacts },
    library: { title: "Library", side: "Library", render: renderLibrary, sideRender: sideLibrary },
    settings: { title: "Settings", side: "Settings", render: renderSettings, sideRender: sideSettings },
  };
  const LIB_TABS = [["connectors", "Connectors"], ["messaging", "Messaging"], ["mcp", "MCP servers"], ["skills", "Skills"], ["tools", "Tools"]];
  const SET_TABS = [["memory", "Memory"], ["usage", "Usage"], ["models", "Models"], ["appearance", "Appearance"]];
  state.libTab = "connectors";
  state.setTab = "memory";

  function go(page) {
    if (!PAGES[page]) page = "sessions";
    state.page = page;
    document.querySelectorAll(".rail-item").forEach((b) => {
      if (b.dataset.page === page) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    $("pageTitle").textContent = PAGES[page].title;
    PAGES[page].sideRender();
    PAGES[page].render();
    history.replaceState(null, "", "#" + page);
  }

  // ---------- side panels ----------
  function sideHead(title) {
    const h = el("div", "side-head", title);
    return h;
  }
  function sideSearchBox(placeholder, onInput, value) {
    const i = el("input", "input side-search");
    i.type = "search";
    i.placeholder = placeholder;
    i.setAttribute("aria-label", placeholder);
    i.value = value || "";
    i.addEventListener("input", () => onInput(i.value));
    return i;
  }
  function setSide(nodes) {
    const side = $("side");
    clear(side);
    nodes.forEach((n) => side.appendChild(n));
  }
  function sideBots() {
    setSide([sideHead("Bots"), el("div", "side-list", "")]);
    const list = document.querySelector("#side .side-list");
    list.setAttribute("aria-live", "polite");
    list.textContent = "loading bots...";
    api("GET", "/bots").then((d) => {
      clear(list);
      if (!d.bots.length) { list.appendChild(empty("no bots yet. make one in the main panel.")); return; }
      d.bots.forEach((b) => {
        const item = el("button", "side-item");
        item.type = "button";
        item.appendChild(el("span", "t", b.name));
        item.appendChild(el("span", "s", b.template ? "template: " + b.template : "custom"));
        item.addEventListener("click", () => { state.selectedBot = b.id; renderBots(); });
        list.appendChild(item);
      });
    }).catch((e) => { list.textContent = "couldn't load bots: " + e.message; });
  }
  function sideSessions() {
    const head = sideHead("Sessions");
    const newBtn = btn("New chat", "primary", newChat, "start a new chat");
    newBtn.style.margin = "0 12px 10px";
    const search = sideSearchBox("Search chats", (q) => { state.sideQuery = q; loadConvs(); }, state.sideQuery);
    const list = el("div", "side-list");
    list.id = "convList";
    setSide([head, newBtn, search, list]);
    loadConvs();
  }
  function sideRoutines() {
    setSide([sideHead("Routines"), notice("System jobs run on their own. Your reminders are saved here but do not run automatically yet.")]);
  }
  function sideArtifacts() {
    setSide([sideHead("Artifacts"), notice("Generated ad packages and content calendars, newest first.")]);
  }
  function sideLibrary() {
    const list = el("div", "side-list");
    LIB_TABS.forEach(([key, label]) => {
      const b = el("button", "side-item");
      b.type = "button";
      b.appendChild(el("span", "t", label));
      b.setAttribute("aria-current", state.libTab === key ? "true" : "false");
      b.addEventListener("click", () => { state.libTab = key; sideLibrary(); renderLibrary(); });
      list.appendChild(b);
    });
    setSide([sideHead("Library"), list]);
  }
  function sideSettings() {
    const list = el("div", "side-list");
    SET_TABS.forEach(([key, label]) => {
      const b = el("button", "side-item");
      b.type = "button";
      b.appendChild(el("span", "t", label));
      b.setAttribute("aria-current", state.setTab === key ? "true" : "false");
      b.addEventListener("click", () => { state.setTab = key; sideSettings(); renderSettings(); });
      list.appendChild(b);
    });
    setSide([sideHead("Settings"), list]);
  }

  // ---------- Bots ----------
  async function renderBots() {
    const body = clear($("pageBody"));
    body.appendChild(el("h2", "h2", "Bots"));
    body.appendChild(el("p", "muted small", "A bot is a named persona that pins one marketing playbook into the chat."));
    const list = el("div", "grid");
    body.appendChild(el("div", "sec", "Your bots"));
    body.appendChild(list);
    const makeWrap = el("div");
    body.appendChild(el("div", "sec", "Make a bot"));
    body.appendChild(makeWrap);
    try {
      const [bots, tpl] = await Promise.all([api("GET", "/bots"), api("GET", "/bots/templates")]);
      if (!bots.bots.length) list.appendChild(empty("no bots yet. make one below."));
      bots.bots.forEach((b) => list.appendChild(botCard(b)));
      renderMakeBot(makeWrap, tpl.templates || []);
    } catch (e) {
      list.appendChild(notice("couldn't load bots: " + e.message));
    }
  }
  function botCard(b) {
    const card = el("div", "card");
    const row = el("div", "bot-card");
    const img = document.createElement("img");
    img.src = "/static/mascot.png";
    img.alt = "";
    row.appendChild(img);
    const txt = el("div");
    txt.appendChild(el("div", "n", b.name));
    txt.appendChild(el("div", "d", b.template ? "pins the " + b.template + " playbook" : "custom persona"));
    row.appendChild(txt);
    card.appendChild(row);
    const del = btn("Delete", "danger small", async () => {
      if (!window.confirm("Delete bot " + b.name + "?")) return;
      try { await api("DELETE", "/bots/" + encodeURIComponent(b.id)); toast("bot deleted"); renderBots(); sideBots(); }
      catch (e) { toast(e.message, true); }
    }, "delete bot " + b.name);
    card.appendChild(el("div", "row", "")).appendChild(del);
    return card;
  }
  function renderMakeBot(wrap, templates) {
    clear(wrap);
    const card = el("div", "card");
    const form = el("form");
    form.autocomplete = "off";
    const nameLabel = el("label", "small muted", "Bot name");
    nameLabel.htmlFor = "botName";
    const name = el("input", "input wide");
    name.id = "botName";
    name.maxLength = 60;
    name.placeholder = "e.g. Lagos ad writer";
    name.setAttribute("aria-label", "Bot name");
    form.appendChild(nameLabel);
    form.appendChild(name);
    form.appendChild(el("div", "sec", "Avatar"));
    const avs = el("div", "bot-avatars");
    const avatarOpts = [
      { key: "mascot", src: "/static/mascot.png", label: "Royce mascot" },
    ];
    avatarOpts.forEach((o, i) => {
      const b = el("button", "av");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", i === 0 ? "true" : "false");
      b.setAttribute("aria-label", o.label);
      b.dataset.value = o.key;
      const img = document.createElement("img");
      img.src = o.src;
      img.alt = "";
      b.appendChild(img);
      avs.appendChild(b);
    });
    form.appendChild(avs);
    form.appendChild(el("div", "sec", "Template"));
    const tabs = el("div", "tabs");
    tabs.setAttribute("role", "tablist");
    const cards = el("div", "grid");
    let picked = templates[0] ? templates[0].key : null;
    function paintCards() {
      clear(cards);
      templates.forEach((t) => {
        const c = el("div", "card");
        c.appendChild(el("div", "card-title", t.label || t.key));
        c.appendChild(el("div", "small muted", t.description || ""));
        const sel = btn(picked === t.key ? "Selected" : "Use template", picked === t.key ? "primary" : "", () => { picked = t.key; paintCards(); }, "use template " + (t.label || t.key));
        c.appendChild(el("div", "row", "")).appendChild(sel);
        cards.appendChild(c);
      });
    }
    const cats = ["all"];
    const tabBtns = cats.map((c) => {
      const t = el("button", "tab", c === "all" ? "All" : c);
      t.type = "button";
      t.setAttribute("role", "tab");
      t.setAttribute("aria-selected", "true");
      return t;
    });
    tabBtns.forEach((t) => tabs.appendChild(t));
    form.appendChild(tabs);
    form.appendChild(cards);
    paintCards();
    const make = btn("Create bot", "primary", null, "create bot");
    make.type = "submit";
    const status = el("div", "small muted", "");
    status.setAttribute("aria-live", "polite");
    form.appendChild(el("div", "row", "")).appendChild(make);
    form.appendChild(status);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const n = name.value.trim();
      if (!n) { toast("give the bot a name", true); name.focus(); return; }
      make.disabled = true;
      try {
        const bot = await api("POST", "/bots", { name: n, template: picked });
        toast("bot created: " + bot.name);
        name.value = "";
        renderBots();
        sideBots();
      } catch (e) {
        status.textContent = e.message;
      } finally { make.disabled = false; }
    });
    card.appendChild(form);
    wrap.appendChild(card);
  }

  function fmtDate(v) {
    if (!v) return "";
    const d = new Date(typeof v === "number" ? v * 1000 : v);
    return isNaN(d) ? String(v) : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  // ---------- Sessions (chat) ----------
  async function loadConvs() {
    const list = $("convList");
    if (!list) return;
    try {
      const q = state.sideQuery.trim();
      state.convs = await api("GET", "/conversations" + (q ? "?q=" + encodeURIComponent(q) : ""));
    } catch (e) {
      clear(list).appendChild(notice("couldn't load chats: " + e.message));
      return;
    }
    clear(list);
    if (!state.convs.length) { list.appendChild(empty("no chats yet")); return; }
    state.convs.forEach((c) => {
      const item = el("button", "side-item");
      item.type = "button";
      item.setAttribute("aria-current", c.id === state.convId ? "true" : "false");
      item.appendChild(el("span", "t", c.title || "untitled chat"));
      item.appendChild(el("span", "s", fmtDate(c.updated_at || c.created_at)));
      item.addEventListener("click", () => openConv(c.id));
      list.appendChild(item);
    });
  }
  function newChat() {
    state.convId = null;
    state.page = "sessions";
    renderSessions();
    loadConvs();
  }
  async function openConv(id) {
    state.convId = id;
    renderSessions();
    loadConvs();
  }
  async function renderSessions() {
    const body = clear($("pageBody"));
    const wrap = el("div", "chat");
    const scroll = el("div", "chat-scroll");
    scroll.id = "chatScroll";
    scroll.setAttribute("aria-live", "polite");
    wrap.appendChild(scroll);
    const errSlot = el("div");
    errSlot.id = "chatErr";
    wrap.appendChild(errSlot);
    const compose = buildComposer();
    wrap.appendChild(compose);
    body.appendChild(wrap);
    if (state.convId) {
      try {
        const c = await api("GET", "/conversations/" + encodeURIComponent(state.convId));
        paintMessages(c.messages || []);
      } catch (e) {
        scroll.appendChild(notice("couldn't open that chat: " + e.message));
      }
    } else {
      paintMessages([]);
    }
    loadProviderStatus();
  }
  function paintMessages(msgs) {
    const scroll = $("chatScroll");
    if (!scroll) return;
    clear(scroll);
    if (!msgs.length) {
      scroll.appendChild(empty("start a chat. ask for an ad script, a caption, or a campaign report."));
      return;
    }
    msgs.forEach((m) => {
      const role = m.role === "user" ? "user" : "assistant";
      const b = el("div", "bubble " + role, m.content || m.text || "");
      scroll.appendChild(b);
    });
    scroll.scrollTop = scroll.scrollHeight;
  }
  function buildComposer() {
    const box = el("form", "composer");
    box.autocomplete = "off";
    const ta = el("textarea");
    ta.placeholder = "Message Royce AI. Enter sends, Shift+Enter for a new line.";
    ta.setAttribute("aria-label", "Message");
    ta.rows = 2;
    box.appendChild(ta);
    const tools = el("div", "composer-tools");
    const left = el("div", "row");
    const approval = el("span", "chip off", "no approvals waiting");
    approval.id = "approvalChip";
    left.appendChild(approval);
    const attach = el("span", "small muted", "attachments: not available in the web chat yet");
    left.appendChild(attach);
    tools.appendChild(left);
    const right = el("div", "row");
    const sel = el("select", "select");
    sel.id = "providerSel";
    sel.setAttribute("aria-label", "Model provider");
    right.appendChild(sel);
    const send = btn("Send", "primary", null, "send message");
    send.type = "submit";
    right.appendChild(send);
    tools.appendChild(right);
    box.appendChild(tools);
    ta.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); box.requestSubmit(); }
    });
    box.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const text = ta.value.trim();
      if (!text) return;
      await sendMessage(text, ta, send);
    });
    return box;
  }
  async function sendMessage(text, ta, send) {
    const scroll = $("chatScroll");
    const errSlot = clear($("chatErr"));
    scroll.appendChild(el("div", "bubble user", text));
    scroll.scrollTop = scroll.scrollHeight;
    ta.value = "";
    send.disabled = true;
    try {
      const res = await api("POST", "/chat", { message: text, conversation_id: state.convId });
      state.convId = res.conversation_id;
      scroll.appendChild(el("div", "bubble assistant", res.reply || ""));
      scroll.scrollTop = scroll.scrollHeight;
      loadConvs();
      loadProviderStatus();
    } catch (e) {
      scroll.appendChild(el("div", "bubble error", "The reply didn't go through: " + e.message));
      errSlot.appendChild(retryBar(text));
    } finally {
      send.disabled = false;
    }
  }
  function retryBar(text) {
    const bar = el("div", "err-box");
    bar.appendChild(el("span", "", "Nothing was stored, so retrying is safe."));
    const other = state.provider === "gemini" ? "openrouter" : "gemini";
    bar.appendChild(btn("Retry on " + other, "", async () => {
      try {
        await api("PUT", "/settings/default-provider", { provider: other });
        state.provider = other;
        toast("switched default model to " + other);
      } catch (e) { toast(e.message, true); }
      clear($("chatErr"));
      const scroll = $("chatScroll");
      scroll.appendChild(el("div", "bubble user", text));
      try {
        const res = await api("POST", "/chat", { message: text, conversation_id: state.convId });
        state.convId = res.conversation_id;
        scroll.appendChild(el("div", "bubble assistant", res.reply || ""));
        loadConvs();
      } catch (e2) {
        scroll.appendChild(el("div", "bubble error", "still failing: " + e2.message));
        $("chatErr").appendChild(retryBar(text));
      }
    }, "retry on other model"));
    bar.appendChild(btn("Dismiss", "", () => clear($("chatErr")), "dismiss error"));
    return bar;
  }
  async function loadProviderStatus() {
    try {
      const s = await api("GET", "/status");
      const pill = $("statusPill");
      const providers = s.providers || {};
      const def = s.default_provider || "gemini";
      state.provider = def;
      const ok = Object.keys(providers).some((k) => providers[k].state === "ok" || providers[k].configured);
      pill.className = "status-pill " + (ok ? "ok" : "bad");
      pill.textContent = ok ? "model: " + def : "no model configured";
      const sel = $("providerSel");
      if (sel) {
        clear(sel);
        Object.keys(providers).forEach((k) => {
          const o = el("option", "", k + (providers[k].configured ? "" : " (no key)"));
          o.value = k;
          if (k === def) o.selected = true;
          sel.appendChild(o);
        });
        sel.onchange = async () => {
          try {
            await api("PUT", "/settings/default-provider", { provider: sel.value });
            toast("default model set to " + sel.value);
            loadProviderStatus();
          } catch (e) { toast(e.message, true); }
        };
      }
      const usage = await api("GET", "/usage");
      $("usageLine").textContent = usage.remaining_this_hour + " requests left this hour";
      refreshApprovals();
    } catch (e) {
      $("statusPill").textContent = "status unavailable";
      $("statusPill").className = "status-pill bad";
    }
  }
  async function refreshApprovals() {
    const chip = $("approvalChip");
    if (!chip) return;
    try {
      const items = await api("GET", "/approvals");
      const pending = items.filter((a) => a.status === "pending").length;
      chip.textContent = pending ? pending + " waiting for your approval" : "no approvals waiting";
      chip.className = "chip " + (pending ? "warn" : "off");
    } catch (e) { /* keep last state */ }
  }

  // ---------- Routines ----------
  async function renderRoutines() {
    const body = clear($("pageBody"));
    body.appendChild(el("h2", "h2", "Routines"));
    body.appendChild(el("p", "muted small", "System jobs run on this server. Reminders you add here are stored but do not run automatically yet."));
    const sysWrap = el("div");
    const userWrap = el("div");
    body.appendChild(el("div", "sec", "System jobs"));
    body.appendChild(sysWrap);
    body.appendChild(el("div", "sec", "Your reminders"));
    body.appendChild(notice("runs_automatically: false. Reminders are saved only. They do not fire on their own yet."));
    body.appendChild(userWrap);
    const form = el("form", "card");
    form.style.margin = "12px 0";
    const name = el("input", "input wide");
    name.placeholder = "Reminder title";
    name.maxLength = 80;
    name.setAttribute("aria-label", "Reminder title");
    const sched = el("input", "input");
    sched.placeholder = "When (free text)";
    sched.maxLength = 120;
    sched.setAttribute("aria-label", "When");
    const note = el("input", "input wide");
    note.placeholder = "Note (optional)";
    note.maxLength = 500;
    note.setAttribute("aria-label", "Note");
    const add = btn("Add reminder", "primary", null, "add reminder");
    add.type = "submit";
    form.appendChild(el("div", "row", "")).append(name, sched, note, add);
    body.appendChild(form);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!name.value.trim()) { toast("give the reminder a title", true); return; }
      try {
        await api("POST", "/routines", { name: name.value.trim(), schedule: sched.value.trim(), note: note.value.trim() });
        toast("reminder saved");
        renderRoutines();
      } catch (e) { toast(e.message, true); }
    });
    try {
      const d = await api("GET", "/routines");
      if (!d.system.length) sysWrap.appendChild(empty("no system jobs reported"));
      d.system.forEach((j) => {
        const c = el("div", "card");
        c.appendChild(el("div", "card-title", j.name));
        c.appendChild(el("div", "small muted", "every " + Math.round(j.every_seconds / 60) + " min"));
        c.appendChild(el("div", "small muted", "last run: " + (j.last_run || "not yet recorded")));
        c.appendChild(chip(j.last_ok === true ? "last ok" : j.last_ok === false ? "last failed" : "no result", j.last_ok === true ? "on" : j.last_ok === false ? "warn" : "off"));
        sysWrap.appendChild(c);
      });
      if (!d.user.length) userWrap.appendChild(empty("no reminders yet."));
      d.user.forEach((r) => {
        const c = el("div", "card");
        c.appendChild(el("div", "card-title", r.name));
        c.appendChild(el("div", "small muted", (r.schedule ? r.schedule + " · " : "") + (r.note || "")));
        c.appendChild(el("div", "row", "")).appendChild(btn("Delete", "danger small", async () => {
          try { await api("DELETE", "/routines/" + encodeURIComponent(r.id)); toast("reminder deleted"); renderRoutines(); }
          catch (e) { toast(e.message, true); }
        }, "delete reminder " + r.name));
        userWrap.appendChild(c);
      });
    } catch (e) {
      sysWrap.appendChild(notice("couldn't load routines: " + e.message));
    }
  }

  // ---------- Artifacts ----------
  async function renderArtifacts() {
    const body = clear($("pageBody"));
    body.appendChild(el("h2", "h2", "Artifacts"));
    body.appendChild(el("p", "muted small", "Generated ad packages and content calendars."));
    const grid = el("div", "grid");
    body.appendChild(grid);
    try {
      const d = await api("GET", "/work");
      if (!d.items.length) { grid.appendChild(empty("nothing generated yet. ask for an ad package in chat.")); return; }
      d.items.forEach((w) => {
        const c = el("div", "card");
        c.appendChild(el("div", "card-title", w.product || w.title || "untitled"));
        c.appendChild(el("div", "small muted", (w.platform || "") + (w.audience ? " · " + w.audience : "")));
        c.appendChild(el("div", "small muted", w.created_at || ""));
        const hooks = (w.hooks || []).length;
        c.appendChild(el("div", "small muted", hooks + " hooks · " + ((w.script || []).length) + " script lines · " + ((w.captions || []).length) + " captions"));
        grid.appendChild(c);
      });
    } catch (e) {
      grid.appendChild(notice("couldn't load artifacts: " + e.message));
    }
  }

  // ---------- Library ----------
  function renderLibrary() {
    const body = clear($("pageBody"));
    const tabs = el("div", "tabs");
    tabs.setAttribute("role", "tablist");
    LIB_TABS.forEach(([key, label]) => {
      const t = el("button", "tab", label);
      t.type = "button";
      t.setAttribute("role", "tab");
      t.setAttribute("aria-selected", state.libTab === key ? "true" : "false");
      t.addEventListener("click", () => { state.libTab = key; sideLibrary(); renderLibrary(); });
      tabs.appendChild(t);
    });
    body.appendChild(tabs);
    const pane = el("div");
    body.appendChild(pane);
    const map = { connectors: libConnectors, messaging: libMessaging, mcp: libMcp, skills: libSkills, tools: libTools };
    (map[state.libTab] || libConnectors)(pane);
  }
  async function libConnectors(pane) {
    pane.appendChild(notice("Connections are set up from the Royce AI dashboard. This list shows what the server sees."));
    const grid = el("div", "grid");
    pane.appendChild(grid);
    try {
      const d = await api("GET", "/connectors");
      d.connectors.forEach((c) => {
        const card = el("div", "card");
        card.appendChild(el("div", "card-title", c.label || c.name));
        card.appendChild(el("div", "small muted", c.is_ads ? "ads platform" : "inbox / other"));
        card.appendChild(chip(c.configured ? "Connected" : "Available", c.configured ? "on" : "off"));
        grid.appendChild(card);
      });
      if (!d.connectors.length) grid.appendChild(empty("no connectors registered"));
    } catch (e) { grid.appendChild(notice("couldn't load connectors: " + e.message)); }
  }
  async function libMessaging(pane) {
    pane.appendChild(notice("Turn on the flagged-link precaution per chat. With it on, a flagged link queues a delete for your approval in your DM. Nothing is deleted automatically."));
    const list = el("div", "grid");
    pane.appendChild(list);
    try {
      const d = await api("GET", "/messaging");
      if (!d.chats.length) list.appendChild(empty("no telegram chats seen yet"));
      d.chats.forEach((c) => {
        const card = el("div", "card");
        card.appendChild(el("div", "card-title", c.title || c.group_id));
        card.appendChild(el("div", "small muted", c.type + " · " + c.messages + " messages stored"));
        const row = el("div", "row");
        row.appendChild(toggle(c.precautions_on, "flagged link precaution for " + (c.title || c.group_id), async (next, b) => {
          try {
            await api("PUT", "/messaging/" + encodeURIComponent(c.group_id), { precautions_on: next });
            b.setAttribute("aria-checked", next ? "true" : "false");
            toast("precaution " + (next ? "on" : "off") + " for " + (c.title || c.group_id));
          } catch (e) { toast(e.message, true); }
        }));
        row.appendChild(el("span", "small muted", "flagged-link precaution"));
        card.appendChild(row);
        list.appendChild(card);
      });
    } catch (e) { list.appendChild(notice("couldn't load chats: " + e.message)); }
  }
  function libMcp(pane) {
    pane.appendChild(notice("MCP servers need the mcp expansion phase, which is not built yet. Adding and importing servers is disabled until then."));
    const row = el("div", "row");
    row.appendChild(btn("Add server", "", null, "add server (not available yet)")).disabled = true;
    row.appendChild(btn("Import", "", null, "import server (not available yet)")).disabled = true;
    pane.appendChild(row);
    pane.appendChild(empty("no MCP servers connected"));
  }
  async function libSkills(pane) {
    const top = el("div", "row");
    top.appendChild(btn("Import .md", "", null, "import skill file (not available yet)")).disabled = true;
    top.appendChild(btn("Marketplace", "", null, "marketplace (coming soon)")).disabled = true;
    top.appendChild(el("span", "small muted", "Import and marketplace are coming soon."));
    pane.appendChild(top);
    const list = el("div", "grid");
    pane.appendChild(list);
    try {
      const d = await api("GET", "/skills");
      if (!d.skills.length) list.appendChild(empty("no skills found on the server"));
      d.skills.forEach((s) => {
        const card = el("div", "card");
        card.appendChild(el("div", "card-title", s.name));
        card.appendChild(el("div", "small muted", s.description || ""));
        card.appendChild(el("div", "row", "")).appendChild(btn("Edit", "small", () => editSkill(s.name), "edit skill " + s.name));
        list.appendChild(card);
      });
    } catch (e) { list.appendChild(notice("couldn't load skills: " + e.message)); }
  }
  async function editSkill(name) {
    let skill;
    try { skill = await api("GET", "/skills/" + encodeURIComponent(name)); }
    catch (e) { toast(e.message, true); return; }
    const bg = el("div", "modal-bg");
    const modal = el("div", "modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-label", "Edit skill " + name);
    modal.appendChild(el("div", "h2", "Edit " + name));
    const desc = el("input", "input wide");
    desc.value = skill.description || "";
    desc.setAttribute("aria-label", "Description");
    desc.maxLength = 300;
    modal.appendChild(desc);
    const ta = el("textarea", "code");
    ta.value = skill.body;
    ta.setAttribute("aria-label", "Skill body");
    modal.appendChild(ta);
    const close = () => bg.remove();
    const save = btn("Save", "primary", async () => {
      try {
        await api("PUT", "/skills/" + encodeURIComponent(name), { body: ta.value, description: desc.value });
        toast("skill saved");
        close();
        libSkills($("pageBody"));
      } catch (e) { toast(e.message, true); }
    });
    modal.appendChild(el("div", "row", "")).append(save, btn("Cancel", "", close));
    bg.appendChild(modal);
    bg.addEventListener("click", (ev) => { if (ev.target === bg) close(); });
    document.body.appendChild(bg);
    ta.focus();
  }
  async function libTools(pane) {
    pane.appendChild(notice("Switching a tool off stops the agent from running it. Changes take effect on the next call."));
    const list = el("div", "card");
    pane.appendChild(list);
    try {
      const d = await api("GET", "/tools");
      d.tools.forEach((t) => {
        const row = el("div", "row");
        row.style.justifyContent = "space-between";
        row.style.padding = "8px 0";
        row.style.borderBottom = "1px solid var(--hair)";
        const left = el("div");
        left.appendChild(el("div", "", t.name));
        left.appendChild(el("div", "small muted", t.args.length ? "args: " + t.args.join(", ") : "no arguments"));
        row.appendChild(left);
        row.appendChild(toggle(t.enabled, "enable tool " + t.name, async (next, b) => {
          try {
            await api("PUT", "/tools/" + encodeURIComponent(t.name), { enabled: next });
            b.setAttribute("aria-checked", next ? "true" : "false");
            toast(t.name + (next ? " on" : " off"));
          } catch (e) { toast(e.message, true); }
        }));
        list.appendChild(row);
      });
      if (!d.tools.length) list.appendChild(empty("no tools registered"));
    } catch (e) { list.appendChild(notice("couldn't load tools: " + e.message)); }
  }

  // ---------- Settings ----------
  function renderSettings() {
    const body = clear($("pageBody"));
    body.appendChild(el("h2", "h2", "Settings"));
    const tabs = el("div", "tabs");
    tabs.setAttribute("role", "tablist");
    SET_TABS.forEach(([key, label]) => {
      const t = el("button", "tab", label);
      t.type = "button";
      t.setAttribute("role", "tab");
      t.setAttribute("aria-selected", state.setTab === key ? "true" : "false");
      t.addEventListener("click", () => { state.setTab = key; sideSettings(); renderSettings(); });
      tabs.appendChild(t);
    });
    body.appendChild(tabs);
    const pane = el("div");
    body.appendChild(pane);
    const map = { memory: setMemory, usage: setUsage, models: setModels, appearance: setAppearance };
    (map[state.setTab] || setMemory)(pane);
  }
  async function setMemory(pane) {
    const q = el("input", "input wide");
    q.type = "search";
    q.placeholder = "Search memory";
    q.setAttribute("aria-label", "Search memory");
    pane.appendChild(q);
    const list = el("div");
    pane.appendChild(list);
    async function load() {
      clear(list);
      try {
        const items = await api("GET", "/memory" + (q.value.trim() ? "?q=" + encodeURIComponent(q.value.trim()) : ""));
        if (!items.length) { list.appendChild(empty("no memory saved yet")); return; }
        items.forEach((m) => {
          const c = el("div", "card");
          c.style.marginTop = "10px";
          c.appendChild(el("div", "card-title", m.key));
          c.appendChild(el("div", "small", m.text || ""));
          c.appendChild(el("div", "small muted", "scope: " + (m.scope || "project")));
          c.appendChild(el("div", "row", "")).appendChild(btn("Forget", "danger small", async () => {
            try { await api("DELETE", "/memory/" + encodeURIComponent(m.key)); toast("forgotten"); load(); }
            catch (e) { toast(e.message, true); }
          }, "forget " + m.key));
          list.appendChild(c);
        });
      } catch (e) { list.appendChild(notice("couldn't load memory: " + e.message)); }
    }
    q.addEventListener("input", load);
    load();
  }
  async function setUsage(pane) {
    try {
      const u = await api("GET", "/usage");
      const dl = el("dl", "kv");
      [["Web requests used this hour", u.used_this_hour], ["Web requests left this hour", u.remaining_this_hour]].forEach(([k, v]) => {
        dl.appendChild(el("dt", "", k));
        dl.appendChild(el("dd", "", String(v)));
      });
      const card = el("div", "card");
      card.appendChild(dl);
      pane.appendChild(card);
      pane.appendChild(notice("This is the real hourly web budget from the server. Credit balances are not shown because the server does not report them."));
    } catch (e) { pane.appendChild(notice("couldn't load usage: " + e.message)); }
  }
  async function setModels(pane) {
    try {
      const s = await api("GET", "/status");
      const providers = s.providers || {};
      const card = el("div", "card");
      Object.keys(providers).forEach((k) => {
        const p = providers[k];
        const row = el("div", "row");
        row.style.justifyContent = "space-between";
        row.style.padding = "8px 0";
        row.style.borderBottom = "1px solid var(--hair)";
        row.appendChild(el("div", "", k + " · " + (p.model || "")));
        row.appendChild(chip(p.configured ? (p.state_text || p.state || "configured") : "no key", p.configured ? "on" : "warn"));
        card.appendChild(row);
      });
      pane.appendChild(card);
      const r = el("div", "row");
      r.style.marginTop = "12px";
      const sel = el("select", "select");
      sel.setAttribute("aria-label", "Default provider");
      Object.keys(providers).forEach((k) => {
        const o = el("option", "", k);
        o.value = k;
        if (k === s.default_provider) o.selected = true;
        sel.appendChild(o);
      });
      r.appendChild(el("span", "small muted", "Default provider"));
      r.appendChild(sel);
      r.appendChild(btn("Save default", "primary", async () => {
        try { await api("PUT", "/settings/default-provider", { provider: sel.value }); toast("default set to " + sel.value); loadProviderStatus(); }
        catch (e) { toast(e.message, true); }
      }, "save default provider"));
      pane.appendChild(r);
    } catch (e) { pane.appendChild(notice("couldn't load models: " + e.message)); }
  }
  function setAppearance(pane) {
    const cur = localStorage.getItem(ACCENT_KEY) || "purple";
    const card = el("div", "card");
    card.appendChild(el("div", "card-title", "Accent colour"));
    card.appendChild(el("div", "small muted", "Saved in this browser only."));
    const row = el("div", "row");
    row.setAttribute("role", "radiogroup");
    row.setAttribute("aria-label", "Accent colour");
    [["purple", "#a855f7", "Purple (default)"], ["orange", "#f97316", "Royce orange"]].forEach(([k, c, label]) => {
      const s = el("button", "swatch");
      s.type = "button";
      s.dataset.value = k;
      s.style.background = c;
      s.setAttribute("role", "radio");
      s.setAttribute("aria-label", label);
      s.setAttribute("aria-checked", cur === k ? "true" : "false");
      s.addEventListener("click", () => {
        localStorage.setItem(ACCENT_KEY, k);
        applyAccent();
        setPressed(row, ".swatch", "aria-checked", k);
        toast("accent: " + label);
      });
      row.appendChild(s);
    });
    card.appendChild(row);
    pane.appendChild(card);
  }
  function applyAccent() {
    const v = localStorage.getItem(ACCENT_KEY) || "purple";
    if (v === "orange") document.body.dataset.accent = "orange";
    else delete document.body.dataset.accent;
  }

  // ---------- auth form ----------
  function bindAuth() {
    $("authForm").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const v = $("tokenInput").value.trim();
      if (!v) return;
      state.token = v;
      sessionStorage.setItem(TOKEN_KEY, v);
      hideAuth();
      go(state.page);
    });
  }

  // ---------- boot ----------
  function init() {
    applyAccent();
    bindAuth();
    document.querySelectorAll(".rail-item").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));
    window.addEventListener("hashchange", () => go((location.hash || "#sessions").slice(1)));
    const start = (location.hash || "#sessions").slice(1);
    if (!state.token) { showAuth(""); }
    go(PAGES[start] ? start : "sessions");
    loadProviderStatus();
  }
  init();
})();
