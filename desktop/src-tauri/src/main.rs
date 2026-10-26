// Royce AI desktop client. This is a thin native shell: it opens the backend workspace (the same
// /workspace the browser uses) in a real Windows window. No AI logic, no secrets, no local data.
//
// Backend URL: set ROYCE_BACKEND_URL at build time (or edit the placeholder in tauri.conf.json) to
// your deployed backend, e.g. https://royce-ai.onrender.com. The tauri.conf.json placeholder is
// ROYCE-BACKEND-URL and is never used as a live URL. It is read once when the window is made.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{Manager, Url, WebviewUrl, WebviewWindowBuilder};

const PLACEHOLDER: &str = "ROYCE-BACKEND-URL";

fn backend_url() -> Result<Url, String> {
    // runtime override first, then build-time value, then the tauri.conf.json window url
    let raw = std::env::var("ROYCE_BACKEND_URL")
        .ok()
        .or_else(|| option_env!("ROYCE_BACKEND_URL").map(|s| s.to_string()))
        .unwrap_or_default();
    let base = raw.trim().trim_end_matches('/').to_string();
    if base.is_empty() {
        return Err("ROYCE_BACKEND_URL is not set. Set it to your backend URL before building or launching.".into());
    }
    if base.contains(PLACEHOLDER) {
        return Err("ROYCE_BACKEND_URL still holds the placeholder. Set your deployed backend URL.".into());
    }
    let url = Url::parse(&format!("{base}/workspace")).map_err(|e| e.to_string())?;
    if url.scheme() != "https" && !matches!(url.host_str(), Some("localhost") | Some("127.0.0.1")) {
        return Err("backend URL must be https (plain http only for localhost).".into());
    }
    Ok(url)
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let url = match backend_url() {
                Ok(u) => WebviewUrl::External(u),
                Err(msg) => {
                    eprintln!("{msg}");
                    // open the bundled notice instead of a blank or wrong window
                    WebviewUrl::App("index.html".into())
                }
            };
            WebviewWindowBuilder::new(app, "main", url)
                .title("Royce AI")
                .inner_size(1280.0, 800.0)
                .min_inner_size(1024.0, 640.0)
                .center()
                .build()?;
            let _ = app.get_webview_window("main");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Royce AI");
}
