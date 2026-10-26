/* Royce AI workspace. Talks only to /api/ws/* on this server. Open access, no token.
   Every piece of model or user text is rendered with textContent only. */
(function () {
  "use strict";

  const ACCENT_KEY = "royce_accent";
  const $ = (id) => document.getElementById(id);
  const state = {
    page: "sessions",
    convs: [],
    convId: null,
    provider: null,
    sideQuery: "",
    selectedBot: null,
    botScope: null,
    workConvId: null,
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
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const r = await fetch((window.ROYCE_API_BASE || "") + "/api/ws" + path, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
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
        item.appendChild(el("span", "s", b.template === "coordinator" ? "coordinates all bots" : (b.template ? "template: " + b.template : "custom")));
        item.addEventListener("click", () => { selectBot(b.id); });
        list.appendChild(item);
      });
    }).catch((e) => { list.textContent = "couldn't load bots: " + e.message; });
  }
  function sideSessions() {
    const head = sideHead("Sessions");
    const newBtn = btn("＋ New chat", "primary side-new", newChat, "start a new chat");
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
    const hero = el("div", "hero");
    hero.style.paddingTop = "10px";
    hero.appendChild(el("p", "page-kicker", "Royce AI \u00b7 Make a bot"));
    hero.appendChild(el("h2", "", "Make a bot"));
    hero.appendChild(el("p", "", "Name it, pick a face, pick a playbook. Every bot pins one marketing playbook into the chat."));
    body.appendChild(hero);
    const makeWrap = el("div");
    body.appendChild(makeWrap);
    const list = el("div", "grid");
    body.appendChild(el("div", "sec", "Your bots"));
    body.appendChild(list);
    try {
      const [bots, tpl] = await Promise.all([api("GET", "/bots"), api("GET", "/bots/templates")]);
      if (!bots.bots.length) list.appendChild(empty("no bots yet. make one below."));
      bots.bots.forEach((b) => list.appendChild(botCard(b)));
      const sel = bots.bots.find((b) => b.id === state.selectedBot);
      if (sel) body.insertBefore(botChatPanel(sel), makeWrap.previousSibling);
      renderMakeBot(makeWrap, tpl.templates || []);
    } catch (e) {
      list.appendChild(notice("couldn't load bots: " + e.message));
    }
  }
  function botCard(b) {
    const card = el("div", "card hoverable");
    const row = el("div", "bot-card");
    const av = el("span", "bot-av");
    const img = document.createElement("img");
    img.src = "/static/mascot.png";
    img.alt = "";
    av.appendChild(img);
    if (b.template === "coordinator") av.appendChild(el("span", "crown", "\u2605"));
    row.appendChild(av);
    const txt = el("div");
    txt.appendChild(el("div", "n", b.name));
    txt.appendChild(el("div", "d", b.template === "coordinator" ? "coordinates all bots" : (b.template ? "pins the " + b.template + " playbook" : "custom persona")));
    row.appendChild(txt);
    card.appendChild(row);
    const open = btn(b.template === "coordinator" ? "Open work workspace" : "Open chat", "small", () => selectBot(b.id), (b.template === "coordinator" ? "open work workspace " : "open chat with ") + b.name);
    card.appendChild(el("div", "row", "")).appendChild(open);
    if (b.template === "coordinator") {
      card.appendChild(playbookDisclosure(b));
      return card;
    }
    const del = btn("Delete", "danger small", async () => {
      if (!window.confirm("Delete bot " + b.name + "?")) return;
      try { await api("DELETE", "/bots/" + encodeURIComponent(b.id)); toast("bot deleted"); renderBots(); sideBots(); }
      catch (e) { toast(e.message, true); }
    }, "delete bot " + b.name);
    card.appendChild(el("div", "row", "")).appendChild(del);
    return card;
  }
  function playbookDisclosure(b) {
    const wrap = el("details", "playbook");
    const sum = el("summary", "", "playbook");
    wrap.appendChild(sum);
    const body = el("div", "playbook-body muted small", "loading playbook...");
    wrap.appendChild(body);
    let loaded = false;
    wrap.addEventListener("toggle", async () => {
      if (!wrap.open || loaded) return;
      loaded = true;
      try {
        const p = await api("GET", "/bots/" + encodeURIComponent(b.id) + "/persona");
        clear(body);
        body.appendChild(el("pre", "playbook-text", p.persona || "no playbook pinned for this bot."));
      } catch (e) {
        clear(body);
        body.appendChild(el("div", "small", "couldn't load the playbook: " + e.message));
        loaded = false;
      }
    });
    return wrap;
  }
  function renderMakeBot(wrap, templates) {
    clear(wrap);
    const hero = el("div", "hero");
    hero.style.paddingTop = "6px";
    const faces = ["/static/mascot.png"];
    const faceRow = el("div", "bot-avatars");
    faceRow.style.justifyContent = "center";
    faceRow.style.marginBottom = "14px";
    faces.forEach((src, i) => {
      const b = el("button", "av");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", i === 0 ? "true" : "false");
      b.setAttribute("aria-label", "Royce mascot");
      b.style.width = "76px"; b.style.height = "76px"; b.style.borderRadius = "50%";
      b.style.borderColor = "var(--accent)";
      const img = document.createElement("img");
      img.src = src; img.alt = "";
      img.style.borderRadius = "50%";
      b.appendChild(img);
      faceRow.appendChild(b);
    });
    hero.appendChild(faceRow);
    const form = el("form");
    form.autocomplete = "off";
    form.style.maxWidth = "560px"; form.style.margin = "0 auto"; form.style.width = "100%";
    const pill = el("div", "name-pill");
    const name = el("input", "");
    name.id = "botName";
    name.maxLength = 60;
    name.placeholder = "Name your bot";
    name.setAttribute("aria-label", "Bot name");
    const goBtn = el("button", "send-fab", "\u2191");
    goBtn.type = "submit";
    goBtn.setAttribute("aria-label", "create bot");
    pill.appendChild(name);
    pill.appendChild(goBtn);
    form.appendChild(pill);
    const status = el("div", "small muted", "");
    status.style.textAlign = "center"; status.style.marginTop = "8px";
    status.setAttribute("aria-live", "polite");
    form.appendChild(status);
    hero.appendChild(form);
    wrap.appendChild(hero);
    // templates
    const secT = el("div", "sec", "Templates");
    wrap.appendChild(secT);
    const tabs = el("div", "tabs");
    tabs.setAttribute("role", "tablist");
    let picked = templates[0] ? templates[0].key : null;
    let filter = "Featured";
    const tabNames = ["Featured"];
    templates.slice(0, 4).forEach((t) => tabNames.push(t.label || t.key));
    if (templates.length > 4) tabNames.push("More");
    const cards = el("div", "template-grid");
    function visibleTemplates() {
      if (filter === "Featured" || filter === "More") return templates;
      return templates.filter((t) => (t.label || t.key) === filter);
    }
    function paintCards() {
      clear(cards);
      const vis = visibleTemplates();
      if (!vis.length) { cards.appendChild(empty("no templates in this group yet.")); return; }
      vis.forEach((t) => {
        const c = el("div", "template-card");
        const isSel = picked === t.key;
        if (isSel) { c.style.borderColor = "var(--accent-line)"; }
        c.appendChild(el("div", "t-ico", "\u2726"));
        c.appendChild(el("h3", "", t.label || t.key));
        c.appendChild(el("p", "", t.description || ""));
        const sel = btn(isSel ? "Selected" : "Use template", isSel ? "primary small" : "small", () => { picked = t.key; paintCards(); }, "use template " + (t.label || t.key));
        const row = el("div", "row", ""); row.appendChild(sel);
        c.appendChild(row);
        cards.appendChild(c);
      });
    }
    tabNames.forEach((tn) => {
      const t = el("button", "tab", tn);
      t.type = "button";
      t.setAttribute("role", "tab");
      t.setAttribute("aria-selected", tn === filter ? "true" : "false");
      t.addEventListener("click", () => {
        filter = tn;
        tabs.querySelectorAll(".tab").forEach((x) => x.setAttribute("aria-selected", x.textContent === tn ? "true" : "false"));
        paintCards();
      });
      tabs.appendChild(t);
    });
    wrap.appendChild(tabs);
    wrap.appendChild(cards);
    paintCards();
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const n = name.value.trim();
      if (!n) { toast("give the bot a name", true); name.focus(); return; }
      goBtn.disabled = true;
      try {
        const bot = await api("POST", "/bots", { name: n, template: picked });
        toast("bot created: " + bot.name);
        name.value = "";
        renderBots();
        sideBots();
      } catch (e) {
        status.textContent = e.message;
      } finally { goBtn.disabled = false; }
    });
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
      const qs = [];
      if (q) qs.push("q=" + encodeURIComponent(q));
      if (state.botScope) qs.push("bot_id=" + encodeURIComponent(state.botScope));
      state.convs = await api("GET", "/conversations" + (qs.length ? "?" + qs.join("&") : ""));
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
    botSelector().then((sel) => compose.insertBefore(sel, compose.firstChild));
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
      scroll.appendChild(empty("Start a session \u2014 ask for an ad script, a caption, or a campaign report. Your bots pin the playbook; Royce runs it."));
      return;
    }
    msgs.forEach((m) => {
      const role = m.role === "user" ? "user" : "assistant";
      if (role === "assistant" && m.meta && m.meta.delegations) {
        const dl = delegationLine(m.meta.delegations);
        if (dl) scroll.appendChild(dl);
      }
      if (role === "user") scroll.appendChild(userBubble(m.content || m.text || ""));
      else scroll.appendChild(assistantBubble(m.content || m.text || "", m.meta && m.meta.bot_name ? { name: m.meta.bot_name } : null));
    });
    scroll.scrollTop = scroll.scrollHeight;
  }
  function buildComposer() {
    const box = el("form", "composer");
    box.autocomplete = "off";
    const ta = el("textarea");
    ta.placeholder = "Reply, or steer the run\u2026  (Enter sends, Shift+Enter for a new line)";
    ta.setAttribute("aria-label", "Message");
    ta.rows = 2;
    box.appendChild(ta);
    const tools = el("div", "composer-tools");
    const left = el("div", "row");
    const approval = el("span", "chip off", "no approvals waiting");
    approval.id = "approvalChip";
    left.appendChild(approval);
    const attach = el("span", "small muted", "plain-text chat \u00b7 attachments live in the workspace folder");
    left.appendChild(attach);
    tools.appendChild(left);
    const right = el("div", "row");
    const pill = el("span", "model-pill", "Model\u00a0");
    const sel = document.createElement("select");
    sel.id = "providerSel";
    sel.className = "select";
    sel.style.border = "0";
    sel.style.background = "transparent";
    sel.style.padding = "0";
    sel.setAttribute("aria-label", "Model provider");
    pill.appendChild(sel);
    right.appendChild(pill);
    const send = el("button", "send-fab", "\u2191");
    send.type = "submit";
    send.setAttribute("aria-label", "send message");
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
  async function ensureChief() {
    try { const c = await api("GET", "/bots/chief"); return c.id; } catch (e) { return null; }
  }
  async function botSelector() {
    const wrap = el("label", "bot-pick");
    wrap.appendChild(el("span", "muted small", "bot "));
    const sel = document.createElement("select");
    sel.id = "botPick";
    sel.setAttribute("aria-label", "active bot");
    const none = document.createElement("option");
    none.value = ""; none.textContent = "plain chat";
    sel.appendChild(none);
    try {
      const d = await api("GET", "/bots");
      d.bots.forEach((b) => {
        const o = document.createElement("option");
        o.value = b.id; o.textContent = b.name + (b.template === "coordinator" ? " (coordinates all bots)" : "");
        sel.appendChild(o);
      });
    } catch (e) { /* selector stays on plain chat */ }
    const chiefId = await ensureChief();
    if (chiefId && !state.selectedBot) state.selectedBot = chiefId;
    if (state.selectedBot) sel.value = state.selectedBot;
    sel.addEventListener("change", () => { state.selectedBot = sel.value || null; });
    wrap.appendChild(sel);
    return wrap;
  }
  function thinkingRow(label) {
    const row = el("div", "thinking");
    row.setAttribute("role", "status");
    row.appendChild(el("span", "dots", ""));
    row.appendChild(el("span", "small muted", label));
    return row;
  }
  function avatarNode() {
    const av = el("span", "bot-av msg-av");
    const img = document.createElement("img");
    img.src = "/static/mascot.png";
    img.alt = "";
    av.appendChild(img);
    return av;
  }
  function assistantBubble(text, bot) {
    const row = el("div", "msg-row assistant-row");
    row.appendChild(avatarNode());
    const col = el("div", "msg-col");
    if (bot && bot.name) col.appendChild(el("div", "msg-name muted small", bot.name));
    col.appendChild(el("div", "bubble assistant", text));
    row.appendChild(col);
    return row;
  }
  function userBubble(text) {
    const row = el("div", "msg-row user-row");
    row.appendChild(el("div", "bubble user", text));
    return row;
  }
  function selectBot(id) {
    state.selectedBot = id;
    state.botScope = id;
    state.convId = null;
    go("bots");
  }
  function botChatPanel(bot) {
    const isWork = bot.template === "coordinator";
    const wrap = el("section", isWork ? "bot-panel work-panel" : "bot-panel");
    const head = el("header", "bot-head");
    head.appendChild(avatarNode());
    const t = el("div");
    t.appendChild(el("div", "n", isWork ? bot.name + " \u2014 work workspace" : bot.name));
    t.appendChild(el("div", "d muted small", isWork ? "complex work: delegates to your bots and waits for your approval" : (bot.template ? "pins the " + bot.template + " playbook" : "custom persona")));
    head.appendChild(t);
    wrap.appendChild(head);
    mountChat(wrap, { botId: bot.id, botName: bot.name, work: isWork });
    return wrap;
  }
  // One chat surface for a bot (normal) or the Chief work workspace. Each bot keeps its own conversation.
  function mountChat(host, cfg) {
    const scroll = el("div", "chat-scroll");
    scroll.setAttribute("aria-live", "polite");
    const errSlot = el("div");
    const compose = el("form", "composer");
    compose.autocomplete = "off";
    const ta = el("textarea");
    ta.placeholder = cfg.work ? "Give " + cfg.botName + " a complex task\u2026  (Enter sends, Shift+Enter for a new line)" : "Message " + cfg.botName + "\u2026  (Enter sends, Shift+Enter for a new line)";
    ta.setAttribute("aria-label", "Message " + cfg.botName);
    ta.rows = 2;
    compose.appendChild(ta);
    const ctools = el("div", "composer-tools");
    const approvalChipEl = el("span", "chip off", "no approvals waiting");
    ctools.appendChild(approvalChipEl);
    const send = el("button", "send-fab", "\u2191");
    send.type = "submit";
    send.setAttribute("aria-label", "send message");
    ctools.appendChild(send);
    compose.appendChild(ctools);
    ta.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); compose.requestSubmit(); }
    });
    const wrap = el("div", "chat bot-chat");
    wrap.appendChild(scroll);
    wrap.appendChild(errSlot);
    wrap.appendChild(compose);
    host.appendChild(wrap);
    const key = cfg.work ? "__work" : cfg.botId;
    // approvals strip: pending count from the real approvals endpoint
    approvalChipEl.id = "approvalChip";
    state.convByBot = state.convByBot || {};
    let convId = state.convByBot[key] || null;
    const persist = (id) => { state.convByBot[key] = id; };
    async function load() {
      if (!convId) { scroll.appendChild(empty("no messages yet. say hi to " + cfg.botName + ".")); return; }
      try {
        const c = await api("GET", "/conversations/" + encodeURIComponent(convId));
        clear(scroll);
        renderHistory(scroll, c.messages || []);
      } catch (e) { scroll.appendChild(notice("couldn't open that chat: " + e.message)); }
    }
    async function run(text) {
      clear(errSlot);
      scroll.appendChild(userBubble(text));
      ta.value = "";
      send.disabled = true;
      const thinking = thinkingRow(cfg.work ? "working on it..." : "thinking...");
      scroll.appendChild(thinking);
      scroll.scrollTop = scroll.scrollHeight;
      try {
        const res = await api("POST", "/chat", { message: text, conversation_id: convId, bot_id: cfg.botId });
        thinking.remove();
        convId = res.conversation_id; persist(convId);
        if (cfg.work) renderWorkResult(scroll, res);
        else scroll.appendChild(assistantBubble(res.reply || "", res.bot));
        scroll.scrollTop = scroll.scrollHeight;
        loadConvs(); refreshApprovals();
      } catch (e) {
        thinking.remove();
        scroll.appendChild(el("div", "bubble error", "The reply didn't go through: " + e.message));
        const bar = el("div", "err-box");
        bar.appendChild(el("span", "", "Nothing was stored, so retrying is safe."));
        bar.appendChild(btn("Retry", "", () => { bar.remove(); run(text); }, "retry message"));
        errSlot.appendChild(bar);
      } finally {
        send.disabled = false;
      }
    }
    compose.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const text = ta.value.trim();
      if (text) run(text);
    });
    load();
  }
  function renderHistory(scroll, msgs) {
    if (!msgs.length) { scroll.appendChild(empty("no messages yet.")); return; }
    msgs.forEach((m) => {
      if (m.role === "user") scroll.appendChild(userBubble(m.text || m.content || ""));
      else scroll.appendChild(assistantBubble(m.text || m.content || "", m.meta && m.meta.bot_name ? { name: m.meta.bot_name } : null));
    });
  }
  // Renders delegation metadata the normal bot chat never shows: one line per step, step N of M, status.
  function renderWorkResult(scroll, res) {
    const ds = res.delegations || [];
    const total = ds.length;
    ds.forEach((d, i) => {
      const label = "step " + (i + 1) + " of " + total + ": delegated to " + d.bot_name + " \u2014 " + stepStatus(d.status);
      scroll.appendChild(el("div", "delegated muted small step-line s-" + d.status, label));
    });
    scroll.appendChild(assistantBubble(res.reply || "", res.bot));
    if (res.status === "awaiting_approval") {
      const bar = el("div", "err-box approval-box");
      bar.appendChild(el("span", "", "This step needs your approval before it runs."));
      scroll.appendChild(bar);
      loadApprovalsInto(bar);
    } else if (res.status === "partial") {
      scroll.appendChild(el("div", "small muted", "finished with some steps not completed."));
    } else if (res.status === "completed") {
      scroll.appendChild(el("div", "small muted", "done."));
    }
  }
  function stepStatus(s) {
    return ({ completed: "done", failed: "failed", awaiting_approval: "waiting for approval", skipped_hop_limit: "skipped (step limit)" })[s] || String(s || "pending");
  }
  async function loadApprovalsInto(bar) {
    try {
      const items = await api("GET", "/approvals");
      const pending = items.filter((a) => a.status === "pending");
      if (!pending.length) { bar.appendChild(el("span", "small muted", "nothing waiting for approval right now.")); return; }
      pending.forEach((a) => {
        const row = el("div", "row approval-row");
        row.appendChild(el("span", "small", a.summary || a.action_class || "approval"));
        row.appendChild(btn("Approve", "primary small", async () => {
          try { await api("POST", "/approvals/" + encodeURIComponent(a.id) + "/approve"); toast("approved"); row.remove(); }
          catch (e) { toast(e.message, true); }
        }, "approve " + (a.id || "")));
        row.appendChild(btn("Reject", "danger small", async () => {
          try { await api("POST", "/approvals/" + encodeURIComponent(a.id) + "/reject"); toast("rejected"); row.remove(); }
          catch (e) { toast(e.message, true); }
        }, "reject " + (a.id || "")));
        bar.appendChild(row);
      });
    } catch (e) { bar.appendChild(el("span", "small", "couldn't load approvals: " + e.message)); }
  }
  function delegationLine(delegations) {
    const names = (delegations || []).filter((d) => d.status === "completed").map((d) => d.bot_name);
    if (!names.length) return null;
    return el("div", "delegated muted small", "delegated to " + names.join(", "));
  }
  async function sendMessage(text, ta, send) {
    const scroll = $("chatScroll");
    const errSlot = clear($("chatErr"));
    scroll.appendChild(el("div", "bubble user", text));
    scroll.scrollTop = scroll.scrollHeight;
    ta.value = "";
    send.disabled = true;
    try {
      const thinking = thinkingRow(state.selectedBot ? "thinking..." : "thinking...");
      scroll.appendChild(thinking);
      scroll.scrollTop = scroll.scrollHeight;
      const res = await api("POST", "/chat", { message: text, conversation_id: state.convId, bot_id: state.selectedBot || null });
      thinking.remove();
      state.convId = res.conversation_id;
      const dl = delegationLine(res.delegations);
      if (dl) scroll.appendChild(dl);
      scroll.appendChild(assistantBubble(res.reply || "", res.bot));
      scroll.scrollTop = scroll.scrollHeight;
      loadConvs();
      loadProviderStatus();
    } catch (e) {
      const t = scroll.querySelector(".thinking");
      if (t) t.remove();
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
          (providers[k].models || []).forEach((m) => {
            const mo = el("option", "", "  " + k + " / " + m.model + (m.healthy ? "" : " (cooling)"));
            mo.value = k;
            mo.disabled = true;
            sel.appendChild(mo);
          });
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
      const fill = $("usageBarFill");
      if (fill && usage.used_this_hour != null && usage.remaining_this_hour != null) {
        const total = usage.used_this_hour + usage.remaining_this_hour;
        fill.style.width = total ? Math.max(4, Math.round((usage.remaining_this_hour / total) * 100)) + "%" : "60%";
      }
      refreshApprovals();
    } catch (e) {
      $("statusPill").textContent = "status unavailable";
      $("statusPill").className = "status-pill bad";
    }
  }
  async function refreshApprovals() {
    const chip = $("approvalChip") || document.querySelector(".composer .chip");
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
    const hero = el("div", "hero");
    const orbs = el("div", "hero-orbs");
    ["\u25d7", "\u2726", "\u25a4"].forEach((g, i) => orbs.appendChild(el("span", "orb " + ["p", "g", "b"][i], g)));
    hero.appendChild(orbs);
    hero.appendChild(el("h2", "", "Put your bots on a schedule"));
    hero.appendChild(el("p", "", "A routine runs a prompt on its own: every morning, every few hours, or when a webhook calls it. System jobs run on this server; reminders you add below are stored here."));
    hero.appendChild(btn("New reminder", "primary", () => { const f = $("routineForm"); if (f) { f.scrollIntoView({ behavior: "smooth", block: "center" }); const n = $("routineName"); if (n) n.focus(); } }, "add a new reminder"));
    body.appendChild(hero);
    body.appendChild(el("div", "sec", "Or start from one of these"));
    const presets = [
      ["Morning brief", "What today needs: the calendar, the mail worth a reply, and anything addressed to you.", "Every morning"],
      ["Inbox triage", "Sort new mail into reply, read, and noise, and draft the replies.", "Every hour"],
      ["Ad performance check", "Pull spend and results since yesterday; flag exactly what changed.", "Every morning"],
      ["Content calendar", "Draft next week's posts from the pinned playbook.", "Weekly"],
      ["Lead follow-up", "List open leads and draft the next nudge for each.", "Every morning"],
      ["Week in review", "What shipped this week, grouped by theme.", "Weekly"],
    ];
    const tgrid = el("div", "template-grid");
    presets.forEach(([t, d, s]) => {
      const tc = el("div", "template-card");
      tc.appendChild(el("div", "t-ico", "\u2726"));
      tc.appendChild(el("h3", "", t));
      tc.appendChild(el("p", "", d));
      tc.appendChild(el("div", "row", "")).appendChild(btn("Use", "small", () => {
        const f = $("routineForm"); const n = $("routineName"); const sc = $("routineWhen");
        if (n) n.value = t; if (sc) sc.value = s;
        if (f) f.scrollIntoView({ behavior: "smooth", block: "center" });
        if (n) n.focus();
      }, "use preset " + t));
      tgrid.appendChild(tc);
    });
    body.appendChild(tgrid);
    body.appendChild(el("p", "muted small", "System jobs run on this server. Reminders you add here are stored but do not run automatically yet."));
    const sysWrap = el("div");
    const userWrap = el("div");
    body.appendChild(el("div", "sec", "System jobs"));
    body.appendChild(sysWrap);
    body.appendChild(el("div", "sec", "Your reminders"));
    body.appendChild(notice("runs_automatically: false. Reminders are saved only. They do not fire on their own yet."));
    body.appendChild(userWrap);
    const form = el("form", "card");
    form.id = "routineForm";
    form.style.margin = "12px 0";
    const name = el("input", "input wide");
    name.id = "routineName";
    name.placeholder = "Reminder title";
    name.maxLength = 80;
    name.setAttribute("aria-label", "Reminder title");
    const sched = el("input", "input");
    sched.id = "routineWhen";
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
      const iconFor = (c) => {
        const n = String(c.label || c.name || "?");
        if (/gmail|mail/i.test(n)) return ["G", "gmail"];
        if (/github/i.test(n)) return ["\u2325", "github"];
        if (/telegram/i.test(n)) return ["\u2708", "telegram"];
        if (/whatsapp/i.test(n)) return ["\u2706", "whatsapp"];
        if (/discord/i.test(n)) return ["\u25c8", "discord"];
        return [n.slice(0, 1).toUpperCase(), ""];
      };
      d.connectors.forEach((c) => {
        const card = el("div", "conn-card");
        const [g, cls] = iconFor(c);
        card.appendChild(el("div", "conn-ico " + cls, g));
        const main = el("div", "conn-main");
        main.appendChild(el("div", "conn-name", c.label || c.name));
        main.appendChild(el("div", "conn-sub", (c.is_ads ? "ads platform" : "inbox / other") + (c.configured ? " \u00b7 connected" : " \u00b7 available")));
        card.appendChild(main);
        const acts = el("div", "conn-actions");
        acts.appendChild(chip(c.configured ? "Connected" : "Available", c.configured ? "on" : "off"));
        card.appendChild(acts);
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
        (p.models || []).forEach((m, i) => {
          const mr = el("div", "row");
          mr.style.justifyContent = "space-between";
          mr.style.padding = "6px 0 6px 18px";
          mr.style.borderBottom = "1px solid var(--hair)";
          const used = m.last_used ? " · last used" : "";
          const tried = m.tried ? "" : " · not tried yet";
          mr.appendChild(el("div", "small", (i + 1) + ". " + m.model + used + tried));
          let label = m.healthy ? "healthy" : "cooling down " + m.cooldown_s + "s";
          mr.appendChild(chip(label, m.healthy ? "on" : "warn"));
          card.appendChild(mr);
        });
      });
      pane.appendChild(card);
      const cascadeNote = el("div", "small muted", "Gemini tries these models in order on a timeout, 429, 503 or network error, then falls back to the next provider. Health and last-used are from the live router state.");
      cascadeNote.style.marginTop = "8px";
      pane.appendChild(cascadeNote);
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


  // ---------- boot ----------
  function init() {
    applyAccent();
    document.querySelectorAll(".rail-item").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));
    window.addEventListener("hashchange", () => go((location.hash || "#sessions").slice(1)));
    const start = (location.hash || "#sessions").slice(1);
    go(PAGES[start] ? start : "sessions");
    loadProviderStatus();
  }
  init();
})();
