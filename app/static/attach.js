/* Royce AI chat attachments, shared by /workspace and /m: the + button, the thumbnail strip, upload
   to /api/ws/upload, and inline rendering of sent attachments (image) or file cards (documents).
   Text is always set with textContent. Exposes window.RoyceAttach. */
(function () {
  "use strict";
  var ACCEPT = "image/png,image/jpeg,image/webp,application/pdf,.txt,.csv,.docx";
  var IMG_EXT = ["png", "jpg", "jpeg", "webp"];
  var DOC_EXT = ["pdf", "txt", "csv", "docx"];
  var MAX_FILES = 4;
  var IMG_MAX = 10 * 1024 * 1024;
  var DOC_MAX = 20 * 1024 * 1024;

  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function base() { return window.ROYCE_API_BASE || ""; }
  // served attachment urls are always /uploads/... from the server; anything else renders as nothing
  function fileUrl(u) { u = String(u || ""); return u.indexOf("/uploads/") === 0 ? base() + u : ""; }
  function extOf(name) { var m = /\.([A-Za-z0-9]+)$/.exec(String(name || "")); return m ? m[1].toLowerCase() : "file"; }
  function fmtSize(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  // client-side pre-check for fast feedback; the server re-checks everything (type, size, magic bytes)
  function precheck(file) {
    var ext = extOf(file.name);
    if (IMG_EXT.indexOf(ext) >= 0) {
      if (file.size > IMG_MAX) return "images must be 10 MB or smaller";
      return "";
    }
    if (DOC_EXT.indexOf(ext) >= 0) {
      if (file.size > DOC_MAX) return "files must be 20 MB or smaller";
      return "";
    }
    return "that file type is not supported. use png, jpg, webp, pdf, txt, csv or docx.";
  }

  async function upload(file, convId) {
    var fd = new FormData();
    fd.append("file", file);
    if (convId) fd.append("conversation_id", convId);
    var r = await fetch(base() + "/api/ws/upload", { method: "POST", body: fd, cache: "no-store" });
    var data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) {
      var d = data && data.detail !== undefined ? data.detail : data;
      throw new Error(String((d && d.message) || "upload failed (" + r.status + ")"));
    }
    return data;
  }

  // One attach control for one composer. opts: {getConvId(), onChange(), toast(msg, bad)}
  function create(opts) {
    var pending = [];
    var input = document.createElement("input");
    input.type = "file";
    input.accept = ACCEPT;
    input.multiple = true;
    input.hidden = true;
    input.className = "attach-input";
    input.setAttribute("aria-label", "attach files");
    var button = node("button", "attach-btn", "+");
    button.type = "button";
    button.title = "attach an image or file";
    button.setAttribute("aria-label", "attach an image or file");
    button.addEventListener("click", function () { input.click(); });
    var strip = node("div", "attach-strip");
    strip.hidden = true;

    function notify(msg) { if (opts && opts.toast) opts.toast(msg, true); }
    function changed() { render(); if (opts && opts.onChange) opts.onChange(); }
    function render() {
      strip.textContent = "";
      pending.forEach(function (p) {
        var t = node("div", "attach-thumb" + (p.kind === "image" ? " is-img" : ""));
        if (p.kind === "image" && p.preview) {
          var img = document.createElement("img");
          img.src = p.preview;
          img.alt = p.name;
          t.appendChild(img);
        } else {
          t.appendChild(node("span", "att-ext", extOf(p.name).toUpperCase()));
        }
        t.appendChild(node("span", "att-tname", p.uploading ? "uploading " + p.name : p.name));
        var x = node("button", "att-x", "\u00d7");
        x.type = "button";
        x.setAttribute("aria-label", "remove " + p.name);
        x.addEventListener("click", function () { removeSlot(p); });
        t.appendChild(x);
        strip.appendChild(t);
      });
      strip.hidden = pending.length === 0;
    }
    function removeSlot(p) {
      var i = pending.indexOf(p);
      if (i >= 0) pending.splice(i, 1);
      if (p.preview) URL.revokeObjectURL(p.preview);
      changed();
    }

    input.addEventListener("change", async function () {
      var files = Array.prototype.slice.call(input.files || []);
      input.value = "";
      for (var i = 0; i < files.length; i++) {
        var f = files[i];
        if (pending.length >= MAX_FILES) { notify("up to " + MAX_FILES + " files per message"); break; }
        var problem = precheck(f);
        if (problem) { notify(problem); continue; }
        var isImg = IMG_EXT.indexOf(extOf(f.name)) >= 0;
        var slot = { name: f.name, kind: isImg ? "image" : "file", preview: isImg ? URL.createObjectURL(f) : "", uploading: true };
        pending.push(slot);
        changed();
        try {
          var info = await upload(f, opts && opts.getConvId ? opts.getConvId() : null);
          slot.url = info.url; slot.name = info.name; slot.type = info.type; slot.size = info.size; slot.kind = info.kind;
          slot.uploading = false;
          changed();
        } catch (e) {
          removeSlot(slot);
          notify(e.message || "upload failed");
        }
      }
    });

    return {
      button: button,
      input: input,
      strip: strip,
      count: function () { return pending.length; },
      uploading: function () { return pending.some(function (p) { return p.uploading; }); },
      // the finished attachments for one send; clears the strip
      take: function () {
        var out = pending.filter(function (p) { return !p.uploading; }).map(function (p) {
          return { url: p.url, name: p.name, type: p.type, size: p.size, kind: p.kind };
        });
        pending.forEach(function (p) { if (p.preview) URL.revokeObjectURL(p.preview); });
        pending.length = 0;
        render();
        if (opts && opts.onChange) opts.onChange();
        return out;
      }
    };
  }

  // Rendered attachments on a message: inline image (opens full size) or a file card with name, size, download.
  function nodes(list) {
    var wrap = node("div", "attachments");
    (list || []).forEach(function (a) {
      if (!a) return;
      var href = fileUrl(a.url);
      if (!href) return;
      if (a.kind === "image") {
        var link = document.createElement("a");
        link.href = href; link.target = "_blank"; link.rel = "noopener"; link.className = "att-link";
        var img = document.createElement("img");
        img.src = href; img.alt = a.name || "image"; img.loading = "lazy"; img.className = "att-img";
        link.appendChild(img);
        wrap.appendChild(link);
      } else {
        var card = document.createElement("a");
        card.href = href; card.download = a.name || "file"; card.rel = "noopener"; card.className = "att-file";
        card.appendChild(node("span", "att-ext", extOf(a.name).toUpperCase()));
        var meta = node("span", "att-meta");
        meta.appendChild(node("span", "att-name", a.name || "file"));
        meta.appendChild(node("span", "att-size", fmtSize(a.size || 0) + " \u00b7 download"));
        card.appendChild(meta);
        wrap.appendChild(card);
      }
    });
    return wrap;
  }

  window.RoyceAttach = { create: create, nodes: nodes, fileUrl: fileUrl, ACCEPT: ACCEPT };
})();
