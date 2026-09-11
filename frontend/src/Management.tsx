import { Fragment, useEffect, useRef, useState } from "react";
import {BatchProgress,startWorkspaceBatch} from './BatchProgress';
import {unnamed,batchDestination} from './source-names';
import {
  X,
  Search,
  RefreshCw,
  FilePlus2,
  Loader2,
  ArrowUpRight,
  Server,
  Eye,
  EyeOff,
  Copy,
  Download,
  Pause,
  Play,
  RotateCcw,
  UserPlus,
  Trash2,
  AlertCircle,
  CheckCircle2,
  Circle,
  LogOut,
} from "lucide-react";
import { asPage, client, download, post, query, request } from "./api";
import type { Client } from "./api";
import type { Invite, Job, Page, Remote, Session, Source, User } from "./types";
import { bytes, copyText, displayDate, label } from "./utils";

export function Modal({
  title,
  children,
  onClose,
  wide = false,
  className = '',
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  wide?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    ref.current?.focus();
    function key(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const focusable = ref.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]),input:not([disabled]),select,textarea,a[href],[tabindex="0"]',
        );
        if (!focusable?.length) return;
        const first = focusable[0],
          last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={"modal " + (wide ? "wide " : "") + className}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
      >
        <header>
          <h2>{title}</h2>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="关闭对话框"
          >
            <X size={19} />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}

export function SourcePicker({
  onClose,
  onIndexed,
  onError,
  signal,
}: {
  onClose: () => void;
  onIndexed: () => void;
  onError: (e: unknown) => void;
  signal: number;
}) {
  const [sources, setSources] = useState<Source[]>([]),
    [destination,setDestination]=useState(batchDestination),
    [batchSignal,setBatchSignal]=useState(0),
    [workspace, setWorkspace] = useState('__all__'),
    [alternates, setAlternates] = useState(false),
    [workspaces, setWorkspaces] = useState<{project: string; count: number}[]>([]),
    [workspaceNext, setWorkspaceNext] = useState<string | null>(null),
    [loading, setLoading] = useState(true),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [q, setQ] = useState(""),
    [busy, setBusy] = useState(false),
    [scanning, setScanning] = useState(false),
    [page, setPage] = useState(0),
    [next, setNext] = useState<string | null>(null),
    [cursors, setCursors] = useState<string[]>([""]);
  const generation = useRef(0);
  async function loadWorkspaces(cursor?: string) {
    const data = await request<Page<{project: string; count: number}>>('/sources/workspaces' + query({cursor, limit: 100, include_alternates: alternates}));
    setWorkspaces(old => cursor === undefined ? data.items : [...old, ...data.items]);
    setWorkspaceNext(data.next_cursor || null);
  }
  async function load() {
    const ticket = ++generation.current;
    setLoading(true);
    try {
      const r = await request<Page<Source>>(
        "/sources" + query({ cursor: cursors[page], q, limit: 50, include_alternates: alternates }) + (workspace !== '__all__' ? '&project=' + encodeURIComponent(workspace) : ''),
      );
      if (ticket !== generation.current) return;
      setSources(asPage(r).items);
      setNext(r.next_cursor || null);
    } catch (e) {
      if (ticket === generation.current) onError(e);
    } finally { if (ticket === generation.current) setLoading(false); }
  }
  useEffect(() => {
    const timer = setTimeout(() => void load(), 250);
    return () => clearTimeout(timer);
  }, [page, q, signal, workspace, alternates]);
  useEffect(() => { void loadWorkspaces().catch(onError); }, [signal, alternates]);
  useEffect(() => { void scan(); return () => { generation.current++; }; }, []);
  const filtered = sources;
  const foldedSource=(s:Source)=>unnamed(s.title)||s.metadata_json?.is_subagent===true||s.metadata_json?.in_sidebar===false||s.source_kind==='cursor_cli';
  const sourceGroups=[...new Set(filtered.map(s=>s.project||''))].map(project=>({project,items:filtered.filter(s=>(s.project||'')===project)}));
  async function batch(project:string|null){try{await startWorkspaceBatch(project,destination);setBatchSignal(v=>v+1);onIndexed()}catch(e){onError(e)}}
  async function index() {
    setBusy(true);
    let done = 0;
    try {
      for (const id of selected) {
        await post("/sources/" + id + "/index");
        done++;
      }
      onIndexed();
      onClose();
    } catch (e) {
      onError(e);
      if (done) onIndexed();
    } finally {
      setBusy(false);
    }
  }
  async function scan() {
    setScanning(true);
    try {
      await post("/sources/scan");
      onIndexed();
      await load();
    } catch (e) {
      onError(e);
    } finally {
      setScanning(false);
    }
  }
  return (
    <Modal title="导入会话" onClose={onClose} wide className="source-import">
      <div className="source-toolbar">
        <div className="search-field">
          <Search size={16} />
          <input
            placeholder="搜索标题或项目"
            aria-label="搜索本机来源"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(0);
            }}
          />
        </div>
        <button
          className="button source-refresh"
          aria-label="发现记录"
          onClick={() => void scan()}
          disabled={scanning}
        >
          {scanning ? (
            <Loader2 size={15} className="spin" />
          ) : (
            <RefreshCw size={15} />
          )}
          刷新
        </button>
      </div>
      <div className="workspace-filter" data-destination={destination}>
        <label>同步目标 <select aria-label="工作区同步目标" value={destination} onChange={e=>{setDestination(e.target.value as 'local'|'server');localStorage.setItem('csh-batch-destination',e.target.value)}}><option value="local">本地库</option><option value="server">团队服务器</option></select></label>
        <label>工作区 <select aria-label="选择 Cursor 工作区" value={workspace} onChange={e => {
          generation.current++; setSources([]); setLoading(true); setWorkspace(e.target.value); setPage(0); setCursors(['']);
        }}><option value="__all__">全部工作区</option>{workspaces.map(w => <option key={w.project} value={w.project}>{w.project || '未记录工作区'}（{w.count}）</option>)}</select></label>
        {workspaceNext !== null && <button className="text-button" onClick={() => void loadWorkspaces(workspaceNext).catch(onError)}>更多工作区</button>}
        <label className="source-alternates"><input type="checkbox" checked={alternates} onChange={e => { setAlternates(e.target.checked); setPage(0); setCursors(['']); }} />JSONL 副本</label>
      </div>
      <div className="source-list">
        {loading ? <div className="loading-line">加载中…</div> : !filtered.length ? (
          <div className="empty-small">
            <FilePlus2 size={25} />
            <p>暂无记录</p>
          </div>
        ) : (
          sourceGroups.map(group=><section key={group.project}><div className="source-workspace-heading"><h3 className="workspace-group" title={group.project}>{group.project.split(/[\\/]/).filter(Boolean).pop()||'未记录工作区'}</h3>{group.project&&<button className="text-button" onClick={()=>void batch(group.project)}>同步整个工作区</button>}</div>{[false,true].map(folded=>{
            const rows=group.items.filter(s=>foldedSource(s)===folded);const content=rows.map(s=>(
            <label key={s.id} className={"source-row "+(selected.has(s.id)?"selected":"")} data-state={s.status}>
              <input
                type="checkbox"
                checked={selected.has(s.id)}
                onChange={(e) =>
                  setSelected((prev) => {
                    const next = new Set(prev);
                    if (e.target.checked) next.add(s.id);
                    else next.delete(s.id);
                    return next;
                  })
                }
              />
              <span>
                <strong>{s.title || "未命名会话"}</strong>
                <small className="source-kind" title={s.path}>
                  {s.source_kind === 'cursor_ide' ? 'Cursor IDE' : s.source_kind === 'cursor_cli' ? 'Cursor CLI' : '会话文件'}
                </small>
                <details><summary>来源信息</summary><small>{s.path}</small><small>ID：{s.native_id}</small></details>
              </span>
              <em className="source-status" data-state={s.status}>{label(s.status)}</em>
            </label>));return folded?rows.length>0&&<details className="unnamed-sources" key="unnamed"><summary>其他记录 <span>{rows.length}</span></summary>{content}</details>:<Fragment key="named">{content}</Fragment>
          })}</section>)
        )}
      </div>
      <BatchProgress signal={batchSignal} onError={onError}/>
      {(next || page > 0) && (
        <div className="pagination">
          <button disabled={!page} onClick={() => setPage((v) => v - 1)}>
            上一页
          </button>
          <span>第 {page + 1} 页</span>
          <button
            disabled={!next}
            onClick={() => {
              if (next) {
                setCursors((v) => [...v.slice(0, page + 1), next]);
                setPage((v) => v + 1);
              }
            }}
          >
            下一页
          </button>
        </div>
      )}
      <footer className="modal-footer">
        <span>已选 {selected.size} / 10</span>
        <button className="button source-all" onClick={()=>void batch(null)}>同步全部工作区</button>
        <button
          className="button primary"
          onClick={() => void index()}
          disabled={busy || !selected.size || selected.size > 10}
        >
          {busy ? (
            <Loader2 size={15} className="spin" />
          ) : (
            <FilePlus2 size={15} />
          )}
          导入所选
        </button>
      </footer>
    </Modal>
  );
}

export function Login({
  local,
  onSuccess,
  onError,
  initialToken = "",
}: {
  local: boolean;
  onSuccess: (user: User) => void;
  onError: (e: unknown) => void;
  initialToken?: string;
}) {
  const [url, setUrl] = useState(""),
    [allowHttp, setAllowHttp] = useState(false),
    [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [displayName, setDisplayName] = useState(""),
    [token, setToken] = useState(initialToken),
    [register, setRegister] = useState(!!initialToken),
    [busy, setBusy] = useState(false),
    [show, setShow] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    if (local)
      void request<Remote>("/remote/config")
        .then((r) => { setUrl(r.url || r.server_url || ""); setAllowHttp(r.allow_insecure_http === true); })
        .catch(() => {});
  }, [local]);
  async function submit() {
    setBusy(true);
    setError("");
    try {
      if (local) {
        await request("/remote/config", {
          method: "PUT",
          body: JSON.stringify({ url, allow_insecure_http: allowHttp }),
        });
        if (register)
          await post("/remote/api/auth/register", {
            token,
            username,
            display_name: displayName,
            password,
          });
        const r = await post<{ user: User }>("/remote/login", {
          username,
          password,
        });
        onSuccess(r.user);
      } else {
        const r = await post<{ user: User }>(
          register ? "/auth/register" : "/auth/login",
          register
            ? { token, username, display_name: displayName, password }
            : { username, password },
        );
        onSuccess(r.user);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-stage">
      <div className="login-form">
        <div className="login-mark">
          <Server size={26} />
        </div>
        <h1>
          {register
            ? "加入团队空间"
            : local
              ? "连接团队服务器"
              : "登录团队空间"}
        </h1>
        <p>
          {local
            ? "本地记录保持独立，只有你选择的会话会同步到团队。"
            : "查看团队的工作记录，继续每一次讨论。"}
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          {local && (
            <label>
              服务器地址
              <input
                required
                type="url"
                placeholder="https://sessions.example.com"
                value={url}
                onChange={(e) => { setUrl(e.target.value); setAllowHttp(false); }}
                autoComplete="url"
              />
            </label>
          )}
          {local && /^http:\/\//i.test(url.trim()) && (
            <label className="http-consent">
              <input type="checkbox" checked={allowHttp} onChange={(e) => setAllowHttp(e.target.checked)} />
              <span>允许 HTTP 内测连接<small>仅对此地址生效；登录密码和同步内容不经过 HTTPS 加密。</small></span>
            </label>
          )}
          {register && (
            <>
              <label>
                邀请凭证
                <input
                  required
                  value={token}
                  onChange={(e) => {let value=e.target.value;try{value=new URL(value).searchParams.get('invite')||value}catch{}setToken(value)}}
                  placeholder="粘贴管理员发来的邀请链接或凭证"
                />
              </label>
              <label>
                显示名称
                <input
                  required
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  autoComplete="name"
                  maxLength={80}
                />
              </label>
            </>
          )}
          <label>
            用户名
            <input
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              maxLength={80}
            />
          </label>
          <label>
            密码
            <div className="password-field">
              <input
                required
                type={show ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={register ? "new-password" : "current-password"}
                minLength={register ? 12 : undefined}
              />
              <button
                type="button"
                aria-label={show ? "隐藏密码" : "显示密码"}
                onClick={() => setShow(!show)}
              >
                {show ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </div>
          </label>
          {error && (
            <div className="inline-error" role="alert">
              <AlertCircle size={15} />
              {error}
            </div>
          )}
          <button className="button primary full" disabled={busy}>
            {busy ? <Loader2 size={16} className="spin" /> : null}
            {register ? "创建账号并加入" : "登录"}
            <ArrowUpRight size={16} />
          </button>
        </form>
        <div className="login-switch">
          <span>{register ? "已经有账号？" : "收到团队邀请？"}</span>
          <button
            onClick={() => {
              setRegister((v) => !v);
              setError("");
            }}
          >
            {register ? "直接登录" : "使用邀请加入"}
          </button>
        </div>
      </div>
    </div>
  );
}

type PreviewSession = {
  id: string;
  title: string;
  revision_id: string;
  assets: {
    id: string;
    name?: string;
    mime?: string;
    bytes?: number;
    missing?: boolean;
  }[];
};
export function SyncPreview({
  sessions,
  onClose,
  onDone,
  onError,
}: {
  sessions: Session[];
  onClose: () => void;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const [preview, setPreview] = useState<PreviewSession[]>([]),
    [excluded, setExcluded] = useState<Set<string>>(new Set()),
    [busy, setBusy] = useState(true),
    [error, setError] = useState(""),
    [assetPage, setAssetPage] = useState(0);
  useEffect(() => {
    let live = true;
    post<{ sessions: PreviewSession[] }>("/remote/preview", {
      session_ids: sessions.map((s) => s.id),
    })
      .then((p) => {
        if (live) setPreview(p.sessions);
      })
      .catch((e) => {
        if (live) setError(String(e));
      })
      .finally(() => {
        if (live) setBusy(false);
      });
    return () => {
      live = false;
    };
  }, [sessions]);
  async function sync() {
    setBusy(true);
    try {
      await post("/remote/sync", {
        session_ids: sessions.map((s) => s.id),
        excluded_asset_ids: [...excluded],
      });
      onDone();
      onClose();
    } catch (e) {
      onError(e);
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const assets = preview.flatMap((s) =>
    s.assets.map((a) => ({ ...a, sessionTitle: s.title })),
  );
  return (
    <Modal title="同步到团队服务器" onClose={onClose}>
      <div className="modal-copy">
        团队成员将能够查看这 {sessions.length}{" "}
        条会话。请选择随记录一起共享的图片。
      </div>
      <div className="sync-summary">
        {sessions.slice(0, 10).map((s) => (
          <div key={s.id}>
            <FilePlus2 size={15} />
            <span>{s.title}</span>
          </div>
        ))}
      </div>
      {busy && !preview.length ? (
        <div className="loading-line">
          <Loader2 size={17} className="spin" />
          正在检查同步内容…
        </div>
      ) : (
        <>
          <div className="asset-heading">
            <strong>关联图片</strong>
            <span>
              {assets.length} 张 ·{" "}
              {bytes(
                assets.reduce(
                  (a, b) => a + (excluded.has(b.id) ? 0 : b.bytes || 0),
                  0,
                ),
              )}
            </span>
          </div>
          <div className="asset-list">
            {!assets.length ? (
              <p className="muted">这些会话没有可同步的关联图片。</p>
            ) : (
              assets.slice(assetPage * 30, (assetPage + 1) * 30).map((a) => (
                <label key={a.id} className="asset-row">
                  <input
                    type="checkbox"
                    checked={!a.missing && !excluded.has(a.id)}
                    disabled={a.missing}
                    onChange={(e) =>
                      setExcluded((v) => {
                        const n = new Set(v);
                        if (e.target.checked) n.delete(a.id);
                        else n.add(a.id);
                        return n;
                      })
                    }
                  />
                  <span>
                    <strong>{a.name || a.id}</strong>
                    <small>
                      {a.missing
                        ? "本机图片缺失，将保留占位"
                        : a.mime + " · " + bytes(a.bytes)}
                    </small>
                  </span>
                </label>
              ))
            )}
          </div>
          {assets.length > 30 && (
            <div className="pagination compact">
              <button
                disabled={!assetPage}
                onClick={() => setAssetPage((v) => v - 1)}
              >
                上一页
              </button>
              <span>
                {assetPage + 1}/{Math.ceil(assets.length / 30)}
              </span>
              <button
                disabled={(assetPage + 1) * 30 >= assets.length}
                onClick={() => setAssetPage((v) => v + 1)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
      {error && (
        <div className="inline-error" role="alert">
          {error}
        </div>
      )}
      <footer className="modal-footer">
        <span>上传可以断点续传</span>
        <button
          className="button primary"
          disabled={busy || !!error || !preview.length}
          onClick={() => void sync()}
        >
          确认同步
          <ArrowUpRight size={15} />
        </button>
      </footer>
    </Modal>
  );
}

export function JobsPanel({
  api,
  signal,
  onError,
  onNotice,
  localBatches=false,
}: {
  localBatches?:boolean;
  api: Client;
  signal: number;
  onError: (e: unknown) => void;
  onNotice: (s: string) => void;
}) {
  const [jobs, setJobs] = useState<Job[]>([]),
    [tab, setTab] = useState<"jobs" | "syncs">("jobs"),
    [syncs, setSyncs] = useState<Record<string, unknown>[]>([]),
    [next, setNext] = useState<string | null>(null),
    [history, setHistory] = useState([""]);
  async function load() {
    try {
      if (tab === "jobs") {
        const p = await api.get<Page<Job>>(
          "/jobs" + query({ cursor: history[history.length - 1], limit: 50 }),
        );
        setJobs(p.items);
        setNext(p.next_cursor || null);
      } else {
        const p = await api.get<Page<Record<string, unknown>>>(
          "/syncs" + query({ cursor: history[history.length - 1] }),
        );
        setSyncs(p.items);
        setNext(p.next_cursor || null);
      }
    } catch (e) {
      onError(e);
    }
  }
  useEffect(() => {
    void load();
  }, [api, signal, tab, history]);
  async function action(job: Job, verb: string) {
    try {
      await api.post("/jobs/" + job.id + "/" + verb);
      await load();
    } catch (e) {
      onError(e);
    }
  }
  return (
    <div className="wide-page">
      {localBatches&&<BatchProgress signal={signal} onError={onError}/>}
      <header className="page-heading">
        <div>
          <h1>同步任务</h1>
          <p>查看解析、上传、导出进度与每次同步记录。</p>
        </div>
        <button className="button" onClick={() => void load()}>
          <RefreshCw size={15} />
          刷新
        </button>
      </header>
      <div className="tabs">
        <button
          className={tab === "jobs" ? "selected" : ""}
          onClick={() => {
            setTab("jobs");
            setHistory([""]);
          }}
        >
          处理任务
        </button>
        <button
          className={tab === "syncs" ? "selected" : ""}
          onClick={() => {
            setTab("syncs");
            setHistory([""]);
          }}
        >
          同步历史
        </button>
      </div>
      {tab === "jobs" ? (
        <div className="jobs-list">
          {!jobs.length ? (
            <div className="empty-small">
              <CheckCircle2 size={28} />
              <p>没有待处理任务</p>
              <span>导入或同步后，会在这里显示进度。</span>
            </div>
          ) : (
            jobs.map((j) => (
              <div className="job-row" key={j.id}>
                <div className={"job-icon " + j.state}>
                  {["running", "queued"].includes(j.state) ? (
                    <Loader2
                      size={20}
                      className={j.state === "running" ? "spin" : ""}
                    />
                  ) : j.state === "succeeded" ? (
                    <CheckCircle2 size={20} />
                  ) : j.state === "failed" ? (
                    <AlertCircle size={20} />
                  ) : (
                    <Circle size={20} />
                  )}
                </div>
                <div className="job-detail">
                  <strong>{label(j.kind)}</strong>
                  <span>
                    {displayDate(j.created_at)} ·{" "}
                    {j.session_id?.slice(0, 8) || "后台任务"}
                  </span>
                  {j.state === "running" && (
                    <progress
                      max={j.total || 100}
                      value={j.total ? j.progress : undefined}
                    />
                  )}
                  <small>
                    {label(j.state)}
                    {j.total
                      ? " · " +
                        Math.min(
                          100,
                          Math.round((j.progress / j.total) * 100),
                        ) +
                        "%"
                      : ""}
                  </small>
                  {j.error && <p className="job-error">{j.error}</p>}
                </div>
                <div className="job-actions">
                  {["queued", "running"].includes(j.state) && (
                    <>
                      <button
                        title="暂停"
                        aria-label="暂停任务"
                        onClick={() => void action(j, "pause")}
                      >
                        <Pause size={16} />
                      </button>
                      <button
                        title="取消"
                        aria-label="取消任务"
                        onClick={() => void action(j, "cancel")}
                      >
                        <X size={16} />
                      </button>
                    </>
                  )}
                  {j.state === "paused" && (
                    <button
                      title="继续"
                      aria-label="继续任务"
                      onClick={() => void action(j, "resume")}
                    >
                      <Play size={16} />
                    </button>
                  )}
                  {["failed", "cancelled"].includes(j.state) && (
                    <button
                      title="重试"
                      aria-label="重试任务"
                      onClick={() => void action(j, "retry")}
                    >
                      <RotateCcw size={16} />
                    </button>
                  )}
                  {j.kind === "export" && j.state === "succeeded" && (
                    <button
                      className="button"
                      onClick={() =>
                        void download(
                          api.path("/exports/" + j.id + "/download"),
                          j.download_filename || "cursor-session" + (j.export_format === "pdf" ? ".pdf" : j.export_format === "html" ? ".html" : ".md"),
                        )
                          .then(() => onNotice("导出文件已保存"))
                          .catch(onError)
                      }
                    >
                      <Download size={15} />
                      下载 {j.export_format === 'pdf' ? 'PDF' : j.export_format === 'html' ? 'HTML' : 'Markdown'}
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      ) : (
        <div className="history-table">
          <div className="history-row header">
            <span>成员 / 设备</span>
            <span>会话</span>
            <span>同步时间</span>
            <span>结果</span>
          </div>
          {!syncs.length && <div className="empty-small">尚无同步历史</div>}
          {syncs.map((s, i) => (
            <div className="history-row" key={String(s.id || i)}>
              <span>
                {String(s.owner_name || s.display_name || s.owner_id || "成员")}
                <small>{String(s.device_id || "")}</small>
              </span>
              <span title={String(s.session_id || "")}>
                {String(s.title || s.session_title || s.session_id || "").slice(
                  0,
                  55,
                )}
              </span>
              <span>{displayDate(Number(s.created_at || s.updated_at))}</span>
              <span className={"status " + s.state}>
                {label(String(s.state || s.status || ""))}
                {!!s.error && <small>{String(s.error)}</small>}
              </span>
            </div>
          ))}
        </div>
      )}
      {(next || history.length > 1) && (
        <div className="pagination">
          <button
            disabled={history.length <= 1}
            onClick={() => setHistory((h) => h.slice(0, -1))}
          >
            上一页
          </button>
          <span>第 {history.length} 页</span>
          <button
            disabled={!next}
            onClick={() => {
              if (next) setHistory((h) => [...h, next]);
            }}
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
}

export function AdminPanel({
  api,
  user,
  onError,
  onNotice,
}: {
  api: Client;
  user: User;
  onError: (e: unknown) => void;
  onNotice: (s: string) => void;
}) {
  const [members, setMembers] = useState<User[]>([]),
    [invites, setInvites] = useState<Invite[]>([]),
    [newInvite, setNewInvite] = useState(""),
    [busy, setBusy] = useState(false),
    [deleteTarget, setDeleteTarget] = useState<User | null>(null),
    [deleting, setDeleting] = useState(false);
  async function load() {
    try {
      const [m, i] = await Promise.all([
        api.get<Page<User>>("/members"),
        api.get<Page<Invite>>("/invites"),
      ]);
      setMembers(asPage(m).items);
      setInvites(asPage(i).items);
    } catch (e) {
      onError(e);
    }
  }
  useEffect(() => {
    void load();
  }, [api]);
  async function invite() {
    setBusy(true);
    try {
      const i = await api.post<Invite>("/invites");
      let base=location.origin;
      if(api.path('/invites').startsWith('/remote/api')){const remote=await request<Remote>('/remote/config');base=remote.url||base}
      setNewInvite(i.invite_url || (i.token ? base.replace(/\/$/,'')+'/?invite='+encodeURIComponent(i.token) : ''));
      await load();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  async function update(id: string, data: unknown) {
    try {
      await api.patch("/members/" + id, data);
      await load();
    } catch (e) {
      onError(e);
    }
  }
  async function removeMember() {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    try {
      await api.del('/members/' + deleteTarget.id);
      setDeleteTarget(null);
      onNotice('账号已删除，历史会话和评论已保留');
      await load();
    } catch (error) {
      onError(error);
    } finally {
      setDeleting(false);
    }
  }
  return (
    <div className="wide-page">
      <header className="page-heading">
        <div>
          <h1>成员管理</h1>
          <p>已同步的会话对团队成员可见，修改权限由所有者和管理员管理。</p>
        </div>
        <button
          className="button primary"
          disabled={busy}
          onClick={() => void invite()}
        >
          <UserPlus size={16} />
          邀请成员
        </button>
      </header>
      {newInvite && (
        <div className="invite-created">
          <strong>邀请已创建 · 7 天内有效，仅可使用一次</strong>
          <div>
            <code>{newInvite}</code>
            <button
              className="button"
              onClick={() =>
                void copyText(newInvite)
                  .then(() => onNotice("邀请链接已复制"))
                  .catch(onError)
              }
            >
              <Copy size={14} />
              复制邀请链接
            </button>
          </div>
          <p>同事打开邀请链接即可创建账号，也可以在桌面端粘贴链接加入。</p>
        </div>
      )}
      <div className="members-list">
        {members.map((m) => (
          <div className="member-row" key={m.id}>
            <div className="member-avatar">
              {m.display_name.slice(0, 1).toUpperCase()}
            </div>
            <div className="member-detail">
              <strong>
                {m.display_name}
                {m.id === user.id && <small>你</small>}
              </strong>
              <span>@{m.username}</span>
            </div>
            <select
              aria-label={m.display_name + "的角色"}
              value={m.role}
              disabled={m.id === user.id}
              onChange={(e) => void update(m.id, { role: e.target.value })}
            >
              <option value="member">成员</option>
              <option value="admin">管理员</option>
            </select>
            <span className={"status " + (m.active ? "succeeded" : "paused")}>
              {m.active ? "正常" : "已停用"}
            </span>
            <div className="member-actions">
              <button
                className="button"
                disabled={m.id === user.id}
                onClick={() => void update(m.id, { active: !m.active })}
              >
                {m.active ? "停用" : "启用"}
              </button>
              <button className="button danger" disabled={m.id === user.id}
                title={m.id === user.id ? '不能删除当前登录账号' : '删除此账号，保留历史记录'}
                aria-label={'删除账号 ' + m.username} onClick={() => setDeleteTarget(m)}>
                <Trash2 size={13} />删除
              </button>
            </div>
          </div>
        ))}
      </div>

      <h2 className="section-title">邀请记录</h2>
      {deleteTarget && (
        <Modal title="删除成员账号" onClose={() => { if (!deleting) setDeleteTarget(null); }}>
          <p className="modal-copy">删除 <strong>{deleteTarget.display_name}</strong>（@{deleteTarget.username}）后，该账号将立即退出，无法再登录。</p>
          <p className="modal-copy">登录凭证、个人收藏和关联邀请会被清理。已同步的会话和评论保留，并显示为“已删除成员”。重新注册同名账号不会继承旧记录的所有权。</p>
          <footer className="modal-footer">
            <button className="button" disabled={deleting} onClick={() => setDeleteTarget(null)}>取消</button>
            <button className="button danger" disabled={deleting} onClick={() => void removeMember()}>
              {deleting ? <Loader2 size={14} className="spin" /> : <Trash2 size={14} />}确认删除账号
            </button>
          </footer>
        </Modal>
      )}
      <div className="invite-list">
        {!invites.length ? (
          <p className="muted">暂时没有邀请。</p>
        ) : (
          invites.map((i) => (
            <div key={i.id}>
              <span>{i.id.slice(0, 8)}</span>
              <span>到期 {displayDate(i.expires_at)}</span>
              <span>
                {i.used_at || i.used_by
                  ? "已使用"
                  : i.revoked
                    ? "已撤销"
                    : i.expires_at < Date.now() / 1000
                      ? "已过期"
                      : "待使用"}
              </span>
              {!i.used_at && !i.used_by && !i.revoked && (
                <button
                  className="text-button"
                  onClick={() =>
                    void api
                      .del("/invites/" + i.id)
                      .then(() => load())
                      .catch(onError)
                  }
                >
                  撤销
                </button>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
