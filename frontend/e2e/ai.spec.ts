import {test,expect,type Page} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function setup(page:Page){
  const sessions=[{id:'a',title:'Review model evaluation',project:'research_workspace'},{id:'b',title:'Plan next experiments',project:'research_workspace'}].map(s=>({...s,current_revision:'rev-'+s.id,owner_id:'local',round_count:1,updated_at:Date.now()/1000}));
  let config={enabled:true,configured:true,base_url:'https://provider.example/v1',model:'Team Model',context_chars:4000,max_output_tokens:2048,version:1,scope:'local'};
  const sends:any[]=[];let replyCalls=0,onceOffline=true;
  await page.route('**/api/v1/**',async route=>{
    const url=new URL(route.request().url()),p=url.pathname;let value:any={items:[],next_cursor:null};
    if(p.endsWith('/capabilities'))value={mode:'local'};
    else if(p.endsWith('/remote/me'))value={user:null};
    else if(p.endsWith('/activity'))return route.fulfill({contentType:'text/event-stream',body:'data: {"kind":"heartbeat"}\n\n'});
    else if(p.endsWith('/updates'))value={status:'ok',available:false};
    else if(p.endsWith('/ai/settings')){if(route.request().method()==='PATCH'){config={...config,...route.request().postDataJSON(),version:2};delete (config as any).api_key}value=config}
    else if(p.endsWith('/ai/context'))value={references:route.request().postDataJSON().references.map((r:any)=>({...r,title:sessions.find(s=>s.id===r.session_id)?.title,events:2,total_events:10,partial:true})),settings_version:config.version,destination:config.base_url,model:config.model};
    else if(p.endsWith('/ai/messages')){const input=route.request().postDataJSON();sends.push(input);value={thread_id:'chat-1',user:{id:'u1',seq:1,role:'user',text:input.text,state:'complete'},assistant:{id:'m1',seq:2,role:'assistant',text:'',state:'queued'}}}
    else if(p.endsWith('/ai/replies/m1')){replyCalls++;if(url.searchParams.get('offset')==='0')value={text:'根据历史😀，',next_offset:6,state:'running',has_more:false};else if(onceOffline){onceOffline=false;return route.fulfill({status:503,json:{detail:'temporary offline'}})}else value={text:'**下一步**进行复核。\n\n```python\nprint("ready")\n```',next_offset:60,state:'complete',has_more:false}}
    else if(p.endsWith('/sessions'))value={items:sessions};
    else if(p.endsWith('/rounds'))value={items:[{number:1,preview:'Evaluate results',count:2}]};
    else if(p.endsWith('/events'))value={items:[{id:'old-u',seq:1,event:{kind:'user',text:'Review our evaluation results.'}},{id:'old-a',seq:2,event:{kind:'assistant',blocks:[{type:'text',text:'The evaluation completed. Review the report before the next experiment.'},{type:'tool_use',name:'Shell',input:{command:'python evaluate.py --report'},result:{text:'Completed with 12 checks.'}}]}}]};
    await route.fulfill({json:value});
  });
  return {sessions,sends,getReplyCalls:()=>replyCalls};
}

test('API chat sends selected context once, reconnects with Unicode offset, and exposes admin settings',async({page})=>{
  const state=await setup(page);await page.setViewportSize({width:1440,height:1000});await page.goto('/');
  await page.locator('.session-main').filter({hasText:state.sessions[0].title}).click();
  await expect(page.locator('.ai-reference')).toContainText(state.sessions[0].title);
  await expect(page.locator('.tool-toggle')).toContainText('python evaluate.py');
  await page.getByLabel('聊天消息').fill('Compare @Plan');
  await page.getByRole('dialog',{name:'选择引用会话'}).getByRole('button',{name:/Plan next experiments/}).click();
  await expect(page.locator('.ai-reference')).toHaveCount(2);
  await page.getByLabel('聊天消息').fill('Compare the findings and suggest the next step.');
  await expect(page.getByLabel('发送消息')).toBeEnabled();await page.getByLabel('发送消息').click();
  await expect(page.getByRole('alert')).toContainText('正在重连');
  await expect(page.locator('.ai-messages')).toContainText('进行复核',{timeout:12000});
  expect(state.sends).toHaveLength(1);expect(state.sends[0].references.map((r:any)=>r.session_id)).toEqual(['a','b']);
  expect(state.getReplyCalls()).toBe(3);
  await expect(page.locator('.ai-messages')).toContainText('根据历史😀，');
  await expect(page.getByRole('alert')).toHaveCount(0);
  const shots=path.resolve('../.runtime/workbench-visual');fs.mkdirSync(shots,{recursive:true});
  await page.screenshot({animations:'disabled',path:path.join(shots,'chat-151.png')});
  await page.getByLabel('API 设置',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'API 设置'});
  await dialog.getByLabel('模型名称').fill('Configured Model');await dialog.getByRole('button',{name:'保存 API 配置'}).click();
  await expect(dialog.getByRole('status')).toContainText('已保存');
  await page.screenshot({animations:'disabled',path:path.join(shots,'api-settings-151.png')});
  // Native-only controls use the same stylesheet; exercise that rule without invoking Tauri.
  expect(await page.evaluate(()=>{const controls=document.createElement('div');controls.className='window-controls';document.querySelector('.workbench-titlebar')!.appendChild(controls);const color=getComputedStyle(controls).backgroundColor;controls.remove();return color})).not.toBe('rgb(21, 21, 21)');
});
