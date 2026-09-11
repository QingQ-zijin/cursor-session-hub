import type {EventRecord} from './types';
export type ReadingGroup={process:boolean;items:EventRecord[]};
const execution=new Set(['tool','web_call','web_search']);
export function readingGroups(items:EventRecord[]):ReadingGroup[]{
  let lastEvent=-1,lastBlock=-1;
  items.forEach((item,i)=>{
    if(execution.has(String(item.event.kind))){lastEvent=i;lastBlock=-1}
    const blocks=item.event.blocks as Record<string,unknown>[]|undefined;
    blocks?.forEach((block,j)=>{if(block.type==='tool_use'){lastEvent=i;lastBlock=j}});
  });
  const groups:ReadingGroup[]=[];
  function add(item:EventRecord,process:boolean){const previous=groups.at(-1);if(process&&previous?.process)previous.items.push(item);else groups.push({process,items:[item]})}
  items.forEach((item,i)=>{
    const kind=String(item.event.kind),blocks=item.event.blocks as Record<string,unknown>[]|undefined;
    if(kind==='user'){add(item,false);return}
    if(i===lastEvent&&lastBlock>=0&&blocks){
      add({...item,event:{...item.event,blocks:blocks.slice(0,lastBlock+1)}},true);
      if(lastBlock+1<blocks.length)add({...item,event:{...item.event,blocks:blocks.slice(lastBlock+1)}},false);
      return;
    }
    const process=execution.has(kind)||kind==='reasoning'||kind==='status'||(kind==='assistant'&&i<lastEvent)||(kind==='assistant'&&!!blocks?.length&&blocks.every(b=>['tool_use','thinking'].includes(String(b.type))));
    add(item,process);
  });return groups;
}
