import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  Folder,
  FolderPlus,
  PanelLeft,
  Send,
  Settings,
  ArrowUpRight,
  Bookmark,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Cloud,
  FilePlus2,
  FolderOpen,
  Laptop,
  Loader2,
  LogOut,
  Menu,
  Moon,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  SlidersHorizontal,
  Sun,
  Upload,
  Users,
  X,
  Zap,
} from "lucide-react";
import {
  ApiError,
  asPage,
  chooseFiles,
  client,
  initializeTransport,
  isDesktop,
  post,
  query,
  request,
  subscribeActivity,
} from "./api";
import type {
  Capabilities,
  Favorite,
  Page,
  Remote,
  Session,
  User,
} from "./types";
import { displayDate, label, sessionStatus } from "./utils";
import { Reader } from "./Reader";
import { Titlebar, PreviewHome, PaneButton, BootChrome } from "./Workbench";
import { Updates } from "./Updates";
import {
  AdminPanel,
  JobsPanel,
  Login,
  Modal,
  SourcePicker,
  SyncPreview,
} from "./Management";

type Nav = "local" | "team" | "favorites" | "jobs" | "admin";
const initialScope = new URLSearchParams(location.hash.slice(1)).get("scope") === "team" ? "team" : "local";
const localUser: User = {
  id: "local",
  username: "local",
  display_name: "我的电脑",
  role: "admin",
  active: true,
};
export default function App() {
  const [caps, setCaps] = useState<Capabilities | null>(null),
    [me, setMe] = useState<User | null>(null),
    [remoteMe, setRemoteMe] = useState<User | null>(null),
    [nav, setNav] = useState<Nav>(initialScope),
    [searchOpen, setSearchOpen] = useState(false),
    [sideVisible, setSideVisible] = useState(true),
    [dockRight, setDockRight] = useState(localStorage.getItem('csh-dock-right') === 'true'),
    [tabs, setTabs] = useState<Session[]>([]),
    [collapsed, setCollapsed] = useState<Set<string>>(new Set()),
    [scope, setScope] = useState<"local" | "team">(initialScope),
    [bootError, setBootError] = useState(""),
    [ready, setReady] = useState(false),
    [theme, setTheme] = useState(localStorage.getItem("csh-theme") || "light"),
    [sessions, setSessions] = useState<Session[]>([]),
    [selected, setSelected] = useState<Session | null>(null),
    [selection, setSelection] = useState<Set<string>>(new Set()),
    [next, setNext] = useState<string | null>(null),
    [history, setHistory] = useState([""]),
    [search, setSearch] = useState(""),
    [searchQuery, setSearchQuery] = useState(""),
    [owner, setOwner] = useState(""),
    [project, setProject] = useState(""),
    [dateFrom, setDateFrom] = useState(""),
    [dateTo, setDateTo] = useState(""),
    [filterOpen, setFilterOpen] = useState(false),
    [members, setMembers] = useState<User[]>([]),
    [busy, setBusy] = useState(true),
    [listError, setListError] = useState(""),
    [importBusy, setImportBusy] = useState(false),
    [sourceOpen, setSourceOpen] = useState(false),
    [syncSessions, setSyncSessions] = useState<Session[] | null>(null),
    [refreshSignal, setRefreshSignal] = useState(0),
    [jobSignal, setJobSignal] = useState(0),
    [versionSignal, setVersionSignal] = useState(0),
    [hasUpdates, setHasUpdates] = useState(false),
    [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(
      null,
    ),
    [live, setLive] = useState(false),
    [mobileNav, setMobileNav] = useState(false),
    [help, setHelp] = useState(false),
    [preferences, setPreferences] = useState(false),
    [revoke, setRevoke] = useState<Session | null>(null),
    [favorites, setFavorites] = useState<Favorite[]>([]);
  const searchInput = useRef<HTMLInputElement>(null);
  const uploadInput = useRef<HTMLInputElement>(null),
    viewGeneration = useRef(0),
    selectionGeneration = useRef(0),
    initialSession = useRef(new URLSearchParams(location.hash.slice(1)).get("session")),
    noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null),
    selectedRef = useRef<Session | null>(selected);
  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);
  const notify = useCallback((text: string, error = false) => {
    setNotice({ text, error });
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(
      () => setNotice(null),
      error ? 9000 : 5000,
    );
  }, []);
  const onError = useCallback(
    (error: unknown) => {
      notify(error instanceof Error ? error.message : String(error), true);
    },
    [notify],
  );
  const refresh = useCallback(() => {
    setRefreshSignal((v) => v + 1);
    setHasUpdates(false);
  }, []);
  const local = caps?.mode === "local";
  const team = scope === "team" || !local;
  const api = useMemo(() => client(!!local && team), [local, team]);
  const activeUser = local ? (team ? remoteMe : localUser) : me;
  const generation = viewGeneration.current;
  const scopedError = (error: unknown) => {
    if (generation === viewGeneration.current) onError(error);
  };
  const inviteToken = new URLSearchParams(location.search).get("invite") || "";
  useEffect(() => {
    void initializeTransport()
      .then(() => request<Capabilities>("/capabilities"))
      .then(async (c) => {
        setCaps(c);
        if (c.mode === "local") {
          setMe(localUser);
          try {
            const r = await request<Remote>("/remote/me");
            setRemoteMe(r.user || null);
          } catch {}
        } else {
          setNav("team");
          setScope("team");
          try {
            setMe(await request<User>("/auth/me"));
          } catch {
            setMe(null);
          }
        }
        setReady(true);
      })
      .catch((e) => {
        setBootError(String(e));
        setReady(true);
      });
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("csh-theme", theme);
  }, [theme]);
  useEffect(() => {
    const t = setTimeout(() => {
      setSearchQuery(search);
      setHistory([""]);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);
  useEffect(() => {
    if (!caps || !activeUser) return;
    let valid = true;
    const unsubscribe = subscribeActivity(
      (value) => {
        if (!valid || generation !== viewGeneration.current) return;
        const activity = value as {
          kind?: string;
          session_id?: string;
          payload_json?: Record<string, unknown>;
        };
        setJobSignal((v) => v + 1);
        if (
          activity.session_id === selectedRef.current?.id &&
          activity.kind !== "comment"
        )
          setVersionSignal((v) => v + 1);
        if (activity.kind !== "heartbeat") setHasUpdates(true);
      },
      (value) => { if (valid && generation === viewGeneration.current) setLive(value); },
      api.path("/activity"),
    );
    return () => { valid = false; unsubscribe(); };
  }, [caps, activeUser?.id, api, generation]);
  useEffect(() => {
    if (!activeUser || !team) {
      setMembers([]);
      return;
    }
    let valid = true;
    void api
      .get<Page<User>>("/members")
      .then((r) => { if (valid && generation === viewGeneration.current) setMembers(asPage(r).items); })
      .catch((e) => { if (valid) scopedError(e); });
    return () => { valid = false; };
  }, [api, activeUser?.id, team, generation]);
  useEffect(() => {
    if (!caps || !activeUser || !["local", "team", "favorites"].includes(nav))
      return;
    let valid = true;
    setBusy(true);
    setListError("");
    const qs = query({
      cursor: history[history.length - 1],
      limit: 50,
      owner_id: owner,
      project,
      q: searchQuery,
      favorite: nav === "favorites" ? true : undefined,
      date_from: dateFrom
        ? Math.floor(new Date(dateFrom + "T00:00:00").getTime() / 1000)
        : undefined,
      date_to: dateTo
        ? Math.floor(new Date(dateTo + "T23:59:59").getTime() / 1000)
        : undefined,
    });
    api
      .get<Page<Session>>("/sessions" + qs)
      .then((p) => {
        if (valid && generation === viewGeneration.current) {
          setSessions(asPage(p).items);
          setNext(p.next_cursor || null);
        }
      })
      .catch((e) => {
        if (valid && generation === viewGeneration.current) {
          setSessions([]);
          setNext(null);
          if (e instanceof ApiError && e.status === 401) {
            if (local && team) setRemoteMe(null);
            else if (!local) setMe(null);
          } else setListError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (valid && generation === viewGeneration.current) setBusy(false);
      });
    return () => {
      valid = false;
    };
  }, [
    caps,
    activeUser?.id,
    nav,
    api,
    history,
    owner,
    project,
    searchQuery,
    dateFrom,
    dateTo,
    refreshSignal,
  ]);
  useEffect(() => {
    if (nav !== "favorites" || !activeUser) return;
    let valid = true;
    void api
      .get<Page<Favorite>>("/favorites")
      .then((r) => { if (valid && generation === viewGeneration.current) setFavorites(r.items); })
      .catch((e) => { if (valid) scopedError(e); });
    return () => { valid = false; };
  }, [nav, api, activeUser?.id, refreshSignal, generation]);
  useEffect(() => {
    if (!ready || !activeUser) return;
    const id = initialSession.current;
    if (!id) return;
    const ticket = selectionGeneration.current;
    let valid = true;
    void api
        .get<Session>("/sessions/" + encodeURIComponent(id))
        .then((s) => {
          if (valid && generation === viewGeneration.current && ticket === selectionGeneration.current) {
            initialSession.current = null;
            setSelected(s);
            setTabs([s]);
            historyReplace(s.id);
          }
        })
        .catch((e) => {
          if (valid && generation === viewGeneration.current && ticket === selectionGeneration.current) {
            initialSession.current = null;
            historyReplace();
            if (e instanceof ApiError && e.status === 404)
              notify("链接中的会话已不可用，请从当前列表重新选择。", true);
            else onError(e);
          }
        });
    return () => { valid = false; };
  }, [ready, activeUser?.id, api]);
  function navigate(nextNav: Nav) {
    setTabs([]);
    setCollapsed(new Set());
    viewGeneration.current++;
    selectionGeneration.current++;
    initialSession.current = null;
    selectedRef.current = null;
    historyReplace();
    setNotice(null);
    if (["local","team","favorites"].includes(nextNav)) setSessions([]);
    setNext(null);
    setFavorites([]);
    setMembers([]);
    setListError("");
    setBusy(["local","team","favorites"].includes(nextNav));
    setLive(false);
    setRevoke(null);
    setSourceOpen(false);
    setSyncSessions(null);
    if (nextNav === "local" || nextNav === "team") setScope(nextNav);
    setNav(nextNav);
    setSelected(null);
    setSelection(new Set());
    setHistory([""]);
    setOwner("");
    setProject("");
    setSearch("");
    setSearchQuery("");
    setDateFrom("");
    setDateTo("");
    setMobileNav(false);
    setHasUpdates(false);
  }
  function openSession(s: Session) {
    setTabs(old => old.some(t=>t.id===s.id) ? old.map(t=>t.id===s.id?s:t) : [...old,s].slice(-6));
    if (nav === "jobs" || nav === "admin") setNav(team ? "team" : "local");
    setMobileNav(false);
    initialSession.current = null;
    const ticket = ++selectionGeneration.current;
    if (nav === "favorites") {
      void api
        .get<Page<Favorite>>("/favorites" + query({ session_id: s.id }))
        .then((p) => {
          if (ticket !== selectionGeneration.current || generation !== viewGeneration.current) return;
          const f = p.items[0];
          setSelected({
            ...s,
            favorite_revision_id: f?.revision_id,
            favorite_event_id: f?.event_id,
            favorite_round: f?.round_number,
            favorite_seq: f?.event_seq,
          });
        })
        .catch((e) => { if (ticket === selectionGeneration.current) scopedError(e); });
    } else setSelected(s);
    setVersionSignal(0);
    historyReplace(s.id);
    if (!s.current_revision && s.source_id && !team)
      void post("/sources/" + s.source_id + "/index")
        .then(() => notify("已加入解析队列"))
        .catch(onError);
  }
  function historyReplace(id?: string) {
    const nextUrl = new URL(location.href);
    nextUrl.hash = id ? new URLSearchParams({ scope: team ? "team" : "local", session: id }).toString() : "";
    window.history.replaceState(null, "", nextUrl);
  }
  async function importFiles(files: FileList | File[]) {
    setImportBusy(true);
    let n = 0;
    try {
      for (const file of Array.from(files)) {
        if (file.size > 512 * 1024 * 1024)
          throw new Error(file.name + " 超过 512 MiB 限制");
        const form = new FormData();
        form.append("file", file);
        await request("/imports", { method: "POST", body: form });
        n++;
      }
      notify(`已提交 ${n} 份记录，后台正在依次解析`);
      refresh();
      setJobSignal((v) => v + 1);
    } catch (e) {
      onError(e);
      if (n) refresh();
    } finally {
      setImportBusy(false);
      if (uploadInput.current) uploadInput.current.value = "";
    }
  }
  async function chooseImport() {
    if (!isDesktop()) {
      uploadInput.current?.click();
      return;
    }
    setImportBusy(true);
    let n = 0;
    try {
      const paths = await chooseFiles();
      for (const path of paths) {
        await post("/imports/path", { path });
        n++;
      }
      if (n) {
        notify(`已提交 ${n} 份记录，后台正在依次解析`);
        refresh();
      }
    } catch (e) {
      onError(e);
    } finally {
      setImportBusy(false);
    }
  }
  function beginSync(items: Session[]) {
    if (!remoteMe) {
      navigate("team");
      notify("先登录团队服务器，再选择会话同步");
      return;
    }
    if (items.some((s) => !s.current_revision)) {
      notify("请等待所选会话解析完成后再同步", true);
      return;
    }
    setSyncSessions(items);
  }
  async function logout() {
    try {
      if (local) {
        await post("/remote/logout");
        setRemoteMe(null);
      } else {
        await post("/auth/logout");
        setMe(null);
      }
      navigate('team');
      notify("已退出团队账号");
    } catch (e) {
      onError(e);
    }
  }
  const projects = [
    ...new Set(sessions.map((s) => s.project).filter(Boolean)),
  ] as string[];
  const workspaceSessions = new Map<string, Session[]>();
  for (const session of sessions) {
    const key = session.project || '';
    if (!workspaceSessions.has(key)) workspaceSessions.set(key, []);
    workspaceSessions.get(key)!.push(session);
  }
  const visibleSessions = [...workspaceSessions.values()].flat();
  const title =
    nav === "local"
      ? "本地记录"
      : nav === "team"
        ? "团队空间"
        : nav === "favorites"
          ? "我的收藏"
          : nav === "jobs"
            ? "同步任务"
            : "成员管理";
  const selectionItems = sessions.filter((s) => selection.has(s.id));
  function focusSearch(value?: string) {
    if (nav === 'jobs' || nav === 'admin') navigate(team ? 'team' : 'local');
    if (value !== undefined) setSearch(value);
    setSearchOpen(true); setSideVisible(true); if(window.innerWidth <= 760) setMobileNav(true);
    setTimeout(() => searchInput.current?.focus(), 0);
  }
  function toggleSidebar() {
    if (window.innerWidth <= 760) setMobileNav(value => !value);
    else setSideVisible(value => !value);
  }
  function switchDock() {
    setDockRight(value => { localStorage.setItem('csh-dock-right', String(!value)); return !value; });
    setSideVisible(true);
  }
  function closeTab(id: string) {
    const remaining = tabs.filter(tab => tab.id !== id); setTabs(remaining);
    if (selected?.id === id) {
      selectionGeneration.current++; const next = remaining.at(-1) || null;
      setSelected(next); historyReplace(next?.id);
    }
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() === 'k') { event.preventDefault(); focusSearch(); }
      if (event.key.toLowerCase() === 'b') { event.preventDefault(); toggleSidebar(); }
    };
    document.addEventListener('keydown', key); return () => document.removeEventListener('keydown', key);
  }, [nav, team]);
  const indexPanel = (
            <section
              className={"session-index " + (searchOpen ? "search-open " : "") + (filterOpen ? "filters-open" : "")}
              onDragOver={(e) => {
                if (local && !team) e.preventDefault();
              }}
              onDrop={(e) => {
                if (local && !team) {
                  e.preventDefault();
                  if (e.dataTransfer.files.length)
                    void importFiles(e.dataTransfer.files);
                }
              }}
            >
              <header className="index-heading">
                <div>
                  <h1 aria-label={nav==='jobs'||nav==='admin' ? '工作区会话' : title}>Repositories</h1>
                  <p>
                    {nav === "favorites"
                      ? team
                        ? "团队空间中的收藏"
                        : "本地库中的收藏"
                      : team
                        ? "查看成员同步的会话与讨论"
                        : "这台电脑上的 Cursor 会话"}
                  </p>
                </div>
                <button className="icon-button repo-filter" aria-label="筛选工作区列表" title="筛选工作区列表" onClick={()=>setFilterOpen(v=>!v)}><SlidersHorizontal size={16}/></button>
                {local && !team && (
                  <button
                    className="icon-button add-source"
                    aria-label="本机记录"
                    title="从 Cursor 发现记录"
                    onClick={() => setSourceOpen(true)}
                  >
                    <FolderPlus size={18} />
                  </button>
                )}
              </header>
              {local && !team && nav !== "favorites" && (
                <div className="import-actions">
                  <button
                    className="button"
                    disabled={importBusy}
                    onClick={() => void chooseImport()}
                  >
                    {importBusy ? (
                      <Loader2 size={15} className="spin" />
                    ) : (
                      <Upload size={15} />
                    )}
                    导入文件
                  </button>
                  <button
                    className="button"
                    onClick={() => setSourceOpen(true)}
                  >
                    <FolderOpen size={15} />
                    本机记录
                  </button>
                  <input
                    ref={uploadInput}
                    className="sr-only"
                    type="file"
                    multiple
                    accept=".jsonl,.json,.md,.markdown,.html,.htm,.pdf"
                    onChange={(e) => {
                      if (e.target.files) void importFiles(e.target.files);
                    }}
                  />
                </div>
              )}
              <div className="index-filters">
                <div className="search-field">
                  <Search size={16} />
                  <input
                    ref={searchInput}
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="搜索会话与正文"
                    aria-label="搜索会话与正文"
                  />
                  {search && (
                    <button aria-label="清除搜索" onClick={() => setSearch("")}>
                      <X size={14} />
                    </button>
                  )}
                </div>
                <button
                  className={"icon-button " + (filterOpen ? "active" : "")}
                  aria-label="筛选项目与日期"
                  onClick={() => setFilterOpen(!filterOpen)}
                >
                  <SlidersHorizontal size={17} />
                </button>
              </div>
              {team && (
                <div className="owner-filter">
                  <Users size={15} />
                  <select
                    value={owner}
                    aria-label="按同步成员筛选"
                    onChange={(e) => {
                      setOwner(e.target.value);
                      setHistory([""]);
                    }}
                  >
                    <option value="">全部成员</option>
                    {activeUser && (
                      <option value={activeUser.id}>只看我的同步</option>
                    )}
                    {members
                      .filter((m) => m.id !== activeUser?.id)
                      .map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.display_name}
                        </option>
                      ))}
                  </select>
                </div>
              )}
              {filterOpen && (
                <div className="advanced-filters">
                  <label>
                    项目
                    <input
                      list="projects"
                      placeholder="输入项目名称或路径"
                      value={project}
                      onChange={(e) => {
                        setProject(e.target.value);
                        setHistory([""]);
                      }}
                    />
                    <datalist id="projects">
                      {projects.map((p) => (
                        <option key={p} value={p} />
                      ))}
                    </datalist>
                  </label>
                  <div>
                    <label>
                      开始日期
                      <input
                        type="date"
                        value={dateFrom}
                        onChange={(e) => {
                          setDateFrom(e.target.value);
                          setHistory([""]);
                        }}
                      />
                    </label>
                    <label>
                      结束日期
                      <input
                        type="date"
                        value={dateTo}
                        onChange={(e) => {
                          setDateTo(e.target.value);
                          setHistory([""]);
                        }}
                      />
                    </label>
                  </div>
                  <button
                    className="text-button"
                    onClick={() => {
                      setProject("");
                      setDateFrom("");
                      setDateTo("");
                      setOwner("");
                      setHistory([""]);
                    }}
                  >
                    清除筛选
                  </button>
                </div>
              )}
              <div className="list-caption">
                <span>
                  {busy ? "加载中…" : selection.size
                    ? `已选择 ${selection.size} 条`
                    : `${sessions.length} 条记录${next ? " · 本页" : ""}`}
                </span>
                <button
                  title="刷新会话列表"
                  aria-label="刷新会话列表"
                  onClick={refresh}
                >
                  <RefreshCw size={13} className={busy ? "spin" : ""} />
                </button>
              </div>
              {hasUpdates && (
                <button className="list-update" onClick={refresh}>
                  有更新 · 刷新列表
                </button>
              )}
              {selection.size > 0 && (
                <div className="selection-bar">
                  <button onClick={() => setSelection(new Set())}>
                    取消选择
                  </button>
                  {local && !team && (
                    <button
                      disabled={selection.size > 10}
                      onClick={() => beginSync(selectionItems)}
                    >
                      同步到服务器 <ArrowUpRight size={13} />
                    </button>
                  )}
                  <span>{selection.size > 10 ? "单次最多 10 条" : ""}</span>
                </div>
              )}
              <div className="session-list" aria-busy={busy}>
                {busy ? (
                  <div className="loading-line" role="status">
                    <Loader2 size={17} className="spin" />
                    {team ? "正在加载云端记录…" : "正在加载本地记录…"}
                  </div>
                ) : listError ? (
                  <div className="empty-list" role="alert">
                    <h3>记录加载失败</h3><p>{listError}</p>
                    <button className="button" onClick={refresh}>重新加载</button>
                  </div>
                ) : !sessions.length ? (
                  <div className="empty-list">
                    <Archive size={30} />
                    <h3>
                      {searchQuery
                        ? "未找到匹配的会话"
                        : nav === "favorites"
                          ? "还没有收藏"
                          : team
                            ? "还没有团队记录"
                            : "建立你的本地会话库"}
                    </h3>
                    <p>
                      {searchQuery
                        ? "试试其他关键词或清除筛选。"
                        : nav === "favorites"
                          ? "在会话或消息旁点击收藏图标。"
                          : team
                            ? "在桌面端选择会话，同步后在这里查看。"
                            : "导入文件，或从本机 Cursor 发现会话。"}
                    </p>
                    {local && !team && nav !== "favorites" && !searchQuery && (
                      <button
                        className="button primary"
                        onClick={() => void chooseImport()}
                      >
                        <Plus size={16} />
                        导入第一份记录
                      </button>
                    )}
                  </div>
                ) : (
                  visibleSessions.map((s, i) => (<Fragment key={s.id}>
                    {(i === 0 || visibleSessions[i-1].project !== s.project) && <button className="workspace-group repo-heading" title={s.project || '未记录工作区'} aria-expanded={!collapsed.has(s.project || '')} onClick={() => setCollapsed(old => { const next = new Set(old); const key = s.project || ''; if (next.has(key)) next.delete(key); else next.add(key); return next; })}>{collapsed.has(s.project || '') ? <Folder size={17}/> : <FolderOpen size={17}/>}<span>{s.project?.split(/[\\/]/).filter(Boolean).pop() || '未记录工作区'}</span></button>}
                    <div
                      className={
                        "session-row " + (selected?.id === s.id ? "active" : "")
                      }
                      key={s.id}
                      hidden={collapsed.has(s.project || "")}
                    >
                      <label className="session-check">
                        <input
                          aria-label={"选择 " + s.title}
                          type="checkbox"
                          checked={selection.has(s.id)}
                          onChange={(e) =>
                            setSelection((prev) => {
                              const n = new Set(prev);
                              if (e.target.checked) n.add(s.id);
                              else n.delete(s.id);
                              return n;
                            })
                          }
                        />
                      </label>
                      <button
                        className="session-main"
                        onClick={() => openSession(s)}
                      >
                        <span className="session-project">
                          {s.project?.split(/[\\/]/).filter(Boolean).pop() ||
                            "未分类"}
                          {team && (
                            <span>
                              {s.owner_name ||
                                members.find((m) => m.id === s.owner_id)
                                  ?.display_name ||
                                "成员"}
                            </span>
                          )}
                        </span>
                        <strong>{s.title || "未命名会话"}</strong>
                        <span className="session-meta">
                          <span title={displayDate(s.updated_at)}>{Math.max(0, Math.floor((Date.now()/1000 - (s.updated_at || Date.now()/1000))/86400))}d</span>
                          <span>{s.round_count || 0} 轮</span>
                          <span className={"status-dot " + sessionStatus(s)}>
                            {label(sessionStatus(s))}
                          </span>
                          {team && !!s.comment_count && (
                            <span>{s.comment_count} 条评论</span>
                          )}
                        </span>
                      </button>
                      <div className="session-row-actions">
                        {local && !team && (
                          <button
                            title="同步到服务器"
                            aria-label={"同步 " + s.title + " 到服务器"}
                            onClick={() => beginSync([s])}
                          >
                            <Cloud size={14} />
                          </button>
                        )}
                        {nav === "favorites" && (
                          <button
                            title="取消收藏"
                            aria-label={"取消收藏 " + s.title}
                            onClick={() => {
                              void (async () => {
                                let more = true;
                                while (more) {
                                  const p = await api.get<Page<Favorite>>(
                                    "/favorites" + query({ session_id: s.id }),
                                  );
                                  await Promise.all(
                                    p.items.map((f) =>
                                      api.del("/favorites/" + f.id),
                                    ),
                                  );
                                  more = !!p.next_cursor;
                                }
                                refresh();
                              })().catch(onError);
                            }}
                          >
                            <Bookmark size={14} fill="currentColor" />
                          </button>
                        )}
                        {team &&
                          (activeUser?.id === s.owner_id ||
                            activeUser?.role === "admin") && (
                            <button
                              title="撤销共享"
                              aria-label={"撤销共享 " + s.title}
                              onClick={() => setRevoke(s)}
                            >
                              <X size={14} />
                            </button>
                          )}
                      </div>
                    </div></Fragment>
                  ))
                )}
              </div>
              <div className="index-pagination">
                <button
                  disabled={history.length <= 1 || busy}
                  aria-label="上一页会话"
                  onClick={() => setHistory((h) => h.slice(0, -1))}
                >
                  <ChevronLeft size={16} />
                </button>
                <span>第 {history.length} 页</span>
                <button
                  disabled={!next || busy}
                  aria-label="下一页会话"
                  onClick={() => {
                    if (next) setHistory((h) => [...h, next]);
                  }}
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </section>
  );
  if (!ready)
    return (
      <div className="boot-screen">
        <BootChrome/>
        <div className="brand-symbol">H</div>
        <h1>Cursor Session Hub</h1>
        <p>
          <Loader2 className="spin" size={17} />
          正在连接本地服务…
        </p>
      </div>
    );
  if (bootError)
    return (
      <div className="boot-screen">
        <BootChrome/>
        <div className="brand-symbol">H</div>
        <h1>暂时无法连接服务</h1>
        <p className="inline-error">{bootError}</p>
        <button className="button primary" onClick={() => location.reload()}>
          重新连接
        </button>
      </div>
    );
  return (
    <div className={"app cursor-shell " + (selected ? "has-reader " : "") + (dockRight ? "dock-right " : "") + (!sideVisible ? "side-hidden" : "")}>
      <Titlebar toggle={toggleSidebar} dock={dockRight} onDock={switchDock} onImport={local&&!team ? () => void chooseImport() : undefined} onSearch={() => focusSearch()} onHelp={() => setHelp(true)} onTheme={() => setTheme(value => value === 'light' ? 'dark' : 'light')}/>
      <aside className={"sidebar " + (mobileNav ? "mobile-open" : "")}>
        <div className="side-top"><button className="icon-button" aria-label="收起工作区侧栏" onClick={toggleSidebar}><PanelLeft size={17}/></button><span/><button className="icon-button" aria-label="返回预览首页" onClick={() => {selectionGeneration.current++;initialSession.current=null;setSelected(null);historyReplace();}}><ChevronLeft size={18}/></button><button className="icon-button" aria-label="打开最近会话" disabled={!tabs.length} onClick={() => {const last=tabs.at(-1);if(last)openSession(last);}}><ChevronRight size={18}/></button></div>
        <nav aria-label="主导航">
          <button className={!selected && ['local','team'].includes(nav) ? 'selected' : ''} onClick={() => navigate(team ? 'team' : 'local')}><Send size={18}/><span>New Preview</span></button>
          <button onClick={() => focusSearch()}><Search size={18}/><span>Search</span></button>
          <button className={nav==='jobs'?'selected':''} aria-label="同步任务" onClick={() => navigate('jobs')}><RefreshCw size={18}/><span>Sync tasks</span>{hasUpdates&&<i className="nav-dot"/>}</button>
          <button aria-label="自定义工作台" onClick={() => setPreferences(true)}><SlidersHorizontal size={18}/><span>Customize</span></button>
        </nav>
        <div className="sidebar-library">{activeUser && indexPanel}</div>
        <div className="sidebar-account">
          <div className="scope-switch" aria-label="记录位置">{local&&<button className={!team?'active':''} aria-label="本地记录" onClick={() => navigate('local')}><Laptop size={15}/>This PC</button>}<button className={team?'active':''} aria-label="团队空间" onClick={() => navigate('team')}><Users size={15}/>Team</button><button title="我的收藏" aria-label="我的收藏" onClick={() => navigate('favorites')}><Bookmark size={15}/></button></div>
          <div className="account-row"><span className="account-avatar">{activeUser?.display_name?.slice(0,1)||'H'}</span><span>{team ? activeUser?.display_name || '登录团队空间' : 'Local workspace'}<small>{team ? 'Team server' : 'Session Hub'}</small></span><button className="icon-button" aria-label={team&&activeUser?.role==='admin'?'成员管理':'切换深浅色主题'} onClick={() => team&&activeUser?.role==='admin' ? navigate('admin') : setTheme(v=>v==='light'?'dark':'light')}><Settings size={17}/></button>{team&&activeUser&&<button className="icon-button" aria-label="退出团队账号" title="退出登录" onClick={()=>void logout()}><LogOut size={15}/></button>}</div>
          <div className="sidebar-footer"><span className="connection-state"><i/>{team?'团队服务器':'离线本地库'}</span><Updates local={!!local}/></div>
        </div>
      </aside>
      {mobileNav&&<button className="sidebar-scrim" aria-label="关闭侧栏" onClick={()=>setMobileNav(false)}/>}
      <main className="workspace">
        <div className="workbench-toolbar"><button className="icon-button" aria-label="展开主菜单" onClick={toggleSidebar}><PanelLeft size={17}/></button><span className="workbench-location">{team ? 'Team' : 'This PC'}</span><span className="toolbar-space"/><button className="focus-toggle" onClick={toggleSidebar}>Preview <ArrowUpRight size={13}/></button><button className="icon-button" aria-label="工作台说明" onClick={()=>setHelp(true)}><MoreHorizontal size={18}/></button><PaneButton right={dockRight} onClick={switchDock}/></div>
        {team && !activeUser ? (
          <Login
            local={!!local}
            initialToken={inviteToken}
            onSuccess={(u) => {
              if (local) setRemoteMe(u);
              else setMe(u);
              refresh();
            }}
            onError={onError}
          />
        ) : nav === "jobs" ? (
          <JobsPanel
            api={api}
            signal={jobSignal}
            onError={onError}
            onNotice={notify}
          />
        ) : nav === "admin" && activeUser?.role === 'admin' ? (
          <AdminPanel
            api={api}
            user={activeUser}
            onError={onError}
            onNotice={notify}
          />
        ) : (
          <div className="session-workspace">
            {tabs.length>0&&<div className="preview-tabs" role="tablist" aria-label="已打开会话">{tabs.map(tab=><div className={"preview-tab "+(selected?.id===tab.id?'active':'')} key={tab.id}><button role="tab" aria-selected={selected?.id===tab.id} onClick={()=>openSession(tab)} title={tab.title}><FilePlus2 size={13}/><span>{tab.title}</span></button><button aria-label={'关闭标签 '+tab.title} onClick={()=>closeTab(tab.id)}><X size={13}/></button></div>)}</div>}


            {selected && activeUser ? (
              <Reader
                key={
                  scope +
                  selected.id +
                  (selected.favorite_revision_id || "") +
                  (selected.favorite_event_id || "")
                }
                session={selected}
                api={api}
                user={activeUser}
                onClose={() => {
                  selectionGeneration.current++;
                  initialSession.current = null;
                  setSelected(null);
                  historyReplace();
                }}
                onError={scopedError}
                onNotice={notify}
                onChange={refresh}
                versionSignal={versionSignal}
              />
            ) : (
              <PreviewHome projects={projects} project={project} onProject={value=>{setProject(value);setHistory(['']);}} team={team} local={!!local} onScope={navigate} onSearch={focusSearch} onImport={()=>void chooseImport()} onSources={()=>setSourceOpen(true)} onFavorites={()=>navigate('favorites')} onJobs={()=>navigate('jobs')}/>

            )}
          </div>
        )}
      </main>
      {sourceOpen && (
        <SourcePicker
          signal={jobSignal}
          onClose={() => setSourceOpen(false)}
          onIndexed={() => {
            refresh();
            setJobSignal((v) => v + 1);
            notify("任务已提交，解析完成后刷新列表");
          }}
          onError={onError}
        />
      )}{" "}
      {syncSessions && (
        <SyncPreview
          sessions={syncSessions}
          onClose={() => setSyncSessions(null)}
          onDone={() => {
            notify("已加入同步队列");
            refresh();
            setJobSignal((v) => v + 1);
            setSelection(new Set());
          }}
          onError={onError}
        />
      )}{" "}
      {revoke && (
        <Modal title="撤销共享" onClose={() => setRevoke(null)}>
          <div className="modal-copy">
            撤销后，团队成员将无法访问「{revoke.title}
            」及其图片和导出。本机原始记录保留。
          </div>
          <footer className="modal-footer">
            <button className="button" onClick={() => setRevoke(null)}>
              取消
            </button>
            <button
              className="button danger"
              onClick={() =>
                void api
                  .del("/sessions/" + revoke.id)
                  .then(() => {
                    if (selected?.id === revoke.id) setSelected(null);
                    setRevoke(null);
                    refresh();
                    notify("已撤销共享");
                  })
                  .catch(onError)
              }
            >
              撤销共享
            </button>
          </footer>
        </Modal>
      )}
      {preferences && <Modal title="Customize" onClose={()=>setPreferences(false)}><div className="help-content"><h3>外观</h3><p>选择与 Cursor 工作台一致的浅色或深色外观。</p><div className="preferences-options"><button className="button" aria-pressed={theme==='light'} onClick={()=>setTheme('light')}><Sun size={15}/>浅色</button><button className="button" aria-pressed={theme==='dark'} onClick={()=>setTheme('dark')}><Moon size={15}/>深色</button></div><h3>工作区侧栏</h3><p>工作区与会话列表可以停靠在左侧或右侧。</p><button className="button" onClick={switchDock}>{dockRight?'移到左侧':'移到右侧'}</button><h3>快捷键</h3><p>Ctrl / ⌘ K 搜索会话 · Ctrl / ⌘ B 收起或显示侧栏。</p></div></Modal>}
      {help && (
        <Modal title="使用 Session Hub" onClose={() => setHelp(false)}>
          <div className="help-content">
            <h3>本地阅读</h3>
            <p>
              导入文件，或从 Cursor
              发现记录并同步到本地库。解析在后台排队进行，无需登录。
            </p>
            <h3>团队协作</h3>
            <p>
              填写服务器地址并登录。回到本地记录，选择会话、预览图片，再手动同步。已上传的会话对团队成员可见。
            </p>
            <h3>长会话</h3>
            <p>
              默认打开最近 3
              轮，更早对话在目录中按需读取。工具详情与大段正文单独加载。
            </p>
            <h3>数据与权限</h3>
            <p>
              本地模式和团队空间各自保存收藏、评论与历史。同步不会上传整个
              Cursor 数据库或项目目录。
            </p>
          </div>
        </Modal>
      )}
      {notice && (
        <div
          className={"toast " + (notice.error ? "error" : "")}
          role={notice.error ? "alert" : "status"}
        >
          {notice.error ? <CircleHelp size={17} /> : <Check size={17} />}
          <span>{notice.text}</span>
          <button aria-label="关闭提示" onClick={() => setNotice(null)}>
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}
