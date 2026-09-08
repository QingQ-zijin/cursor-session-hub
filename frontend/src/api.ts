import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import type { Page } from './types';

declare global {interface Window {__TAURI_INTERNALS__?:unknown}}
export const isDesktop = () => !!window.__TAURI_INTERNALS__;
export class ApiError extends Error {constructor(public status:number,message:string){super(message)}}
let localBase = '';
let localToken = new URLSearchParams(location.search).get('token') || '';
export async function initializeTransport(){
  if(localToken){sessionStorage.setItem('csh-local-token',localToken);const clean=new URL(location.href);clean.searchParams.delete('token');history.replaceState(null,'',clean)}
  else localToken=sessionStorage.getItem('csh-local-token')||'';
}
export function urlFor(path:string){return localBase+'/api/v1'+path}
export async function request<T>(path:string,options:RequestInit={}):Promise<T>{
  const method=options.method||'GET';
  if(isDesktop() && !(options.body instanceof FormData) && !options.signal && !localBase){
    return invoke<T>('native_bridge',{method,path:'/api/v1'+path,body:options.body?JSON.parse(String(options.body)):null});
  }
  const headers = new Headers(options.headers);
  if(localToken) headers.set('Authorization','Bearer '+localToken);
  if(options.body && !(options.body instanceof FormData)) headers.set('Content-Type','application/json');
  const res=await fetch(urlFor(path),{...options,headers,credentials:'include'});
  if(!res.ok){let detail=res.statusText;try{const d=await res.json();detail=typeof d.detail==='string'?d.detail:JSON.stringify(d.detail)}catch{}throw new ApiError(res.status,detail)}
  if(res.status===204)return undefined as T;
  return res.json();
}
export const post=<T=unknown>(path:string,data:unknown={})=>request<T>(path,{method:'POST',body:JSON.stringify(data)});
export const patch=<T=unknown>(path:string,data:unknown)=>request<T>(path,{method:'PATCH',body:JSON.stringify(data)});
export const del=(path:string)=>request(path,{method:'DELETE'});
export function query(values:Record<string,string|number|boolean|null|undefined>){const q=new URLSearchParams();Object.entries(values).forEach(([k,v])=>{if(v!==undefined&&v!==null&&v!=='')q.set(k,String(v))});return q.size?'?'+q:''}
export async function chooseFiles():Promise<string[]>{return invoke<string[]>('choose_files')}
export async function download(path:string,filename:string){
  if(isDesktop()){await invoke('save_download',{path:'/api/v1'+path,filename});return;}
  if(!localToken){const a=document.createElement('a');a.href=urlFor(path);a.download='';a.click();return;}
  const savePicker=(window as unknown as {showSaveFilePicker?:(options:unknown)=>Promise<{createWritable:()=>Promise<WritableStream>}>}).showSaveFilePicker;
  const handle=savePicker?await savePicker({suggestedName:filename}):undefined;
  const headers=new Headers();if(localToken) headers.set('Authorization','Bearer '+localToken);
  const res=await fetch(urlFor(path),{credentials:'include',headers});if(!res.ok)throw new ApiError(res.status,'下载失败，请检查访问权限');
  if(handle&&res.body){await res.body.pipeTo(await handle.createWritable());return;}
  if(Number(res.headers.get('content-length')||Infinity)>32*1024*1024){await res.body?.cancel();throw new Error('大文件请通过桌面客户端导出，或使用支持直接保存文件的浏览器。')}
  const blob=await res.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function assetUrl(path:string){return urlFor(path)+(localToken?(path.includes('?')?'&':'?')+'token='+encodeURIComponent(localToken):'')}
export async function fetchAsset(path:string){
  if(isDesktop())return invoke<string>('native_asset',{path:'/api/v1'+path});
  const headers=new Headers();if(localToken)headers.set('Authorization','Bearer '+localToken);
  const res=await fetch(urlFor(path),{credentials:'include',headers});if(!res.ok)throw new Error('图片无法读取');
  return URL.createObjectURL(await res.blob());
}
export function subscribeActivity(onEvent:(event:unknown)=>void,onStatus?:(live:boolean)=>void,path='/activity'){
  if(isDesktop()){let disposed=false,stop:(()=>void)|undefined;void listen('hub-activity',event=>onEvent(event.payload)).then(f=>{if(disposed)f();else{stop=f;void invoke('subscribe_activity',{path:'/api/v1'+path}).then(()=>onStatus?.(true)).catch(()=>onStatus?.(false))}});return()=>{disposed=true;stop?.();void invoke('unsubscribe_activity').catch(()=>{})}}
  const controller=new AbortController();
  const run=async()=>{let backoff=1000;while(!controller.signal.aborted){try{
    const headers=new Headers({Accept:'text/event-stream'});if(localToken)headers.set('Authorization','Bearer '+localToken);
    const res=await fetch(urlFor(path),{credentials:'include',headers,signal:controller.signal});if(!res.ok||!res.body)throw Error('连接中断');
    onStatus?.(true);backoff=1000;const reader=res.body.getReader(),decoder=new TextDecoder();let buf='';
    while(!controller.signal.aborted){const value=await reader.read();if(value.done)break;buf+=decoder.decode(value.value,{stream:true});const parts=buf.split(/\r?\n\r?\n/);buf=parts.pop()||'';for(const part of parts){const data=part.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');if(data){try{onEvent(JSON.parse(data))}catch{}}}if(buf.length>65536)buf='';}
  }catch{if(controller.signal.aborted)break;}onStatus?.(false);await new Promise<void>(r=>{const timer=setTimeout(r,backoff);controller.signal.addEventListener('abort',()=>{clearTimeout(timer);r()},{once:true})});backoff=Math.min(backoff*2,30000)}};
  void run();return()=>controller.abort();
}
export const asPage=<T>(value:Page<T>|T[]):Page<T>=>Array.isArray(value)?{items:value,next_cursor:null}:value;
export type Client={get:<T>(path:string)=>Promise<T>;post:<T=unknown>(path:string,data?:unknown)=>Promise<T>;patch:<T=unknown>(path:string,data:unknown)=>Promise<T>;del:(path:string)=>Promise<unknown>;path:(path:string)=>string};
export function client(remote=false):Client{const path=(p:string)=>(remote?'/remote/api':'')+p;return{get:<T>(p:string)=>request<T>(path(p)),post:<T=unknown>(p:string,d:unknown={})=>post<T>(path(p),d),patch:<T=unknown>(p:string,d:unknown)=>patch<T>(path(p),d),del:p=>del(path(p)),path}}
