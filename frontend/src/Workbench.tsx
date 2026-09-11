import type {ReactNode} from 'react';
import { useEffect, useState } from 'react';
import { ArrowUp, ArrowUpRight, FolderOpen, Laptop, Plus, Search, Upload, Users, Bookmark, PanelLeft, PanelRight, Minus, Square, X } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { isDesktop } from './api';
import {useZoom} from './zoom';

export function Titlebar({toggle, dock, onDock, onImport, onHelp, onTheme, busy=false}: {
  toggle:()=>void; dock:boolean; onDock:()=>void; onImport?:()=>void; onHelp:()=>void; onTheme:()=>void;
  busy?:boolean;
}) {
  const [menu,setMenu]=useState<string|null>(null);
  const {zoom,adjust}=useZoom();
  useEffect(()=>{ const close=(event:KeyboardEvent)=>{if(event.key==='Escape')setMenu(null)};document.addEventListener('keydown',close);return()=>document.removeEventListener('keydown',close)},[]);
  const actions:Record<string,[string,()=>void][]>={...(onImport?{File:[['导入会话文件',onImport] as [string,()=>void]]}:{}),View:[['显示 / 隐藏侧栏',toggle],[dock?'工作区停靠左侧':'工作区停靠右侧',onDock],['切换深浅色主题',onTheme],['放大界面 · Ctrl +',()=>adjust(1)],['缩小界面 · Ctrl −',()=>adjust(-1)],[`恢复 100% · 当前 ${Math.round(zoom*100)}%`,()=>adjust(0)]],Help:[['使用说明',onHelp]]};
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
  return <Titlebar busy dock={false} toggle={noop} onDock={noop} onHelp={noop} onTheme={noop}/>;
}

export function PreviewHome(){return <section className="reader-empty preview-home" aria-label="会话预览工作台"><div className="quiet-empty"><FolderOpen size={30}/><h2>选择会话开始阅读</h2></div></section>;}

export function PaneButton({right,onClick}:{right:boolean;onClick:()=>void}) {
  const Icon=right?PanelLeft:PanelRight;
  return <button className="icon-button" aria-label={right?'将工作区移至左侧':'将工作区移至右侧'} title={right?'工作区停靠左侧':'工作区停靠右侧'} onClick={onClick}><Icon size={17}/></button>;
}
