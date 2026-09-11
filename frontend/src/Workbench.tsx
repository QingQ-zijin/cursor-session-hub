import { useEffect, useState } from 'react';
import { ArrowUp, ArrowUpRight, FolderOpen, Laptop, Plus, Search, Upload, Users, Bookmark, PanelLeft, PanelRight, Minus, Square, X } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { isDesktop } from './api';

export function Titlebar({toggle, dock, onDock, onImport, onSearch, onHelp, onTheme, busy=false}: {
  toggle:()=>void; dock:boolean; onDock:()=>void; onImport?:()=>void; onSearch:()=>void; onHelp:()=>void; onTheme:()=>void;
  busy?:boolean;
}) {
  const [menu,setMenu]=useState<string|null>(null);
  useEffect(()=>{ const close=(event:KeyboardEvent)=>{if(event.key==='Escape')setMenu(null)};document.addEventListener('keydown',close);return()=>document.removeEventListener('keydown',close)},[]);
  const actions:Record<string,[string,()=>void][]>={File:[...(onImport ? [['导入会话文件',onImport] as [string,()=>void]]:[]),['搜索会话',onSearch]],Edit:[['搜索会话',onSearch]],View:[['显示 / 隐藏侧栏',toggle],[dock?'工作区停靠左侧':'工作区停靠右侧',onDock],['切换深浅色主题',onTheme]],Help:[['使用说明',onHelp]]};
  return <header className="workbench-titlebar" data-tauri-drag-region>
    <button disabled={busy} className="shell-logo" title="Session Hub" aria-label="切换工作区侧栏" onClick={toggle}><span>H</span></button>
    {Object.keys(actions).map(name=><div className="title-menu" key={name}>
      <button disabled={busy} aria-expanded={menu===name} onClick={()=>setMenu(menu===name?null:name)}>{name}</button>
      {menu===name&&<><button className="menu-dismiss" aria-label="关闭菜单" onClick={()=>setMenu(null)}/><div className="shell-menu" role="menu">{actions[name].map(([text,fn])=><button role="menuitem" key={text} onClick={()=>{setMenu(null);fn()}}>{text}</button>)}</div></>}
    </div>)}
    <span className="shell-app-name" data-tauri-drag-region>Session Hub</span>
    {isDesktop()&&<div className="window-controls">{([['minimize',Minus,'最小化'],['maximize',Square,'最大化 / 还原'],['close',X,'关闭窗口']] as const).map(([action,Icon,label])=><button key={action} aria-label={label} onClick={()=>void invoke('window_action',{action})}><Icon size={14}/></button>)}</div>}
  </header>;
}

export function BootChrome() {
  const noop=()=>{};
  return <Titlebar busy dock={false} toggle={noop} onDock={noop} onSearch={noop} onHelp={noop} onTheme={noop}/>;
}

export function PreviewHome({projects,project,onProject,team,local,onScope,onSearch,onImport,onSources,onFavorites,onJobs}: {
  projects:string[];project:string;onProject:(p:string)=>void;team:boolean;local:boolean;onScope:(scope:'local'|'team')=>void;
  onSearch:(q:string)=>void;onImport:()=>void;onSources:()=>void;onFavorites:()=>void;onJobs:()=>void;
}) {
  const [text,setText]=useState('');
  return <section className="reader-empty preview-home" aria-label="会话预览工作台">
    <div className="preview-start">
      <div className="preview-context">
        <select aria-label="筛选工作区" value={project} onChange={e=>onProject(e.target.value)}><option value="">All workspaces</option>{projects.map(p=><option key={p} value={p}>{p.split(/[\\/]/).filter(Boolean).pop()}</option>)}</select>
        <Laptop size={16}/><select aria-label="选择记录位置" value={team?'team':'local'} onChange={e=>onScope(e.target.value as 'local'|'team')}>{local&&<option value="local">This PC</option>}<option value="team">Team server</option></select>
      </div>
      <form className="preview-composer" onSubmit={e=>{e.preventDefault();onSearch(text)}}>
        <textarea aria-label="搜索已保存的会话" placeholder="Search saved conversations" value={text} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();onSearch(text)}}}/>
        <div className="preview-composer-tools">
          {local&&!team&&<button type="button" className="composer-add" title="导入文件" aria-label="添加会话文件" onClick={onImport}><Plus size={21}/></button>}
          <span className="preview-chip">◉ Session preview</span><span className="preview-format">Cursor transcripts</span>
          <button className="composer-submit" aria-label="开始搜索会话" title="搜索会话"><ArrowUp size={18}/></button>
        </div>
      </form>
      <span className="preview-hint">选择会话开始阅读</span>
      <div className="preview-shortcuts">
        {local&&!team&&<><button onClick={onImport}><Upload size={21}/><strong>Import a session</strong><span>JSONL, Markdown, HTML or PDF</span><ArrowUpRight size={14}/></button>
        <button onClick={onSources}><FolderOpen size={21}/><strong>Browse Cursor sessions</strong><span>按工作区发现这台电脑上的会话</span><ArrowUpRight size={14}/></button></>}
        <button onClick={()=>onScope('team')}><Users size={21}/><strong>Team workspace</strong><span>查看成员同步的会话与讨论</span><ArrowUpRight size={14}/></button>
        <button onClick={onFavorites}><Bookmark size={21}/><strong>Saved conversations</strong><span>继续阅读已收藏的问答</span><ArrowUpRight size={14}/></button>
        {team&&<button onClick={onJobs}><Search size={21}/><strong>Sync history</strong><span>查看同步和导出任务</span><ArrowUpRight size={14}/></button>}
      </div>
    </div>
    <div className="preview-bottom-hint">从工作区选择会话预览 · 较早轮次按需加载</div>
  </section>;
}

export function PaneButton({right,onClick}:{right:boolean;onClick:()=>void}) {
  const Icon=right?PanelLeft:PanelRight;
  return <button className="icon-button" aria-label={right?'将工作区移至左侧':'将工作区移至右侧'} title={right?'工作区停靠左侧':'工作区停靠右侧'} onClick={onClick}><Icon size={17}/></button>;
}
