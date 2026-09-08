import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowUpRight, Download, Loader2 } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { isDesktop, request } from "./api";
import { Modal } from "./Management";
import { version } from "../package.json";

interface Release {
  status: "ok" | "no_release" | "unavailable";
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
      setRelease(await request<Release>("/updates"));
    } catch {
      setError("暂时无法检查更新，请稍后重试。");
    } finally {
      checking.current = false;
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!local) return;
    const start = setTimeout(() => void check(), 5000);
    const timer = setInterval(() => void check(), 6 * 60 * 60 * 1000);
    const focus = () => {
      if (Date.now() - lastCheck.current >= 60 * 60 * 1000) void check();
    };
    window.addEventListener("focus", focus);
    return () => {
      clearTimeout(start);
      clearInterval(timer);
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
      {release?.available ? <><Download size={13} />发现新版本</> : `v${version}`}
    </button>
    {open && createPortal(<Modal title="软件更新" onClose={() => setOpen(false)}>
      <div className="help-content update-content">
        <p>当前版本 v{version}</p>
        {busy ? <p role="status"><Loader2 size={16} className="spin" /> 正在检查更新…</p>
          : error || release?.status === "unavailable" ? <p role="status">{error || "暂时无法连接 GitHub，请稍后重试。本地阅读和同步不受影响。"}</p>
          : release?.available ? <>
            <h3>新版本 v{release.latest_version}</h3>
            <pre className="release-notes">{release.notes || "版本说明请查看发布页面。"}</pre>
            <p>下载后运行安装包即可升级，本地记录和设置会保留。</p>
          </> : <p role="status">{release?.status === "no_release" ? "还没有可下载的正式版本。" : "已是最新版本。"}</p>}
        <p className="muted">启动时及每 6 小时自动检测正式版；后台检测不会打断阅读。</p>
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
