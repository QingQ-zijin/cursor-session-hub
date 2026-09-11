import {test,expect} from '@playwright/test';
import path from 'node:path';import fs from 'node:fs';
test('workspace/all buttons are not page limited, unnamed rows collapse, final answer stays visible',async({page})=>{
  const starts:any[]=[];
  const session={id:'s',title:'Final answer preview',project:'Research',owner_id:'local',current_revision:'r',round_count:1};
  await page.route('**/api/v1/**',async route=>{
    const url=new URL(route.request().url()),p=url.pathname;let data:any={items:[],next_cursor:null};
    if(p.endsWith('/capabilities'))data={mode:'local'};
    else if(p.endsWith('/remote/me'))data={user:null};
    else if(p.endsWith('/activity'))return route.fulfill({contentType:'text/event-stream',body:'data: {"kind":"heartbeat"}\n\n'});
    else if(p.endsWith('/updates'))data={status:'ok',available:false};
    else if(p.endsWith('/sessions'))data={items:[session,{...session,id:'unnamed',title:'未命名 Cursor 会话'}]};
    else if(p.endsWith('/sources/workspaces'))data={items:[{project:'Research',count:120}]};
    else if(p.endsWith('/sources'))data={items:[{id:'named-source',title:'Cursor native name',project:'Research',source_kind:'cursor_ide'},{id:'empty-source',title:'未命名 Cursor 会话',project:'Research',source_kind:'cursor_ide'}],next_cursor:'more-sources'};
    else if(p.endsWith('/source-batches')&&route.request().method()==='POST'){starts.push(route.request().postDataJSON());data={id:'batch',state:'planning'}}
    else if(p.endsWith('/rounds'))data={items:[{number:1,preview:'Review changes',count:4}]};
    else if(p.endsWith('/events'))data={items:[{id:'u',seq:1,event:{kind:'user',text:'Check the work'}},{id:'draft',seq:2,event:{kind:'assistant',text:'INTERMEDIATE_COMMENTARY'}},{id:'tool',seq:3,event:{kind:'tool',name:'Shell',input:{command:'check'}}},{id:'a',seq:4,event:{kind:'assistant',blocks:[{type:'text',text:'FINAL_ANSWER_COMPLETE'}]}}]};
    await route.fulfill({json:data});
  });
  await page.setViewportSize({width:1440,height:900});await page.goto('/');
  await expect(page.locator('.session-main:visible')).toHaveCount(1);
  await page.getByRole('button',{name:'同步工作区 Research',exact:true}).click();expect(starts[0]).toEqual({project:'Research',destination:'local'});
  const dialog=page.getByRole('dialog');await expect(dialog.getByText('Cursor native name',{exact:true})).toBeVisible();await expect(dialog.getByText('未命名 Cursor 会话',{exact:true})).not.toBeVisible();
  await dialog.locator('.unnamed-sources > summary').click();await expect(dialog.getByText('未命名 Cursor 会话',{exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'同步全部工作区',exact:true}).click();expect(starts[1]).toEqual({project:null,destination:'local'});
  await dialog.getByLabel('工作区同步目标').selectOption('server');await dialog.getByRole('button',{name:'同步整个工作区',exact:true}).click();expect(starts[2]).toEqual({project:'Research',destination:'server'});
  await dialog.getByRole('button',{name:'关闭对话框',exact:true}).click();await page.locator('.session-main:visible').click();
  await expect(page.getByText('FINAL_ANSWER_COMPLETE',{exact:true})).toBeVisible();await expect(page.getByText('INTERMEDIATE_COMMENTARY',{exact:true})).toHaveCount(0);
  const output=path.resolve('../.runtime/workspace-153');fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'final-answer.png'),animations:'disabled'});
  await page.locator('.execution-summary>.disclosure>button').click();await expect(page.getByText('INTERMEDIATE_COMMENTARY',{exact:true})).toBeVisible();
});
