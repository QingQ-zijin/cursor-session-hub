import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
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
import {
  AdminPanel,
  JobsPanel,
  Login,
  Modal,
  SourcePicker,
  SyncPreview,
} from "./Management";

type Nav = "local" | "team" | "favorites" | "jobs" | "admin";
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
    [nav, setNav] = useState<Nav>("local"),
    [scope, setScope] = useState<"local" | "team">("local"),
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
    [busy, setBusy] = useState(false),
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
    [revoke, setRevoke] = useState<Session | null>(null),
    [favorites, setFavorites] = useState<Favorite[]>([]);
  const uploadInput = useRef<HTMLInputElement>(null),
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
    return subscribeActivity(
      (value) => {
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
      setLive,
      api.path("/activity"),
    );
  }, [caps, activeUser?.id, api]);
  useEffect(() => {
    if (!activeUser || !team) {
      setMembers([]);
      return;
    }
    void api
      .get<Page<User>>("/members")
      .then((r) => setMembers(asPage(r).items))
      .catch(onError);
  }, [api, activeUser?.id, team]);
  useEffect(() => {
    if (!caps || !activeUser || !["local", "team", "favorites"].includes(nav))
      return;
    let valid = true;
    setBusy(true);
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
        if (valid) {
          setSessions(asPage(p).items);
          setNext(p.next_cursor || null);
        }
      })
      .catch((e) => {
        if (valid) {
          if (e instanceof ApiError && e.status === 401) {
            if (local && team) setRemoteMe(null);
            else if (!local) setMe(null);
          } else onError(e);
        }
      })
      .finally(() => {
        if (valid) setBusy(false);
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
    void api
      .get<Page<Favorite>>("/favorites")
      .then((r) => setFavorites(r.items))
      .catch(onError);
  }, [nav, api, activeUser?.id, refreshSignal]);
  useEffect(() => {
    if (!ready || !activeUser) return;
    const id = new URLSearchParams(location.hash.slice(1)).get("session");
    if (id)
      void api
        .get<Session>("/sessions/" + encodeURIComponent(id))
        .then(setSelected)
        .catch(onError);
  }, [ready, activeUser?.id]);
  function navigate(nextNav: Nav) {
    if (nextNav === "local" || nextNav === "team") setScope(nextNav);
    setNav(nextNav);
    setSelected(null);
    setSelection(new Set());
    setHistory([""]);
    setOwner("");
    setProject("");
    setSearch("");
    setMobileNav(false);
    setHasUpdates(false);
  }
  function openSession(s: Session) {
    if (nav === "favorites") {
      void api
        .get<Page<Favorite>>("/favorites" + query({ session_id: s.id }))
        .then((p) => {
          const f = p.items[0];
          setSelected({
            ...s,
            favorite_revision_id: f?.revision_id,
            favorite_event_id: f?.event_id,
            favorite_round: f?.round_number,
            favorite_seq: f?.event_seq,
          });
        })
        .catch(onError);
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
    nextUrl.hash = id ? "session=" + id : "";
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
      setSelected(null);
      notify("已退出团队账号");
    } catch (e) {
      onError(e);
    }
  }
  const projects = [
    ...new Set(sessions.map((s) => s.project).filter(Boolean)),
  ] as string[];
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
  if (!ready)
    return (
      <div className="boot-screen">
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
        <div className="brand-symbol">H</div>
        <h1>暂时无法连接服务</h1>
        <p className="inline-error">{bootError}</p>
        <button className="button primary" onClick={() => location.reload()}>
          重新连接
        </button>
      </div>
    );
  return (
    <div className={"app " + (selected ? "has-reader" : "")}>
      <aside className={"sidebar " + (mobileNav ? "mobile-open" : "")}>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate(local ? "local" : "team");
          }}
        >
          <div className="brand-symbol">H</div>
          <span>
            Session Hub<small>CURSOR WORKSPACE</small>
          </span>
        </a>
        <div className="workspace-label">工作空间</div>
        <nav aria-label="主导航">
          {local && (
            <button
              className={nav === "local" ? "selected" : ""}
              onClick={() => navigate("local")}
            >
              <Laptop size={17} />
              <span>本地记录</span>
            </button>
          )}
          <button
            className={nav === "team" ? "selected" : ""}
            onClick={() => navigate("team")}
          >
            <Users size={17} />
            <span>团队空间</span>
            {remoteMe && local && <i className="connected-dot" />}
          </button>
          <button
            className={nav === "favorites" ? "selected" : ""}
            onClick={() => navigate("favorites")}
          >
            <Bookmark size={17} />
            <span>我的收藏</span>
          </button>
          <button
            className={nav === "jobs" ? "selected" : ""}
            onClick={() => navigate("jobs")}
          >
            <RefreshCw size={17} />
            <span>同步任务</span>
            {hasUpdates && <i className="nav-dot" />}
          </button>
          {activeUser?.role === "admin" && team && (
            <button
              className={nav === "admin" ? "selected" : ""}
              onClick={() => navigate("admin")}
            >
              <Settings2 size={17} />
              <span>成员管理</span>
            </button>
          )}
        </nav>
        <div className="sidebar-space" />
        <div className="sidebar-context">
          <span className={"connection-state " + (live ? "live" : "")}>
            <i />
            {team ? "团队服务器" : "离线本地库"}
          </span>
          <small>
            {team
              ? remoteMe?.display_name || me?.display_name || "等待登录"
              : "记录保存在这台电脑"}
          </small>
        </div>
        <div className="sidebar-footer">
          <button
            aria-label="切换深浅色主题"
            title={theme === "light" ? "切换深色模式" : "切换浅色模式"}
            onClick={() => setTheme((v) => (v === "light" ? "dark" : "light"))}
          >
            {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
          </button>
          <button
            aria-label="使用说明"
            title="使用说明"
            onClick={() => setHelp(true)}
          >
            <CircleHelp size={17} />
          </button>
          {team && activeUser && (
            <button
              aria-label="退出团队账号"
              title="退出登录"
              onClick={() => void logout()}
            >
              <LogOut size={17} />
            </button>
          )}
          <span>内测版 0.1</span>
        </div>
      </aside>
      <main className="workspace">
        <div className="mobile-top">
          <button
            className="icon-button"
            aria-label="展开主菜单"
            onClick={() => setMobileNav(!mobileNav)}
          >
            <Menu size={20} />
          </button>
          <strong>Session Hub</strong>
          <span>{title}</span>
        </div>
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
        ) : nav === "admin" && activeUser ? (
          <AdminPanel
            api={api}
            user={activeUser}
            onError={onError}
            onNotice={notify}
          />
        ) : (
          <div className="session-workspace">
            <section
              className="session-index"
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
                  <h1>{title}</h1>
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
                {local && !team && (
                  <button
                    className="icon-button add-source"
                    aria-label="从 Cursor 发现记录"
                    title="从 Cursor 发现记录"
                    onClick={() => setSourceOpen(true)}
                  >
                    <Plus size={20} />
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
                    导入 JSONL
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
                    accept=".jsonl,.json,.txt"
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
                  {selection.size
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
              <div className="session-list">
                {busy && !sessions.length ? (
                  <div className="loading-line">
                    <Loader2 size={17} className="spin" />
                    读取会话索引
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
                            : "导入 JSONL，或从本机 Cursor 发现会话。"}
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
                  sessions.map((s) => (
                    <div
                      className={
                        "session-row " + (selected?.id === s.id ? "active" : "")
                      }
                      key={s.id}
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
                          <span>{displayDate(s.updated_at)}</span>
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
                    </div>
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
                  setSelected(null);
                  historyReplace();
                }}
                onError={onError}
                onNotice={notify}
                onChange={refresh}
                versionSignal={versionSignal}
              />
            ) : (
              <section className="reader-empty">
                <div className="empty-symbol">
                  <FilePlus2 size={32} strokeWidth={1.2} />
                </div>
                <h2>选择会话开始阅读</h2>
                <p>选择左侧会话，查看思路、结论与工具记录。</p>
                <div className="empty-shortcuts">
                  <span>
                    <span>01</span>选择一段会话
                  </span>
                  <span>
                    <span>02</span>按需展开历史
                  </span>
                  <span>
                    <span>03</span>
                    {team ? "与同事讨论" : "同步给团队"}
                  </span>
                </div>
                <div className="empty-caption">
                  <Zap size={13} />
                  长会话分轮加载，阅读更轻盈
                </div>
              </section>
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
      {help && (
        <Modal title="使用 Session Hub" onClose={() => setHelp(false)}>
          <div className="help-content">
            <h3>本地阅读</h3>
            <p>
              导入 JSONL，或从 Cursor
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
