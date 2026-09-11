import {useEffect,useState} from 'react';
import type {Client} from './api';

export type AIConfig={enabled:boolean;configured:boolean;base_url:string;model:string;context_chars:number;max_output_tokens:number;token_parameter:string;version:number;scope:string};
export function AISettings({api,onSaved}:{api:Client;onSaved?:()=>void}) {
  const [config,setConfig]=useState<AIConfig|null>(null),[key,setKey]=useState(''),[error,setError]=useState(''),[saved,setSaved]=useState(false),[busy,setBusy]=useState(false);
  useEffect(()=>{let alive=true;void api.get<AIConfig>('/ai/settings').then(value=>{if(alive)setConfig(value)}).catch(e=>{if(alive)setError(String(e))});return()=>{alive=false}},[api]);
  async function save(e:React.FormEvent){e.preventDefault();if(!config)return;setBusy(true);setError('');setSaved(false);try{const value=await api.patch<AIConfig>('/ai/settings',{...config,api_key:key||null});setConfig(value);setKey('');setSaved(true);onSaved?.()}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
  if(!config)return <p role="status">{error||'正在读取 API 配置…'}</p>;
  return <form className="ai-settings" onSubmit={e=>void save(e)}>
    <p>{config.scope==='local'?'本地 API：配置仅保存在这台电脑。团队 API 请在团队空间中由管理员配置。':'团队 API：密钥保存在服务器，成员通过服务器聊天，不会获得密钥。'}</p>
    <label className="ai-enabled"><input type="checkbox" checked={!!config.enabled} onChange={e=>setConfig({...config,enabled:e.target.checked})}/>启用 API 聊天</label>
    <label>Base URL<input type="url" value={config.base_url||''} placeholder="https://api.example.com/v1" onChange={e=>setConfig({...config,base_url:e.target.value})}/></label>
    <label>API Key<input type="password" autoComplete="new-password" value={key} placeholder={config.configured?'已保存；留空保持原密钥':'填写服务商提供的 API Key'} onChange={e=>setKey(e.target.value)}/></label>
    <label>模型名称<input value={config.model||''} placeholder="服务商的模型 ID" onChange={e=>setConfig({...config,model:e.target.value})}/></label>
    <div className="ai-settings-row"><label>引用字符预算<input type="number" min={4000} max={64000} value={config.context_chars||24000} onChange={e=>setConfig({...config,context_chars:Number(e.target.value)})}/></label><label>输出 token 上限<input type="number" min={128} max={8192} value={config.max_output_tokens||2048} onChange={e=>setConfig({...config,max_output_tokens:Number(e.target.value)})}/></label></div>
    <label>输出参数<select value={config.token_parameter||'max_tokens'} onChange={e=>setConfig({...config,token_parameter:e.target.value})}><option value="max_tokens">max_tokens（通用兼容接口）</option><option value="max_completion_tokens">max_completion_tokens（部分 OpenAI 模型）</option></select></label>
    <small>兼容 OpenAI Chat Completions。记录与问题会发送到这里配置的服务商；不执行工具命令。长历史按字符预算选取，不代表整份会话全部发送。</small>
    {error&&<p className="inline-error" role="alert">{error}</p>}{saved&&<p role="status">API 配置已保存</p>}
    <button className="button primary" disabled={busy}>{busy?'保存中…':'保存 API 配置'}</button>
  </form>;
}
