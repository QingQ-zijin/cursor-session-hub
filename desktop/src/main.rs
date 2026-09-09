#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Mutex, atomic::{AtomicU16, AtomicU32, Ordering}};
use std::time::Duration;
use base64::Engine;
use futures_util::StreamExt;
use serde_json::{json, Value};
use tauri::{Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::{ShellExt, process::CommandEvent};
use tokio::io::AsyncWriteExt;

struct CoreState {
    port: AtomicU16,
    pid: AtomicU32,
    token: String,
    client: reqwest::Client,
    subscription: Mutex<Option<tauri::async_runtime::JoinHandle<()>>>,
}

fn endpoint(state: &CoreState, path: &str) -> Result<String, String> {
    if !path.starts_with("/api/v1/") || path.contains("..") || path.contains('\\') || path.contains("://") {
        return Err("不允许的客户端接口路径".into());
    }
    let port = state.port.load(Ordering::SeqCst);
    if port == 0 { return Err("本地服务尚未就绪".into()); }
    Ok(format!("http://127.0.0.1:{port}{path}"))
}

async fn bounded_body(response: reqwest::Response, max: usize) -> Result<Vec<u8>, String> {
    if response.content_length().unwrap_or(0) > max as u64 { return Err("内容较大，请使用分段阅读或下载".into()); }
    let mut stream = response.bytes_stream();
    let mut output = Vec::new();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| e.to_string())?;
        if output.len() + chunk.len() > max { return Err("响应超过客户端单次读取上限".into()); }
        output.extend_from_slice(&chunk);
    }
    Ok(output)
}

#[tauri::command]
async fn native_bridge(state: State<'_, CoreState>, method: String, path: String, body: Option<Value>) -> Result<Value, String> {
    if !["GET","POST","PUT","PATCH","DELETE"].contains(&method.as_str()) { return Err("无效方法".into()); }
    let url = endpoint(&state, &path)?;
    let method = reqwest::Method::from_bytes(method.as_bytes()).map_err(|e| e.to_string())?;
    let mut request = state.client.request(method, url).bearer_auth(&state.token);
    if let Some(value) = body {
        if serde_json::to_vec(&value).map_err(|e| e.to_string())?.len() > 1024 * 1024 { return Err("请求内容过大".into()); }
        request = request.json(&value);
    }
    let response = request.send().await.map_err(|e| e.to_string())?;
    let status = response.status();
    let bytes = bounded_body(response, 8 * 1024 * 1024).await?;
    if bytes.is_empty() && status.is_success() { return Ok(json!({"ok":true})); }
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| "服务器响应格式异常".to_string())?;
    if !status.is_success() {
        return Err(json!({"status":status.as_u16(),"message":value.get("detail").and_then(Value::as_str).unwrap_or("请求失败")}).to_string());
    }
    Ok(value)
}

#[tauri::command]
async fn open_release(app: tauri::AppHandle, url: String) -> Result<(), String> {
    let parsed = reqwest::Url::parse(&url).map_err(|_| "无效更新地址".to_string())?;
    if parsed.scheme() != "https" || parsed.host_str() != Some("github.com")
        || !parsed.username().is_empty() || parsed.password().is_some() || parsed.port().is_some()
        || !(parsed.path() == "/QingQ-zijin/cursor-session-hub/releases"
             || parsed.path().starts_with("/QingQ-zijin/cursor-session-hub/releases/")) {
        return Err("仅允许打开本产品的 GitHub 发布地址".into());
    }
    #[allow(deprecated)]
    app.shell().open(parsed.as_str(), None).map_err(|e| e.to_string())
}

#[tauri::command]
async fn choose_files(app: tauri::AppHandle) -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog().file().add_filter("会话与文档", &["jsonl", "json", "md", "markdown", "html", "htm", "pdf"]).blocking_pick_files()
            .unwrap_or_default().into_iter().map(|p| p.into_path().map(|p| p.to_string_lossy().to_string()).map_err(|e|e.to_string())).collect()
    }).await.map_err(|e|e.to_string())?
}

#[tauri::command]
async fn native_asset(state: State<'_, CoreState>, path: String) -> Result<String, String> {
    if !(path.starts_with("/api/v1/assets/") || path.starts_with("/api/v1/remote/api/assets/")) { return Err("不是可访问的图片资源".into()); }
    let response = state.client.get(endpoint(&state, &path)?).bearer_auth(&state.token).send().await.map_err(|e|e.to_string())?;
    if !response.status().is_success() { return Err("图片不存在或已撤销共享".into()); }
    let mime = response.headers().get("content-type").and_then(|s|s.to_str().ok()).unwrap_or("").to_owned();
    if !mime.starts_with("image/") { return Err("资源不是图片".into()); }
    let bytes = bounded_body(response, 12 * 1024 * 1024).await?;
    Ok(format!("data:{mime};base64,{}", base64::engine::general_purpose::STANDARD.encode(bytes)))
}

#[tauri::command]
async fn save_download(app: tauri::AppHandle, state: State<'_, CoreState>, path: String, filename: String) -> Result<Option<String>, String> {
    let url = endpoint(&state, &path)?;
    let name = std::path::Path::new(&filename).file_name().and_then(|s|s.to_str()).unwrap_or("transcript.html").to_owned();
    let chosen = tauri::async_runtime::spawn_blocking(move || {
        let dialog = app.dialog().file().set_file_name(&name);
        let dialog = if name.ends_with(".md") { dialog.add_filter("Markdown 文档 (*.md)", &["md"]) }
            else if name.ends_with(".pdf") { dialog.add_filter("PDF 文档 (*.pdf)", &["pdf"]) }
            else if name.ends_with(".html") { dialog.add_filter("HTML 网页 (*.html)", &["html"]) }
            else { dialog };
        dialog.blocking_save_file()
    }).await.map_err(|e|e.to_string())?;
    let Some(chosen) = chosen else { return Ok(None); };
    let target = chosen.into_path().map_err(|e|e.to_string())?;
    let response = state.client.get(url).bearer_auth(&state.token).send().await.map_err(|e|e.to_string())?;
    if !response.status().is_success() { return Err("下载失败或访问权限已改变".into()); }
    let temp = target.with_extension(format!("{}.part", uuid::Uuid::new_v4()));
    let result: Result<(), String> = async {
        let mut file = tokio::fs::File::create(&temp).await.map_err(|e|e.to_string())?;
        let mut stream = response.bytes_stream();
        let mut size = 0usize;
        while let Some(block) = stream.next().await {
            let block = block.map_err(|e|e.to_string())?;
            size += block.len();
            if size > 2 * 1024 * 1024 * 1024 { return Err("导出文件超过 2 GiB".into()); }
            file.write_all(&block).await.map_err(|e|e.to_string())?;
        }
        file.flush().await.map_err(|e|e.to_string())?;
        drop(file);
        tokio::fs::rename(&temp, &target).await.map_err(|e|e.to_string())?;
        Ok(())
    }.await;
    if result.is_err() { let _ = tokio::fs::remove_file(&temp).await; }
    result?;
    Ok(Some(target.to_string_lossy().to_string()))
}

#[tauri::command]
async fn unsubscribe_activity(state: State<'_, CoreState>) -> Result<(), String> {
    if let Some(task) = state.subscription.lock().map_err(|e|e.to_string())?.take() { task.abort(); }
    Ok(())
}

#[tauri::command]
async fn subscribe_activity(app: tauri::AppHandle, state: State<'_, CoreState>, path: String) -> Result<(), String> {
    if !["/api/v1/activity", "/api/v1/remote/api/activity"].contains(&path.as_str()) { return Err("无效订阅".into()); }
    let url = endpoint(&state, &path)?;
    if let Some(task) = state.subscription.lock().map_err(|e|e.to_string())?.take() { task.abort(); }
    let client = state.client.clone();
    let token = state.token.clone();
    let task = tauri::async_runtime::spawn(async move {
        let mut delay = 1u64;
        loop {
            if let Ok(response) = client.get(&url).bearer_auth(&token).timeout(Duration::from_secs(86400)).send().await {
                if response.status().is_success() {
                    delay = 1;
                    let mut stream = response.bytes_stream();
                    let mut pending = String::new();
                    while let Some(Ok(chunk)) = stream.next().await {
                        pending.push_str(&String::from_utf8_lossy(&chunk));
                        if pending.len() > 262144 { break; }
                        while let Some(end) = pending.find('\n') {
                            let line: String = pending.drain(..=end).collect();
                            if let Some(data) = line.trim().strip_prefix("data:") {
                                if let Ok(value) = serde_json::from_str::<Value>(data.trim()) { let _ = app.emit("hub-activity", value); }
                            }
                        }
                    }
                }
            }
            tokio::time::sleep(Duration::from_secs(delay)).await;
            delay = (delay * 2).min(30);
        }
    });
    *state.subscription.lock().map_err(|e|e.to_string())? = Some(task);
    Ok(())
}

fn terminate_owned_backend(state: &CoreState) {
    if let Ok(mut guard) = state.subscription.lock() { if let Some(task) = guard.take() { task.abort(); } }
    let pid = state.pid.swap(0, Ordering::SeqCst);
    if pid == 0 { return; }
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        let _ = std::process::Command::new("taskkill").args(["/PID", &pid.to_string(), "/T", "/F"]).creation_flags(0x08000000).output();
    }
    #[cfg(not(target_os = "windows"))]
    { let _ = std::process::Command::new("kill").args(["-TERM", &pid.to_string()]).output(); }
}

fn main() {
    std::panic::set_hook(Box::new(|info| {
        let message = info.to_string();
        if let Ok(path) = std::env::var("CSH_SMOKE_OUTPUT") {
            let _ = std::fs::write(path, json!({"ok":false,"panic":message}).to_string());
        }
        eprintln!("Cursor Session Hub startup error: {message}");
    }));
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![native_bridge, open_release, choose_files, native_asset, save_download, subscribe_activity, unsubscribe_activity])
        .setup(|app| {
            let token = uuid::Uuid::new_v4().to_string() + &uuid::Uuid::new_v4().to_string();
            let (mut events, child) = app.shell().sidecar("hub-core")?
                .args(["serve", "--mode", "local", "--host", "127.0.0.1", "--port", "0"])
                .env("CSH_LOCAL_TOKEN", &token)
                .env("CSH_NATIVE_PARENT_PID", std::process::id().to_string())
                .spawn()?;
            let pid = child.pid();
            let readiness = tauri::async_runtime::block_on(async {
                tokio::time::timeout(Duration::from_secs(60), async {
                    let mut pending = String::new();
                    let mut diagnostics = String::new();
                    while let Some(event) = events.recv().await {
                        match event {
                            CommandEvent::Stdout(bytes) => {
                                pending.push_str(&String::from_utf8_lossy(&bytes));
                                // The shell plugin may deliver lines without a newline.
                                if let Some(start) = pending.find("CSH_READY ") {
                                    let json_text = pending[start + 10..].trim();
                                    if let Ok(info) = serde_json::from_str::<Value>(json_text) {
                                        if let Some(port) = info.get("port").and_then(Value::as_u64) { return Ok(port as u16); }
                                    }
                                }
                                if pending.len() > 262144 { return Err("Startup output too large".to_owned()); }
                            },
                            CommandEvent::Stderr(bytes) => {
                                if diagnostics.len() < 32768 { diagnostics.push_str(&String::from_utf8_lossy(&bytes)); }
                            },
                            CommandEvent::Terminated(_) => return Err(format!("Local service exited before becoming ready: {diagnostics}")),
                            _ => {},
                        }
                    }
                    Err("Local service connection closed".to_owned())
                }).await
            });
            let port = readiness.map_err(|_|"本地服务启动超时")?.map_err(std::io::Error::other)?;
            app.manage(CoreState { port:AtomicU16::new(port), pid:AtomicU32::new(pid), token,
                client:reqwest::Client::builder().timeout(Duration::from_secs(120)).build()?, subscription:Mutex::new(None) });
            let handle = app.handle().clone();
            // Drain process output continuously so a full pipe cannot block it.
            tauri::async_runtime::spawn(async move {
                while let Some(event) = events.recv().await {
                    if matches!(event, CommandEvent::Terminated(_)) { let _ = handle.emit("hub-activity", json!({"kind":"service_stopped"})); break; }
                }
            });
            if std::env::args().any(|s|s == "--smoke-test") {
                let state = app.state::<CoreState>();
                let check = tauri::async_runtime::block_on(async {
                    state.client.get(format!("http://127.0.0.1:{port}/api/v1/capabilities")).bearer_auth(&state.token).send().await
                });
                let success = check.map(|r|r.status().is_success()).unwrap_or(false);
                if let Ok(path) = std::env::var("CSH_SMOKE_OUTPUT") { let _ = std::fs::write(path, json!({"ok":success,"backend_ready":true,"version":env!("CARGO_PKG_VERSION")}).to_string()); }
                terminate_owned_backend(&state);
                app.handle().exit(if success {0} else {1});
            } else if let Some(window) = app.get_webview_window("main") { window.show()?; }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Unable to start Cursor Session Hub");
    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(state) = handle.try_state::<CoreState>() { terminate_owned_backend(&state); }
        }
    });
}
