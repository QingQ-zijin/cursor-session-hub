import {useEffect,useRef,useState} from 'react';
import {ArrowUp,AtSign,Check,ChevronDown,MessageSquare,Plus,Settings,Square,X} from 'lucide-react';
import type {Client} from './api';
import {ApiError,query} from './api';
import type {Session,User} from './types';
import {Modal} from './Management';
import {Markdown} from './RichText';
import {AISettings} from './AISettings';
import type {AIConfig} from './AISettings';

type Ref={session_id:string;revision_id?:string|null;title:string;events?:number;total_events?:number;partial?:boolean;characters?:number};
type Message={id:string;seq:number;role:string;text:string;state:string;received_chars?:number;error?:string|null;metadata_json?:{model?:string}};
type Preview={references:Ref[];settings_version:number;destination:string;model:string};
type Thread={id:string;title:string};
function nonce(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('')}
export function AIChat({api,user,initialSessions=[],compact=false}:{api:Client;user:User;initialSessions?:Session[];compact?:boolean}){
  const [config,setConfig]=useState<AIConfig|null>(null),[references,setReferences]=useState<Ref[]>(()=>initialSessions.slice(0,5).map(s=>({session_id:s.id,revision_id:s.current_revision,title:s.title}))),
    [draft,setDraft]=useState(''),[preview,setPreview]=useState<Preview|null>(null),[error,setError]=useState(''),[messages,setMessages]=useState<Message[]>([]),
    [thread,setThread]=useState<string|null>(null),[threads,setThreads]=useState<Thread[]>([]),[threadNext,setThreadNext]=useState<string|null>(null),[older,setOlder]=useState<number|null>(null),
    [active,setActive]=useState<string|null>(null),[sending,setSending]=useState(false),[settings,setSettings]=useState(false),[picker,setPicker]=useState(false),[candidates,setCandidates]=useState<Session[]>([]),
    [mention,setMention]=useState(''),[expanded,setExpanded]=useState(!compact),[revision,setRevision]=useState(0);
  const alive=useRef(true), requestId=useRef(nonce()), feed=useRef<HTMLDivElement>(null), sendingRef=useRef(false), historyRequest=useRef(0);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;historyRequest.current++}},[]);
  const signature=JSON.stringify(references.map(r=>({session_id:r.session_id,revision_id:r.revision_id})));
  const initialSignature=JSON.stringify(initialSessions.slice(0,5).map(s=>({session_id:s.id,revision_id:s.current_revision,title:s.title})));
  useEffect(()=>{if(!thread&&!messages.length){setReferences(JSON.parse(initialSignature));setPreview(null);requestId.current=nonce()}},[initialSignature]);
  useEffect(()=>{let valid=true;void api.get<AIConfig>('/ai/settings').then(c=>{if(valid)setConfig(c)}).catch(e=>{if(valid)setError(String(e))});void api.get<{items:Thread[];next_cursor:string|null}>('/ai/threads').then(p=>{if(valid){setThreads(p.items||[]);setThreadNext(p.next_cursor)}}).catch(()=>{});return()=>{valid=false}},[api,revision]);
  useEffect(()=>{let valid=true;setPreview(null);if(!config?.enabled||!config.configured)return;const timer=setTimeout(()=>{void api.post<Preview>('/ai/context',{references:JSON.parse(signature),query:draft}).then(p=>{if(valid)setPreview(p)}).catch(e=>{if(valid)setError(String(e))})},250);return()=>{valid=false;clearTimeout(timer)}},[api,signature,draft,config?.version,config?.enabled,config?.configured]);
  useEffect(()=>{if(!picker)return;let valid=true;const timer=setTimeout(()=>{void api.get<{items:Session[]}>('/sessions'+query({q:mention,limit:30})).then(p=>{if(valid)setCandidates(p.items||[])}).catch(e=>{if(valid)setError(String(e))})},180);return()=>{valid=false;clearTimeout(timer)}},[picker,mention,api]);
  useEffect(()=>{
    if(!active)return;let valid=true;const id=active;const current=messages.find(m=>m.id===id);let offset=current?.received_chars??Array.from(current?.text||'').length;
    void(async()=>{let failures=0;while(valid){try{
      const result=await api.get<{text:string;next_offset:number;state:string;error?:string;has_more:boolean}>('/ai/replies/'+id+query({offset}));if(!valid)return;
      failures=0;setError('');offset=result.next_offset;
      setMessages(old=>old.map(m=>m.id===id?{...m,text:m.text+result.text,received_chars:offset,state:result.state,error:result.error}:m));
      if(feed.current){const el=feed.current;if(el.scrollHeight-el.scrollTop-el.clientHeight<160)requestAnimationFrame(()=>{if(valid)el.scrollTop=el.scrollHeight})}
      if(!['queued','running'].includes(result.state)&&!result.has_more){setActive(null);setRevision(v=>v+1);break}
    }catch(e){if(!valid)return;if(e instanceof ApiError&&[401,403,404].includes(e.status)){setError(e.message);setActive(null);return}
      setError('读取回复的连接中断，正在重连；不会重新发送模型请求。');failures++;
      await new Promise(resolve=>setTimeout(resolve,Math.min(10000,1000*2**Math.min(failures,4))));
    }}})();
    return()=>{valid=false};
  },[active,api]);
  async function send(e:React.FormEvent){e.preventDefault();if(!draft.trim()||!preview||active||sendingRef.current)return;sendingRef.current=true;setSending(true);setError('');setExpanded(true);
    try{const result=await api.post<{thread_id:string;user:Message;assistant:Message}>('/ai/messages',{text:draft,references:preview.references.map(r=>({session_id:r.session_id,revision_id:r.revision_id})),thread_id:thread,request_id:requestId.current,settings_version:preview.settings_version});if(!alive.current)return;setThread(result.thread_id);setMessages(old=>[...old.filter(m=>m.id!==result.user.id&&m.id!==result.assistant.id),result.user,result.assistant]);setActive(result.assistant.id);setDraft('');setPreview(null);requestId.current=nonce();requestAnimationFrame(()=>{if(feed.current)feed.current.scrollTop=feed.current.scrollHeight})}catch(e){if(alive.current){setError(e instanceof Error?e.message:String(e));if(e instanceof ApiError&&e.status===409){setRevision(v=>v+1);setPreview(null)}}}finally{sendingRef.current=false;if(alive.current)setSending(false)}}
  async function loadThread(id:string,before?:number){const ticket=++historyRequest.current;setError('');try{const data=await api.get<{items:Message[];next_cursor:number|null}>('/ai/threads/'+id+query({before}));if(!alive.current||ticket!==historyRequest.current)return;setThread(id);setMessages(old=>before?[...data.items,...old]:data.items);setOlder(data.next_cursor);setExpanded(true);const running=data.items.find(m=>['queued','running'].includes(m.state));if(running)setActive(running.id)}catch(e){if(alive.current)setError(String(e))}}
  function updateDraft(text:string){setDraft(text);setPreview(null);requestId.current=nonce();const found=/(?:^|\s)@([^\s@]*)$/.exec(text);if(found){setMention(found[1]);setPicker(true)}else setPicker(false)}
  function addReference(session:Session){if(references.some(r=>r.session_id===session.id)){setPicker(false);return}if(references.length>=5){setError('一次最多引用5个会话');return}setReferences(old=>[...old,{session_id:session.id,revision_id:session.current_revision,title:session.title}]);setPreview(null);setDraft(old=>old.replace(/(?:^|\s)@[^\s@]*$/,' ').trimEnd());requestId.current=nonce();setPicker(false)}
  return <section className={'ai-chat '+(compact?'compact ':'standalone ')+(expanded?'expanded':'')} aria-label="API 会话聊天">
    <header className="ai-chat-header"><button onClick={()=>setExpanded(v=>!v)}><MessageSquare size={14}/>{compact?'继续对话':'Chat'}<ChevronDown size={13}/></button><span>{config?.model||'未配置 API'}</span>
      <select aria-label="我的聊天记录" value={thread||''} disabled={!!active||sending} onChange={e=>{if(e.target.value)void loadThread(e.target.value);else{setThread(null);setMessages([]);setOlder(null);historyRequest.current++}}}><option value="">新对话</option>{threads.map(t=><option key={t.id} value={t.id}>{t.title}</option>)}{thread&&!threads.some(t=>t.id===thread)&&<option value={thread}>当前对话</option>}</select>
      {threadNext&&<button title="更多聊天记录" onClick={()=>void api.get<{items:Thread[];next_cursor:string|null}>('/ai/threads'+query({cursor:threadNext})).then(p=>{setThreads(old=>[...old,...p.items]);setThreadNext(p.next_cursor)}).catch(e=>setError(String(e)))}>…</button>}
      {user.role==='admin'&&<button aria-label="API 设置" title="API 设置" onClick={()=>setSettings(true)}><Settings size={15}/></button>}
    </header>
    {expanded&&messages.length>0&&<div className="ai-messages" ref={feed}>
      {older!==null&&thread&&<button className="text-button" onClick={()=>void loadThread(thread,older)}>加载更早的聊天</button>}
      {messages.map(m=><article key={m.id} className={'ai-message '+m.role}><small>{m.role==='user'?'你':m.metadata_json?.model||config?.model||'Assistant'}{m.state==='running'?' · 正在回复':m.state==='queued'?' · 等待回复':m.state==='truncated'?' · 达到输出上限':''}</small><Markdown text={m.text||(['queued','running'].includes(m.state)?'正在连接模型…':'（未返回正文）')}/>{m.error&&<p className="inline-error">{m.error}</p>}</article>)}
    </div>}
    <form onSubmit={e=>void send(e)} className="ai-composer">
      <div className="ai-references">{references.map(r=><span className="ai-reference" key={r.session_id} title={r.title}>@ {r.title}<button type="button" aria-label={'移除引用 '+r.title} disabled={!!active} onClick={()=>{setReferences(old=>old.filter(v=>v.session_id!==r.session_id));setPreview(null)}}><X size={11}/></button></span>)}</div>
      <textarea aria-label="聊天消息" placeholder="提问，或输入 @ 引用会话" value={draft} maxLength={8000} onChange={e=>updateDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing&&!picker){e.preventDefault();e.currentTarget.form?.requestSubmit()}if(e.key==='Escape')setPicker(false)}}/>
      {picker&&<div className="ai-mention-picker" role="dialog" aria-label="选择引用会话"><input aria-label="搜索引用会话" placeholder="搜索会话标题或正文" value={mention} onChange={e=>setMention(e.target.value)}/>{candidates.filter(s=>s.current_revision).map(s=><button type="button" key={s.id} onClick={()=>addReference(s)}><AtSign size={13}/><span>{s.title}<small>{s.project||'未记录工作区'}</small></span>{references.some(r=>r.session_id===s.id)&&<Check size={13}/>}</button>)}<button type="button" onClick={()=>setPicker(false)}>关闭</button></div>}
      <footer><button type="button" aria-label="引用会话" title="引用会话" disabled={!!active} onClick={()=>{setMention('');setPicker(v=>!v)}}><AtSign size={17}/></button><span>{config?.enabled&&config.configured?(preview?`${preview.references.length} 个会话 · ${preview.references.reduce((n,r)=>n+(r.events||0),0)} 条参考片段`:'正在检查引用范围…'):'管理员尚未启用 API'}</span>{active?<button type="button" className="ai-send" aria-label="停止回复" onClick={()=>void api.post('/ai/replies/'+active+'/stop').catch(e=>setError(String(e)))}><Square size={14}/></button>:<button className="ai-send" aria-label="发送消息" disabled={!config?.enabled||!config.configured||!preview||!draft.trim()||sending}><ArrowUp size={17}/></button>}</footer>
    </form>
    {preview&&<div className="ai-context-note">发送至 {preview.destination} · {preview.references.some(r=>r.partial)?'历史已按预算选取相关/近期片段':'引用已解析文字'} · 不含图片。当前聊天最多使用最近20条记录。</div>}
    {error&&<p className="inline-error" role="alert">{error}</p>}
    {settings&&<Modal title="API 设置" onClose={()=>setSettings(false)}><AISettings api={api} onSaved={()=>setRevision(v=>v+1)}/></Modal>}
  </section>;
}
