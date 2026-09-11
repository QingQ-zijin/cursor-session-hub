import {test,expect} from '@playwright/test';
import fs from 'node:fs';import path from 'node:path';
test('minimal reader, right directory, recoverable annotated trash, folded work and zoom',async({page})=>{
  const session={id:'s',title:'Review evaluation results',project:'research',owner_id:'local',current_revision:'r',round_count:6};
  const rounds=Array.from({length:6},(_,i)=>({number:i+1,preview:'实验检查 '+(i+1),count:5,deleted:false,note:''}));let contentReads=0;const requests:string[]=[];
  await page.route('**/api/v1/**',async route=>{
    const url=new URL(route.request().url()),p=url.pathname;requests.push(p);let data:any={items:[],next_cursor:null};
    if(p.endsWith('/capabilities'))data={mode:'local'};
    else if(p.endsWith('/remote/me'))data={user:null};
    else if(p.endsWith('/updates'))data={status:'ok',available:false};
    else if(p.endsWith('/activity'))return route.fulfill({contentType:'text/event-stream',body:'data: {"kind":"heartbeat"}\n\n'});
    else if(p.endsWith('/sessions'))data={items:[session]};
    else if(p.endsWith('/sessions/s'))data=session;
    else if(/\/rounds\/\d+$/.test(p)){const b=route.request().postDataJSON();Object.assign(rounds[Number(p.split('/').at(-1))-1],b);data={ok:true}}
    else if(p.endsWith('/rounds')){let rows=rounds.filter(r=>r.deleted===(url.searchParams.get('trash')==='true'));if(url.searchParams.has('recent'))rows=rows.slice(-3);data={items:rows}}
    else if(p.endsWith('/events')){const n=url.searchParams.get('round');data={items:[
      {id:'u'+n,seq:1,event:{kind:'user',text:'请核对实验结果 '+n}},
      {id:'t'+n,seq:2,event:{kind:'tool',name:'Shell',input:{command:'python evaluate.py'},result:{content_id:'tool-result',preview:'preview'},ts:'2026-09-11T00:00:00Z'}},
      {id:'t2'+n,seq:3,event:{kind:'tool',name:'Read',input:{path:'results.json'},result:{text:'passed'},ts:'2026-09-11T00:05:32Z'}},
      {id:'a'+n,seq:4,event:{kind:'assistant',blocks:[{type:'text',text:'完整结论：所有检查已通过。'}]}}
    ]}}
    else if(p.endsWith('/contents/tool-result')){contentReads++;data={text:'EXECUTION-DETAILS',next_cursor:null}}
    await route.fulfill({json:data});
  });
  await page.setViewportSize({width:1440,height:900});await page.goto('/');
  await expect(page.getByRole('button',{name:'Search',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'New Chat',exact:true})).toHaveCount(0);
  await page.locator('.session-main').click();await expect(page.locator('.round-content')).toHaveCount(3);
  const rail=page.getByRole('complementary',{name:'轮次目录'});await expect(rail).toBeVisible();
  expect((await rail.boundingBox())!.x).toBeGreaterThan((await page.locator('.transcript-scroll').boundingBox())!.x);
  expect(contentReads).toBe(0);await expect(page.getByRole('button',{name:'Worked for 5m 32s · 2 条记录',exact:true})).toHaveCount(3);
  await page.getByRole('button',{name:'Worked for 5m 32s · 2 条记录',exact:true}).first().click();await page.locator('.tool-toggle').first().click();await expect(page.getByText('EXECUTION-DETAILS',{exact:true})).toBeVisible();expect(contentReads).toBe(1);
  await rail.getByRole('button',{name:'删除第 5 轮',exact:true}).click();const modal=page.getByRole('dialog',{name:'删除第 5 轮'});await modal.getByLabel('删除说明').fill('重复尝试，暂时隐藏');await modal.getByRole('button',{name:'移到回收站',exact:true}).click();
  await expect(rail.locator('.rail-round')).toHaveCount(5);await expect(page.locator('#round-5')).toHaveCount(0);
  await rail.getByRole('button',{name:'回收站',exact:true}).click();await expect(rail).toContainText('重复尝试，暂时隐藏');
  await rail.getByRole('button',{name:'恢复或备注第 5 轮',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'恢复轮次',exact:true}).click();await expect(rail).toContainText('回收站为空');
  await rail.getByRole('button',{name:'更早的会话',exact:true}).click();await expect(rail.locator('.rail-round')).toHaveCount(6);
  await rail.getByRole('button',{name:'01 实验检查 1',exact:true}).click();await expect(page.locator('#round-1 .round-content')).toBeVisible();await expect(page.locator('.round-content')).toHaveCount(3);
  await page.keyboard.press('Control+=');await expect(page.locator('html')).toHaveCSS('zoom','1.1');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.keyboard.press('Control+-');await expect(page.locator('html')).toHaveCSS('zoom','1');
  await page.keyboard.press('Control+=');await page.reload();await expect(page.locator('html')).toHaveCSS('zoom','1.1');
  await page.keyboard.press('Control+0');await expect(page.locator('html')).toHaveCSS('zoom','1');
  expect(requests.some(p=>p.includes('/ai/'))).toBeFalsy();await expect(page.getByLabel('同步所选会话')).toHaveCount(1);
  await expect(page.locator('.round-content')).toHaveCount(3);await expect(rail.locator('.rail-round')).toHaveCount(6);await expect(page.locator('.round-content').first()).toHaveAttribute('aria-busy','false');
  const folder=path.resolve('../.runtime/reader-152');fs.mkdirSync(folder,{recursive:true});await page.screenshot({path:path.join(folder,'reader.png'),animations:'disabled'});
});
