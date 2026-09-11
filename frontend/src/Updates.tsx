import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowUpRight, Download, Loader2 } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { isDesktop, request } from "./api";
import { Modal } from "./Management";
import { version } from "../package.json";

interface Release {
  status: "ok" | "no_release" | "unavailable" | "rate_limited";
  retry_at?: number;
  available: boolean;
  current_version: string;
  latest_version?: string;
  notes?: string;
  release_url: string;
  download_url?: string | null;
}
export function Updates({ local }: { local: boolean }) {
  const [release, setRelease] = useState<Release | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const checking = useRef(false);
  const lastCheck = useRef(0);
  async function check(manual = false) {
    if (manual) setOpen(true);
    if (checking.current) return;
    checking.current = true;
    lastCheck.current = Date.now();
    setBusy(true);
    setError("");
    try {
      const value = await request<Release>("/updates" + (manual ? "?force=true" : ""));
      setRelease(value);
      return value;
    } catch {
      setError("暂时无法检查更新，将自动重试。");
      return undefined;
    } finally {
      checking.current = false;
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!local) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      const result = await check();
      if (!alive) return;
      const delay = result?.status === 'ok' || result?.status === 'no_release' ? 21600000 : Math.max(60000, ((result?.retry_at || Date.now()/1000+60)-Date.now()/1000)*1000);
      timer = setTimeout(() => void run(), delay);
    };
    timer = setTimeout(() => void run(), 5000);
    const focus = () => {
      if (Date.now() - lastCheck.current >= 5 * 60 * 1000) void check();
    };
    window.addEventListener("focus", focus);
    return () => {
      alive = false;
      clearTimeout(timer);
      window.removeEventListener("focus", focus);
    };
  }, [local]);
  async function visit(url: string) {
    try {
      if (isDesktop()) await invoke("open_release", { url });
      else window.open(url, "_blank", "noopener,noreferrer");
    } catch {
      setError("无法打开浏览器，请前往 GitHub 的 Releases 页面下载。");
    }
  }
  if (!local) return <span>v{version}</span>;
  return <>
    <button className={"version-button " + (release?.available ? "update-available" : "")}
      title="检查软件更新" aria-label="检查软件更新" onClick={() => void check(true)}>
      {release?.available ? <><Download size={13} />发现新版本</> : error || release?.status === 'unavailable' || release?.status === 'rate_limited' ? '更新检查失败' : `v${version}`}
    </button>
    {open && createPortal(<Modal title="软件更新" onClose={() => setOpen(false)}>
      <div className="help-content update-content">
        <p>当前版本 v{version}</p>
        {busy ? <p role="status"><Loader2 size={16} className="spin" /> 正在检查更新…</p>
          : error || release?.status === "unavailable" || release?.status === "rate_limited" ? <p role="status">{error || "更新服务暂时不可用，将自动重试。本地阅读和同步不受影响。"}{release?.retry_at ? ` 下次重试：${new Date(release.retry_at*1000).toLocaleTimeString()}` : ''}</p>
          : release?.available ? <>
            <h3>新版本 v{release.latest_version}</h3>
            <pre className="release-notes">{release.notes || "版本说明请查看发布页面。"}</pre>
            <p>下载后运行安装包即可升级，本地记录和设置会保留。</p>
          </> : <p role="status">{release?.status === "no_release" ? "还没有可下载的正式版本。" : "已是最新版本。"}</p>}
        <p className="muted">启动时及每 6 小时读取静态更新清单；失败会显示提示并按恢复时间自动重试。</p>
      </div>
      <footer className="modal-footer">
        <button className="button" onClick={() => void visit(release?.release_url || "https://github.com/QingQ-zijin/cursor-session-hub/releases")}>
          发布页面 <ArrowUpRight size={14} />
        </button>
        {release?.available && release.download_url
          ? <button className="button primary" onClick={() => void visit(release.download_url!)}><Download size={15} />下载安装包</button>
          : <button className="button" disabled={busy} onClick={() => void check(true)}>重新检查</button>}
      </footer>
    </Modal>, document.body)}
  </>;
}
