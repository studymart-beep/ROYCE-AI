/* Royce AI API base override. Default '' = same-origin (the Render service serves this page and the API).
   A separate static host (Vercel) sets window.__ROYCE_API_BASE__ in config.js before this file loads.
   Only a plain http(s) origin is accepted; anything else falls back to same-origin. No secrets here. */
(function () {
  "use strict";
  var raw = window.__ROYCE_API_BASE__;
  var base = "";
  if (typeof raw === "string") {
    var v = raw.trim().replace(/\/+$/, "");
    if (/^https?:\/\/[^\s\/]+$/.test(v)) base = v;
  }
  window.ROYCE_API_BASE = base;
})();
