import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUp,
  MessageSquare,
  Bookmark,
  Download,
  MoreHorizontal,
  X,
  Copy,
  Check,
  Terminal,
  AlertCircle,
  FileText,
  Image as ImageIcon,
  Loader2,
} from "lucide-react";
import { asPage, download, fetchAsset, query } from "./api";
import type { Client } from "./api";
import type {
  Comment,
  EventRecord,
  Favorite,
  Page,
  Revision,
  Round,
  Session,
  User,
} from "./types";
import {
  boundedExpanded,
  displayDate,
  roundPreview,
  safeLink,
  stringify,
} from "./utils";

function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { strict: false, trust: false }]]}
        components={{
          a: ({ href, children }) => (
            <a href={safeLink(href)} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          img: () => (
            <span className="inline-note">
              <ImageIcon size={13} /> 远程图片未自动加载
            </span>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
function ErrorText({ error }: { error: string }) {
  return error ? (
    <div className="inline-error" role="alert">
      <AlertCircle size={15} />
      {error}
    </div>
  ) : null;
}

function Content({
  value,
  api,
  markdown = false,
  contentId,
}: {
  value: unknown;
  api: Client;
  markdown?: boolean;
  contentId?: string;
}) {
  const obj =
    value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : null;
  const id = contentId || (obj?.content_id as string | undefined);
  const initial =
    typeof value === "string"
      ? value
      : obj?.preview !== undefined
        ? stringify(obj.preview)
        : stringify(value);
  const [pages, setPages] = useState<string[]>([]),
    [contentHistory, setContentHistory] = useState<string[]>([]),
    [cursor, setCursor] = useState<string | null>(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [slice, setSlice] = useState(0);
  const text = pages.length ? pages[pages.length - 1] : initial;
  const view = text.slice(slice * 24000, (slice + 1) * 24000);
  async function more(previous = false) {
    if (!id) return;
    setBusy(true);
    const readCursor = previous
      ? contentHistory[contentHistory.length - 2]
      : cursor || "";
    try {
      const r = await api.get<{ text: string; next_cursor: string | null }>(
        "/contents/" +
          encodeURIComponent(id) +
          query({ cursor: readCursor, limit: 262144 }),
      );
      setPages([r.text]);
      setCursor(r.next_cursor);
      setContentHistory((old) =>
        previous ? old.slice(0, -1) : [...old, readCursor],
      );
      setSlice(0);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <ErrorText error={error} />
      {markdown ? (
        <Markdown text={view} />
      ) : (
        <pre className="payload">
          <code>{view || "（无内容）"}</code>
        </pre>
      )}
      {text.length > 24000 && (
        <div className="pagination compact">
          <button disabled={!slice} onClick={() => setSlice((v) => v - 1)}>
            <ChevronLeft size={14} />
            上一段
          </button>
          <span>
            {slice + 1} / {Math.ceil(text.length / 24000)}
          </span>
          <button
            disabled={(slice + 1) * 24000 >= text.length}
            onClick={() => setSlice((v) => v + 1)}
          >
            下一段
            <ChevronRight size={14} />
          </button>
        </div>
      )}
      {id && cursor !== null && (
        <button
          className="text-button content-more"
          disabled={busy}
          onClick={() => void more()}
        >
          {busy ? (
            <Loader2 size={14} className="spin" />
          ) : (
            <FileText size={14} />
          )}{" "}
          {pages.length ? "继续读取下一部分" : "加载完整内容（分段）"}
        </button>
      )}
      {contentHistory.length > 1 && (
        <button
          className="text-button content-more"
          disabled={busy}
          onClick={() => void more(true)}
        >
          <ChevronLeft size={14} />
          读取上一部分
        </button>
      )}
    </>
  );
}

function StoredImage({
  block,
  api,
}: {
  block: Record<string, unknown>;
  api: Client;
}) {
  const [src, setSrc] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    let live = true,
      url = "";
    const id = block.asset_id || block.id;
    const inline = block.data_uri;
    if (
      typeof inline === "string" &&
      /^data:image\/(png|jpeg|gif|webp);base64,/i.test(inline)
    ) {
      setSrc(inline);
      return;
    }
    if (!id) {
      setError(String(block.missing_reason || "原记录未包含可读取的图片"));
      return;
    }
    fetchAsset(api.path("/assets/" + encodeURIComponent(String(id))))
      .then((value) => {
        url = value;
        if (live) setSrc(value);
        else if (value.startsWith("blob:")) URL.revokeObjectURL(value);
      })
      .catch((e) => {
        if (live) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
      if (url.startsWith("blob:")) URL.revokeObjectURL(url);
    };
  }, [block, api]);
  return error ? (
    <div className="missing-image">
      <ImageIcon size={18} />
      {error}
      {block.asset_id || block.id ? (
        <button
          className="text-button"
          onClick={() =>
            void download(
              api.path(
                "/assets/" +
                  encodeURIComponent(String(block.asset_id || block.id)),
              ),
              String(block.name || "session-image.png"),
            ).catch((e) => setError(String(e)))
          }
        >
          下载图片
        </button>
      ) : null}
    </div>
  ) : src ? (
    <img
      className="record-image"
      src={src}
      loading="lazy"
      alt={String(block.alt || "会话附件")}
    />
  ) : (
    <span className="inline-note">正在读取图片…</span>
  );
}

function Tool({ block, api }: { block: Record<string, unknown>; api: Client }) {
  const [open, setOpen] = useState(false);
  const result = block.result as Record<string, unknown> | string | undefined;
  return (
    <div className={"tool-record " + (open ? "is-open" : "")}>
      <button
        className="tool-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Terminal size={15} />
        <strong>{String(block.name || "工具调用")}</strong>
        <span>{String(block.summary || "输入与执行结果")}</span>
        <ChevronRight size={15} />
      </button>
      {open && (
        <div className="tool-body">
          <div className="eyebrow">输入</div>
          <Content
            value={block.input}
            contentId={block.input_content_id as string}
            api={api}
          />
          <div className="eyebrow">执行结果</div>
          {result != null ? (
            <>
              <Content
                value={
                  typeof result === "object" && result.text !== undefined
                    ? result.text
                    : result
                }
                contentId={
                  typeof result === "object"
                    ? (result.content_id as string | undefined)
                    : (block.result_content_id as string)
                }
                api={api}
              />
              {typeof result === "object" &&
                Array.isArray(result.images) &&
                result.images.map((im, i) => (
                  <StoredImage
                    key={i}
                    block={
                      typeof im === "object"
                        ? (im as Record<string, unknown>)
                        : {}
                    }
                    api={api}
                  />
                ))}
            </>
          ) : (
            <p className="muted">原记录未保存工具执行结果。</p>
          )}
        </div>
      )}
    </div>
  );
}

function Collapsed({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="disclosure">
      <button onClick={() => setOpen(!open)} aria-expanded={open}>
        <ChevronRight size={14} className={open ? "rotate" : ""} />
        {label}
      </button>
      {open && <div className="disclosure-content">{children}</div>}
    </div>
  );
}
function Block({
  block,
  api,
}: {
  block: Record<string, unknown>;
  api: Client;
}) {
  switch (block.type) {
    case "text":
      return (
        <Content
          value={block.text}
          contentId={block.content_id as string}
          api={api}
          markdown
        />
      );
    case "thinking":
      return (
        <Collapsed label="已保存的思考记录">
          <Content
            value={block.text}
            contentId={block.content_id as string}
            api={api}
            markdown
          />
        </Collapsed>
      );
    case "tool_use":
      return <Tool block={block} api={api} />;
    case "image":
      return (
        <Collapsed label="查看会话图片">
          <StoredImage block={block} api={api} />
        </Collapsed>
      );
    default:
      return (
        <Collapsed label="其他内容">
          <Content value={block} api={api} />
        </Collapsed>
      );
  }
}
function EventBody({
  event,
  api,
}: {
  event: Record<string, unknown>;
  api: Client;
}) {
  const [blockPage, setBlockPage] = useState(0);
  const blocks = event.blocks as Record<string, unknown>[] | undefined;
  if (blocks?.length)
    return (
      <>
        {blocks.slice(blockPage * 20, (blockPage + 1) * 20).map((b, i) => (
          <Block key={blockPage * 20 + i} block={b} api={api} />
        ))}
        {blocks.length > 20 && (
          <div className="pagination compact">
            <button
              disabled={!blockPage}
              onClick={() => setBlockPage((p) => p - 1)}
            >
              上一组内容
            </button>
            <span>
              {blockPage + 1}/{Math.ceil(blocks.length / 20)}
            </span>
            <button
              disabled={(blockPage + 1) * 20 >= blocks.length}
              onClick={() => setBlockPage((p) => p + 1)}
            >
              下一组内容
            </button>
          </div>
        )}
      </>
    );
  const kind = String(event.kind);
  if (kind === "tool") return <Tool block={event} api={api} />;
  if (kind === "user" || kind === "assistant" || kind === "notice")
    return (
      <Content
        value={event.text}
        contentId={event.content_id as string}
        api={api}
        markdown
      />
    );
  if (kind === "reasoning")
    return (
      <Collapsed label="已保存的思考记录">
        <Content
          value={event.text}
          contentId={event.content_id as string}
          api={api}
          markdown
        />
      </Collapsed>
    );
  const titles: Record<string, string> = {
    web_search: "网页搜索",
    web_call: "网页操作",
    instructions: "系统指令",
    system: "系统记录",
    attachment: "附件",
    guardian_request: "审批请求",
    guardian_decision: "审批结果",
    status: "状态",
    context: "上下文",
    tokens: "Token 用量",
    raw: "原始记录 / 解析说明",
    branch: "其他分支",
  };
  return (
    <Collapsed label={String(event.label || titles[kind] || "其他记录")}>
      <Content
        value={event.text ?? event.query ?? event.payload ?? event}
        contentId={(event.content_id || event._full_content_id) as string}
        api={api}
      />
    </Collapsed>
  );
}

function RoundEvents({
  session,
  revision,
  round,
  api,
  onComment,
  onFavorite,
}: {
  session: Session;
  revision: string;
  round: Round;
  api: Client;
  onComment: (event: EventRecord) => void;
  onFavorite: (event: EventRecord) => void;
}) {
  const [items, setItems] = useState<EventRecord[]>([]),
    [next, setNext] = useState<string | null>(null),
    [history, setHistory] = useState<string[]>(
      session.favorite_seq !== undefined &&
        session.favorite_round === round.number
        ? [String(Math.max(0, session.favorite_seq - 1))]
        : [""],
    ),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .get<Page<EventRecord>>(
        "/sessions/" +
          session.id +
          "/events" +
          query({
            revision,
            round: round.number,
            cursor: history[history.length - 1],
            limit: 40,
          }),
      )
      .then((r) => {
        if (live) {
          setItems(r.items);
          setNext(r.next_cursor || null);
          setError("");
          if (session.favorite_event_id)
            requestAnimationFrame(() =>
              document
                .getElementById("event-" + session.favorite_event_id)
                ?.scrollIntoView({ block: "start" }),
            );
        }
      })
      .catch((e) => {
        if (live) setError(String(e));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [session.id, revision, round.number, api, history]);
  return (
    <div className="round-content">
      <ErrorText error={error} />
      {history[0] !== "" && (
        <button className="text-button" onClick={() => setHistory([""])}>
          从本轮开头阅读
        </button>
      )}
      {loading ? (
        <div className="loading-line">
          <Loader2 size={16} className="spin" />
          正在加载本轮内容
        </div>
      ) : (
        items.map((item) => (
          <article
            key={item.id}
            id={"event-" + item.id}
            className={"event event-" + String(item.event.kind)}
          >
            <header className="event-header">
              <span
                className={
                  "avatar " + (item.event.kind === "user" ? "person" : "agent")
                }
              >
                {item.event.kind === "user" ? "U" : "✦"}
              </span>
              <strong>
                {item.event.kind === "user"
                  ? "用户"
                  : item.event.kind === "assistant"
                    ? "Cursor"
                    : item.event.kind === "tool"
                      ? "工具"
                      : "记录"}
              </strong>
              <span className="event-seq">#{item.seq}</span>
              <div className="event-actions">
                <button
                  aria-label="评论这条消息"
                  title="评论"
                  onClick={() => onComment(item)}
                >
                  <MessageSquare size={14} />
                </button>
                <button
                  aria-label="收藏这条消息"
                  title="收藏"
                  onClick={() => onFavorite(item)}
                >
                  <Bookmark size={14} />
                </button>
              </div>
            </header>
            <div className="event-content">
              <EventBody event={item.event} api={api} />
            </div>
          </article>
        ))
      )}
      {(next || history.length > 1) && (
        <div className="pagination">
          <button
            disabled={loading || history.length <= 1}
            onClick={() => setHistory((h) => h.slice(0, -1))}
          >
            <ChevronLeft size={14} />
            上一页内容
          </button>
          <span>第 {history.length} 页 · 每次最多 40 条</span>
          <button
            disabled={loading || !next}
            onClick={() => {
              if (next) setHistory((h) => [...h, next]);
            }}
          >
            继续加载本轮内容
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

function Comments({
  session,
  revision,
  event,
  api,
  user,
  onClose,
  onError,
}: {
  session: Session;
  revision: string;
  event: EventRecord | null;
  api: Client;
  user: User;
  onClose: () => void;
  onError: (e: unknown) => void;
}) {
  const [items, setItems] = useState<Comment[]>([]),
    [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [editing, setEditing] = useState<string | null>(null),
    [next, setNext] = useState<string | null>(null);
  async function load(cursor?: string) {
    try {
      const p = await api.get<Page<Comment>>(
        "/sessions/" +
          session.id +
          "/comments" +
          query({ revision, event_id: event?.id, cursor }),
      );
      setItems((v) => (cursor ? [...v, ...p.items] : p.items));
      setNext(p.next_cursor || null);
    } catch (e) {
      onError(e);
    }
  }
  useEffect(() => {
    setText("");
    setEditing(null);
    void load();
  }, [session.id, revision, event?.id, api]);
  async function save() {
    if (!text.trim()) return;
    setBusy(true);
    try {
      if (editing) await api.patch("/comments/" + editing, { text });
      else
        await api.post("/sessions/" + session.id + "/comments", {
          revision_id: revision,
          event_id: event?.id || null,
          text,
        });
      setText("");
      setEditing(null);
      await load();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <aside className="inspector">
      <div className="inspector-title">
        <h3>
          讨论<span>{items.length}</span>
        </h3>
        <button className="icon-button" aria-label="关闭讨论" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      <div className="comment-anchor">
        {event ? "消息 #" + event.seq : "整个会话"}
        <span>固定在当前版本</span>
      </div>
      <div className="comment-list">
        {!items.length && (
          <div className="empty-small">
            <MessageSquare size={23} />
            <p>暂时没有评论</p>
            <span>补充结论，或留下交接说明。</span>
          </div>
        )}
        {items.map((c) => (
          <div className="comment" key={c.id}>
            <div>
              <strong>
                {c.author_name || c.owner_name || c.display_name || "成员"}
              </strong>
              <time>{displayDate(c.created_at)}</time>
            </div>
            <Markdown text={c.text} />
            {(c.user_id === user.id ||
              c.owner_id === user.id ||
              user.role === "admin") && (
              <div className="comment-controls">
                <button
                  onClick={() => {
                    setEditing(c.id);
                    setText(c.text);
                  }}
                >
                  编辑
                </button>
                <button
                  onClick={() =>
                    void api
                      .del("/comments/" + c.id)
                      .then(() => load())
                      .catch(onError)
                  }
                >
                  删除
                </button>
              </div>
            )}
          </div>
        ))}
        {next && (
          <button className="text-button" onClick={() => void load(next)}>
            加载更多评论
          </button>
        )}
      </div>
      <form
        className="comment-compose"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <label htmlFor="comment-text">
          {editing ? "编辑评论" : "添加评论"}
        </label>
        <textarea
          id="comment-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="写下你的想法…"
          maxLength={20000}
        />
        <div>
          <span>支持 Markdown</span>
          {editing && (
            <button
              type="button"
              onClick={() => {
                setEditing(null);
                setText("");
              }}
            >
              取消
            </button>
          )}
          <button className="button primary" disabled={busy || !text.trim()}>
            发送
          </button>
        </div>
      </form>
    </aside>
  );
}

export function Reader({
  session,
  api,
  user,
  onClose,
  onError,
  onNotice,
  onChange,
  versionSignal,
}: {
  session: Session;
  api: Client;
  user: User;
  onClose: () => void;
  onError: (e: unknown) => void;
  onNotice: (text: string) => void;
  onChange: () => void;
  versionSignal: number;
}) {
  const [revision, setRevision] = useState(
      session.favorite_revision_id || session.current_revision || "",
    ),
    [rounds, setRounds] = useState<Round[]>([]),
    [expanded, setExpanded] = useState<number[]>([]),
    [directory, setDirectory] = useState(false),
    [directoryItems, setDirectoryItems] = useState<Round[]>([]),
    [dirHistory, setDirHistory] = useState([""]),
    [dirNext, setDirNext] = useState<string | null>(null),
    [comments, setComments] = useState<{ event: EventRecord | null } | null>(
      null,
    ),
    [menu, setMenu] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [revisions, setRevisions] = useState<Revision[]>([]),
    [newVersion, setNewVersion] = useState(false),
    [saved, setSaved] = useState(!!session.favorite),
    [rename, setRename] = useState(false),
    [title, setTitle] = useState(session.title),
    [copy, setCopy] = useState(false);
  const mountedSignal = useRef(versionSignal);
  useEffect(() => {
    if (mountedSignal.current !== versionSignal) {
      setNewVersion(true);
      mountedSignal.current = versionSignal;
    }
  }, [versionSignal]);
  useEffect(() => {
    let live = true;
    setBusy(true);
    setError("");
    setRounds([]);
    setExpanded([]);
    if (!revision) {
      setBusy(false);
      return;
    }
    api
      .get<Page<Round>>(
        "/sessions/" + session.id + "/rounds" + query({ revision, recent: 3 }),
      )
      .then(async (r) => {
        if (
          session.favorite_round !== undefined &&
          !r.items.some((x) => x.number === session.favorite_round)
        ) {
          const target = await api.get<Page<Round>>(
            "/sessions/" +
              session.id +
              "/rounds" +
              query({ revision, cursor: session.favorite_round - 1, limit: 1 }),
          );
          r.items = [...r.items.slice(-2), ...target.items].sort(
            (a, b) => a.number - b.number,
          );
        }
        if (live) {
          setRounds(r.items);
          setExpanded(r.items.map((x) => x.number));
        }
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
  }, [session.id, revision, api]);
  useEffect(() => {
    if (!directory) return;
    let live = true;
    api
      .get<Page<Round>>(
        "/sessions/" +
          session.id +
          "/rounds" +
          query({
            revision,
            cursor: dirHistory[dirHistory.length - 1],
            limit: 100,
          }),
      )
      .then((r) => {
        if (live) {
          setDirectoryItems(r.items);
          setDirNext(r.next_cursor || null);
        }
      })
      .catch(onError);
    return () => {
      live = false;
    };
  }, [directory, dirHistory, revision, session.id, api]);
  function openRound(round: Round) {
    setRounds((old) => {
      const next = [...old.filter((r) => r.number !== round.number), round];
      return next
        .filter((r) => expanded.includes(r.number) || r.number === round.number)
        .sort((a, b) => a.number - b.number);
    });
    setExpanded((prev) => boundedExpanded(prev, round.number));
    setDirectory(false);
  }
  async function bookmark(event?: EventRecord) {
    try {
      await api.post("/favorites", {
        session_id: session.id,
        revision_id: revision,
        event_id: event?.id || null,
      });
      setSaved(true);
      onNotice("已加入我的收藏");
      onChange();
    } catch (e) {
      onError(e);
    }
  }
  async function exportSession(format: "html" | "markdown") {
    try {
      await api.post("/sessions/" + session.id + "/exports", {
        format,
        revision_id: revision,
      });
      setMenu(false);
      onNotice("导出已加入任务队列，完成后可在同步任务中下载");
    } catch (e) {
      onError(e);
    }
  }
  async function refresh() {
    try {
      const r = await api.get<Session>("/sessions/" + session.id);
      setRevision(r.current_revision || "");
      setTitle(r.title);
      setNewVersion(false);
      onChange();
    } catch (e) {
      onError(e);
    }
  }
  return (
    <section className="reader">
      <div className="reader-top">
        <div className="reader-breadcrumb">
          <span>
            {session.project?.split(/[\\/]/).filter(Boolean).pop() ||
              "未分类项目"}
          </span>
          <ChevronRight size={13} />
          <span>{session.owner_name || "我的记录"}</span>
        </div>
        <div className="reader-actions">
          <button
            className={"icon-button " + (saved ? "active" : "")}
            aria-label="收藏会话"
            onClick={() => void bookmark()}
          >
            <Bookmark size={17} fill={saved ? "currentColor" : "none"} />
          </button>
          <button
            className="icon-button"
            aria-label="打开讨论"
            onClick={() => setComments({ event: null })}
          >
            <MessageSquare size={17} />
          </button>
          <div className="menu-wrap">
            <button
              className="icon-button"
              aria-label="会话操作"
              onClick={() => setMenu(!menu)}
            >
              <MoreHorizontal size={19} />
            </button>
            {menu && (
              <div className="popover">
                <button onClick={() => void exportSession("html")}>
                  <Download size={14} />
                  导出 HTML
                </button>
                <button onClick={() => void exportSession("markdown")}>
                  <FileText size={14} />
                  导出 Markdown
                </button>
                <button
                  onClick={() => {
                    void api
                      .get<Page<Revision>>(
                        "/sessions/" + session.id + "/revisions",
                      )
                      .then((r) => setRevisions(asPage(r).items))
                      .catch(onError);
                    setMenu(false);
                  }}
                >
                  查看历史版本
                </button>
                {(session.owner_id === user.id || user.role === "admin") && (
                  <button
                    onClick={() => {
                      setRename(true);
                      setMenu(false);
                    }}
                  >
                    重命名
                  </button>
                )}
                <button
                  onClick={() => {
                    void navigator.clipboard
                      .writeText(
                        location.origin +
                          location.pathname +
                          "#session=" +
                          session.id,
                      )
                      .then(() => {
                        setCopy(true);
                        setTimeout(() => setCopy(false), 2000);
                      })
                      .catch(onError);
                    setMenu(false);
                  }}
                >
                  {copy ? <Check size={14} /> : <Copy size={14} />}复制会话链接
                </button>
              </div>
            )}
          </div>
          <button
            className="icon-button mobile-close"
            aria-label="返回会话列表"
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </div>
      </div>
      <div className="reader-main">
        <div className="transcript-scroll">
          <header className="transcript-title">
            {rename ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void api
                    .patch("/sessions/" + session.id, { title })
                    .then(() => {
                      setRename(false);
                      onChange();
                    })
                    .catch(onError);
                }}
              >
                <input
                  autoFocus
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  maxLength={200}
                />
                <button className="button primary">保存</button>
                <button type="button" onClick={() => setRename(false)}>
                  取消
                </button>
              </form>
            ) : (
              <h1>{title}</h1>
            )}
            <p>
              <span>{session.source_kind || "Cursor"}</span>
              <i />
              更新于 {displayDate(session.updated_at)}
              <i />
              {session.round_count || 0} 轮对话
            </p>
          </header>
          {newVersion && (
            <button className="update-banner" onClick={() => void refresh()}>
              有新的同步内容 · 点击切换到最新版本
            </button>
          )}
          {revisions.length > 0 && (
            <label className="version-select">
              正在查看
              <select
                value={revision}
                onChange={(e) => {
                  setRevision(e.target.value);
                  setComments(null);
                }}
              >
                {revisions.map((r) => (
                  <option key={r.id} value={r.id}>
                    {displayDate(r.created_at)} · {r.event_count || 0} 条记录
                  </option>
                ))}
              </select>
            </label>
          )}
          <ErrorText error={error} />
          {session.status === "ready_with_diagnostics" && (
            <div className="diagnostic-note">
              本记录包含未知或不完整的原始条目；展开“原始记录 /
              解析说明”查看详情。
            </div>
          )}
          {!revision ? (
            <div className="empty-small">
              <Loader2 size={24} />
              <p>正在准备会话</p>
              <span>解析完成后点击更新，已完成的记录不受影响。</span>
              <button className="button" onClick={() => void refresh()}>
                刷新状态
              </button>
            </div>
          ) : (
            <>
              <button
                className="older-toggle"
                onClick={() => setDirectory((v) => !v)}
                aria-expanded={directory}
              >
                <ChevronsUp size={15} />
                {directory ? "收起历史目录" : "查看更早的对话"}
                <span>按轮次加载</span>
                <ChevronDown size={15} />
              </button>
              {directory && (
                <div className="round-directory">
                  <div className="directory-heading">
                    <strong>对话目录</strong>
                    <span>点击标题读取内容</span>
                  </div>
                  {directoryItems.map((r) => (
                    <button key={r.number} onClick={() => openRound(r)}>
                      <span>{String(r.number).padStart(2, "0")}</span>
                      <span>
                        {roundPreview(r.preview) || "对话 " + r.number}
                      </span>
                      <small>{r.count} 条</small>
                      <ChevronRight size={14} />
                    </button>
                  ))}
                  <div className="pagination compact">
                    <button
                      disabled={dirHistory.length <= 1}
                      onClick={() => setDirHistory((h) => h.slice(0, -1))}
                    >
                      上一页
                    </button>
                    <span>第 {dirHistory.length} 页</span>
                    <button
                      disabled={!dirNext}
                      onClick={() => {
                        if (dirNext) setDirHistory((h) => [...h, dirNext]);
                      }}
                    >
                      下一页
                    </button>
                  </div>
                </div>
              )}
              {busy ? (
                <div className="loading-line">
                  <Loader2 className="spin" size={18} />
                  正在读取最近 3 轮对话
                </div>
              ) : (
                rounds.map((r) => (
                  <section key={r.number} className="round">
                    <button
                      className="round-heading"
                      onClick={() =>
                        setExpanded((prev) => boundedExpanded(prev, r.number))
                      }
                      aria-expanded={expanded.includes(r.number)}
                    >
                      <span className="round-number">
                        {String(r.number).padStart(2, "0")}
                      </span>
                      <span>
                        {roundPreview(r.preview) || "对话 " + r.number}
                      </span>
                      <small>{r.count} 条</small>
                      <ChevronDown
                        className={expanded.includes(r.number) ? "" : "closed"}
                        size={16}
                      />
                    </button>
                    {expanded.includes(r.number) && (
                      <RoundEvents
                        session={session}
                        revision={revision}
                        round={r}
                        api={api}
                        onComment={(event) => setComments({ event })}
                        onFavorite={(event) => void bookmark(event)}
                      />
                    )}
                  </section>
                ))
              )}
            </>
          )}
          <div className="reader-footnote">
            仅展开最近或选中的 3 轮 · 工具与大段内容按需读取
          </div>
        </div>
        {comments && (
          <Comments
            session={session}
            revision={revision}
            event={comments.event}
            api={api}
            user={user}
            onClose={() => setComments(null)}
            onError={onError}
          />
        )}
      </div>
    </section>
  );
}
