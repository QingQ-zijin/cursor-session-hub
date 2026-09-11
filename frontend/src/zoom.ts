import {useEffect,useState} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {isDesktop} from './api';
const levels=[0.67,0.8,0.9,1,1.1,1.25,1.5,1.75];
export function nextZoom(current:number,direction:number){return direction===0?1:levels[Math.max(0,Math.min(levels.length-1,levels.indexOf(current)+direction))]}
export function useZoom(){
  const [zoom,setZoom]=useState(()=>{const value=Number(localStorage.getItem('csh-interface-zoom'));return levels.includes(value)?value:1});
  useEffect(()=>{
    localStorage.setItem('csh-interface-zoom',String(zoom));
    if(isDesktop())void invoke('interface_zoom',{scale:zoom}).catch(()=>{});
    else {document.documentElement.style.zoom=String(zoom);document.documentElement.style.setProperty('--interface-zoom',String(zoom))}
  },[zoom]);
  useEffect(()=>{const listener=(event:KeyboardEvent)=>{
    if(!(event.ctrlKey||event.metaKey)||event.altKey||event.isComposing)return;
    const direction=['+','=','Add'].includes(event.key)?1:['-','_','Subtract'].includes(event.key)?-1:event.key==='0'?0:null;
    if(direction===null)return;event.preventDefault();setZoom(old=>nextZoom(old,direction));
  };document.addEventListener('keydown',listener);return()=>document.removeEventListener('keydown',listener)},[]);
  return {zoom,adjust:(direction:number)=>setZoom(old=>nextZoom(old,direction))};
}
