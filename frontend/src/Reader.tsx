import {Markdown} from "./RichText";
import {readingGroups} from './reader-model';
import {Modal} from "./Management";
import { readContent } from "./content";
import { Children, isValidElement, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUp,
  Trash2,
  RotateCcw,
  PanelRight,
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
import { asPage, download, fetchAsset, query, sessionLink } from "./api";
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
  copyText,
  displayDate,
  roundPreview,
  safeLink,
  stringify,
} from "./utils";

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
  const [text, setText] = useState(initial), [busy, setBusy] = useState(!!id),
    [error, setError] = useState(""), [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true;
    setText(initial); setError(""); setBusy(!!id);
    if (id) void readContent(api, id, () => live).then(value => {
      if (live && value !== null) setText(value);
    }).catch(e => { if (live) setError(String(e)); })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [id, api, initial, retry]);
  return <>
    <ErrorText error={error} />
    {error && <button className="text-button" onClick={() => setRetry(v => v + 1)}>重试加载完整内容</button>}
    {busy && <div className="loading-line" role="status"><Loader2 size={14} className="spin" />正在加载完整内容…</div>}
    {markdown ? <Markdown text={text} /> : <pre className="payload"><code>{text || "（无内容）"}</code></pre>}
  </>;
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
  const input = block.input && typeof block.input === 'object' ? block.input as Record<string,unknown> : {};
  const summary = String(block.summary || input.command || input.cmd || input.path || input.file_path || '已保存的工具记录').split(/\r?\n/)[0].slice(0,180);
  return (
    <div className={"tool-record " + (open ? "is-open" : "")}>
      <button
        className="tool-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Terminal size={15} />
        <strong>{String(block.name || "工具调用")}</strong>
        <span title={summary}>{summary}</span>
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
  initialOpen=false,
}: {
  label: string;
  children: React.ReactNode;
  initialOpen?: boolean;
}) {
  const [open, setOpen] = useState(initialOpen);
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
function FullEvent({ id, api }: {id: string; api: Client}) {
  const [event, setEvent] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState(""), [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true; setError("");
    void readContent(api, id, () => live).then(text => {
      if (!live || text === null) return;
      const value = JSON.parse(text);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("会话消息格式无效");
      delete value._full_content_id;
      setEvent(value);
    }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [id, api, retry]);
  if (error) return <><ErrorText error={error} /><button className="text-button" onClick={() => setRetry(v => v + 1)}>重试读取消息</button></>;
  return event ? <EventBody event={event} api={api} /> : <div className="loading-line">正在加载完整消息…</div>;
}
function EventBody({
  event,
  api,
}: {
  event: Record<string, unknown>;
  api: Client;
}) {
  if (event.content_id && event.content_id === event._full_content_id)
    return <FullEvent id={String(event.content_id)} api={api} />;
  const blocks = event.blocks as Record<string, unknown>[] | undefined;
  if (blocks?.length) {
    const groups: {process:boolean;blocks:Record<string,unknown>[]}[]=[];
    for(const block of blocks){const process=['tool_use','thinking'].includes(String(block.type));const previous=groups.at(-1);if(process&&previous?.process)previous.blocks.push(block);else groups.push({process,blocks:[block]})}
    return <>{groups.map((group,i)=>group.process?<Collapsed key={i} label={'执行过程 · '+group.blocks.length+' 条记录'}>{group.blocks.map((block,j)=><Block key={j} block={block} api={api}/>)}</Collapsed>:<Block key={i} block={group.blocks[0]} api={api}/>)}</>;
  }
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
    [error, setError] = useState(""), [loading, setLoading] = useState(true),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true); setItems([]); setError("");
    void (async () => {
      let cursor: string | null = null;
      do {
        const response: Page<EventRecord> = await api.get("/sessions/" + session.id + "/events" + query({revision, round: round.number, cursor, limit: 40}));
        if (!live) return;
        setItems(previous => [...previous, ...response.items]);
        if (response.next_cursor && response.next_cursor === cursor) throw new Error("轮次读取未能前进，请重试");
        cursor = response.next_cursor || null;
      } while (cursor !== null && live);
      if (live && session.favorite_event_id) requestAnimationFrame(() => {
        if (live) document.getElementById("event-" + session.favorite_event_id)?.scrollIntoView({block: "start"});
      });
    })().catch(e => { if (live) setError(String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [session.id, revision, round.number, api, retry]);
  const groups=loading?[]:readingGroups(items);
  function processLabel(records:EventRecord[]){
    const times=records.map(r=>{const ts=r.event.ts;return typeof ts==='number'?(ts<1e11?ts*1000:ts):typeof ts==='string'?Date.parse(ts):NaN}).filter(Number.isFinite);
    const seconds=times.length>1?Math.floor((Math.max(...times)-Math.min(...times))/1000):0;
    return seconds>0&&seconds<86400?`Worked for ${Math.floor(seconds/60)}m ${seconds%60}s · ${records.length} 条记录`:`执行过程 · ${records.length} 条记录`;
  }
  return (
    <div className="round-content" aria-busy={loading}>
      <ErrorText error={error} />
      {error && <button className="text-button" onClick={() => setRetry(v => v + 1)}>重试加载本轮</button>}
      {groups.map((group) => group.process ? <div className="execution-summary" key={'process-'+group.items[0].id} title="包含最终回答之前的思考、说明和工具执行"><Collapsed initialOpen={group.items.some(item=>item.id===session.favorite_event_id)} label={processLabel(group.items)}>{group.items.map(item=><div key={item.id} id={'process-event-'+item.id}><EventBody event={item.event} api={api}/></div>)}</Collapsed></div> : group.items.map((item) => (
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
        )))}
      {loading && <div className="loading-line" role="status"><Loader2 size={16} className="spin" />正在加载本轮完整对话，已读取 {items.length} 条…</div>}
      {!loading&&groups.some(g=>g.process)&&!groups.some(g=>!g.process&&g.items.some(i=>i.event.kind==='assistant'))&&<p className="inline-note">暂无最终回答</p>}

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
    [directory, setDirectory] = useState(window.innerWidth > 1000),
    [trash,setTrash] = useState(false),
    [editRound,setEditRound] = useState<Round|null>(null),
    [editNote,setEditNote] = useState(""),
    [editBusy,setEditBusy] = useState(false),
    [editError,setEditError] = useState(""),
    [edits,setEdits] = useState(0),
    [dirBusy,setDirBusy] = useState(false),
    [dirError,setDirError] = useState(""),
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
          if(live&&!target.items.some(item=>item.number===session.favorite_round))setError('收藏的轮次已移到回收站，可从右侧恢复。');
          r.items = [...r.items.slice(-2), ...target.items.filter(item=>item.number===session.favorite_round)].sort(
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
  }, [session.id, revision, api, edits]);
  useEffect(() => {
    if (!directory) return;
    let live = true;setDirBusy(true);setDirError("");setDirectoryItems([]);
    api
      .get<Page<Round>>(
        "/sessions/" +
          session.id +
          "/rounds" +
          query({
            revision,
            cursor: dirHistory[dirHistory.length - 1],
            limit: 100, trash,
          }),
      )
      .then((r) => {
        if (live) {
          setDirectoryItems(r.items);
          setDirNext(r.next_cursor || null);
        }
      })
      .catch(e=>{if(live)setDirError(String(e))}).finally(()=>{if(live)setDirBusy(false)});
    return () => {
      live = false;
    };
  }, [directory, dirHistory, revision, session.id, api, trash, edits]);
  function openRound(round: Round) {
    setRounds((old) => {
      const next = [...old.filter((r) => r.number !== round.number), round];
      return next
        .filter((r) => expanded.includes(r.number) || r.number === round.number)
        .sort((a, b) => a.number - b.number);
    });
    setExpanded((prev) => boundedExpanded(prev, round.number));
    requestAnimationFrame(()=>document.getElementById("round-"+round.number)?.scrollIntoView({block:"start"}));
  }
  async function saveRound(deleted:boolean){
    if(!editRound||editBusy)return;setEditBusy(true);setEditError('');
    try{await api.patch('/sessions/'+session.id+'/rounds/'+editRound.number,{revision_id:revision,deleted,note:editNote});setEditRound(null);setEdits(v=>v+1);onChange();onNotice(deleted?'已移到回收站，可在右侧恢复':'轮次已恢复')}
    catch(e){setEditError(e instanceof Error?e.message:String(e))}finally{setEditBusy(false)}
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
  async function exportSession(format: "html" | "markdown" | "pdf") {
    try {
      await api.post("/sessions/" + session.id + "/exports", {
        format,
        revision_id: revision,
      });
      setMenu(false);
      onNotice("导出已提交");
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
      setEdits(v=>v+1);
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
          <button className="icon-button" aria-label="更早的会话" title="轮次目录与回收站" aria-expanded={directory} onClick={()=>{setDirectory(v=>!v);setComments(null)}}><PanelRight size={17}/></button>
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
                <button onClick={() => void exportSession("pdf")}>
                  <FileText size={14} />导出 PDF
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
                    void sessionLink(api, session.id, user.id === "local")
                      .then(copyText)
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
              <button className="button" onClick={() => void refresh()}>
                刷新状态
              </button>
            </div>
          ) : (
            <>
              {busy ? (
                <div className="loading-line">
                  <Loader2 className="spin" size={18} />
                  正在读取最近 3 轮对话
                </div>
              ) : (
                rounds.map((r) => (
                  <section key={r.number} id={"round-"+r.number} className="round">
                    <div className="round-head-row">
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
                    {(user.role==="admin"||user.id===session.owner_id)&&<button className="icon-button round-delete" aria-label={"删除第 "+r.number+" 轮"} title="移到回收站" onClick={()=>{setEditRound(r);setEditNote(r.note||"");setEditError("")}}><Trash2 size={14}/></button>}
                    </div>
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
        </div>
        {directory && !comments && <aside className="round-rail" aria-label="轮次目录">
          <div className="rail-tabs"><button className={!trash?'active':''} onClick={()=>{setTrash(false);setDirHistory([''])}}>更早的会话</button><button className={trash?'active':''} onClick={()=>{setTrash(true);setDirHistory([''])}}><Trash2 size={13}/>回收站</button><button className="rail-close" aria-label="关闭轮次目录" onClick={()=>setDirectory(false)}><X size={14}/></button></div>
          <div className="rail-list" aria-busy={dirBusy}>
            {dirBusy?<p role="status">正在加载目录…</p>:<><ErrorText error={dirError}/>{dirError&&<button onClick={()=>setEdits(v=>v+1)}>重试目录</button>}{!directoryItems.length&&!dirError&&<p className="muted">{trash?'回收站为空':'暂无轮次'}</p>}
            {directoryItems.map(r=><div key={r.number} className={'rail-round '+(expanded.includes(r.number)&&!trash?'active':'')}>
              <button disabled={trash} onClick={()=>openRound(r)}><small>{String(r.number).padStart(2,'0')}</small><span>{roundPreview(r.preview)||'对话 '+r.number}</span></button>
              {trash&&<p className="trash-note">{r.note||'未填写删除说明'}</p>}
              {(user.role==='admin'||user.id===session.owner_id)&&<button className="rail-action" aria-label={(trash?'恢复或备注第 ':'删除第 ')+r.number+' 轮'} onClick={()=>{setEditRound(r);setEditNote(r.note||'');setEditError('')}}>{trash?<RotateCcw size={13}/>:<Trash2 size={13}/>}</button>}
            </div>)}</>}
          </div>
          <div className="pagination compact"><button aria-label="目录上一页" disabled={dirHistory.length<=1||dirBusy} onClick={()=>setDirHistory(h=>h.slice(0,-1))}><ChevronLeft size={14}/></button><span>{dirHistory.length}</span><button aria-label="目录下一页" disabled={!dirNext||dirBusy} onClick={()=>{if(dirNext)setDirHistory(h=>[...h,dirNext])}}><ChevronRight size={14}/></button></div>
        </aside>}
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
      {editRound&&<Modal title={editRound.deleted?'回收站 · 第 '+editRound.number+' 轮':'删除第 '+editRound.number+' 轮'} onClose={()=>{if(!editBusy)setEditRound(null)}}>
        <form className="round-edit" onSubmit={e=>{e.preventDefault();void saveRound(true)}}>
          <p>{editRound.deleted?'可更新删除说明，或恢复这一轮。':'移到回收站后可恢复。原始 Cursor 文件不会改变。'}</p>
          <label>删除说明（可选）<textarea aria-label="删除说明" value={editNote} maxLength={1000} onChange={e=>setEditNote(e.target.value)}/></label><ErrorText error={editError}/>
          <footer><button type="button" className="button" disabled={editBusy} onClick={()=>setEditRound(null)}>取消</button>{editRound.deleted&&<button type="button" className="button" disabled={editBusy} onClick={()=>void saveRound(false)}><RotateCcw size={14}/>恢复轮次</button>}<button className="button primary" disabled={editBusy}>{editRound.deleted?'保存备注':'移到回收站'}</button></footer>
        </form>
      </Modal>}
    </section>
  );
}
