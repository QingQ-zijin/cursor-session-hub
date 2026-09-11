import {describe,it,expect} from 'vitest';
import {readingGroups} from './reader-model';
import type {EventRecord} from './types';
const row=(id:string,event:Record<string,unknown>)=>({id,event,seq:1,round_number:1}) as EventRecord;
describe('final answer focus',()=>{
  it('keeps user and every final message, folds intermediate commentary',()=>{
    const groups=readingGroups([row('u',{kind:'user',text:'ask'}),row('draft',{kind:'assistant',text:'let me inspect'}),row('tool',{kind:'tool'}),row('a',{kind:'assistant',text:'final part 1'}),row('b',{kind:'assistant',text:'final part 2'})]);
    expect(groups.filter(g=>!g.process).flatMap(g=>g.items.map(i=>i.id))).toEqual(['u','a','b']);
    expect(groups.filter(g=>g.process).flatMap(g=>g.items.map(i=>i.id))).toEqual(['draft','tool']);
  });
  it('splits mixed text/tool/final blocks without mutating the original',()=>{
    const blocks=[{type:'text',text:'draft'},{type:'tool_use',name:'Shell'},{type:'text',text:'final'}];
    const groups=readingGroups([row('a',{kind:'assistant',blocks})]);expect(groups).toHaveLength(2);expect(groups[1].items[0].event.blocks).toEqual([blocks[2]]);expect(blocks).toHaveLength(3);
  });
  it('does not guess final boundaries for pure text, or invent a final answer after tools',()=>{
    expect(readingGroups([row('a',{kind:'assistant',text:'first'}),row('b',{kind:'assistant',text:'second'})]).every(g=>!g.process)).toBe(true);
    expect(readingGroups([row('a',{kind:'assistant',text:'working'}),row('b',{kind:'tool'})]).every(g=>g.process)).toBe(true);
  });
});
