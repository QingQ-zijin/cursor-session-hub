import {test,expect,type Page} from '@playwright/test';
import fs from 'node:fs';import path from 'node:path';
async function setup(page:Page){
  const session={id:'math',title:'Equation review',project:'Research',owner_id:'local',current_revision:'r',round_count:1};
  await page.route('**/api/v1/**',async route=>{const p=new URL(route.request().url()).pathname;let data:any={items:[],next_cursor:null};
    if(p.endsWith('/capabilities'))data={mode:'local'};
    else if(p.endsWith('/remote/me'))data={user:null};
    else if(p.endsWith('/activity'))return route.fulfill({contentType:'text/event-stream',body:'data: {"kind":"heartbeat"}\n\n'});
    else if(p.endsWith('/updates'))data={status:'ok',available:false};
    else if(p.endsWith('/sessions'))data={items:[session]};
    else if(p.endsWith('/rounds'))data={items:[{number:1,preview:'Review formulas',count:1}]};
    else if(p.endsWith('/events'))data={items:[{id:'a',seq:1,event:{kind:'assistant',blocks:[{type:'text',text:String.raw`行内 \(x^2+y^2=z^2\)，标准 $E=mc^2$。

\[\frac{a}{b}+\sqrt{c}\]

[ z \sim \mathcal{N}(0,1) ]

`+'```tex\n'+String.raw`\[literal_code\]`+'\n```\n\n[link](https://example.com)'}]}}]};
    else if(p.endsWith('/sources/workspaces'))data={items:[{project:'Research',count:3},{project:'Project notes',count:1}]};
    else if(p.endsWith('/sources'))data={items:[{id:'a',title:'Review model results',project:'Research',source_kind:'cursor_ide',status:'indexed'},{id:'b',title:'Plan the next experiment',project:'Research',source_kind:'cursor_ide',status:'changed'},{id:'c',title:'未命名 Cursor 会话',project:'Research',source_kind:'cursor_ide'},{id:'d',title:'Prepare a report',project:'Project notes',source_kind:'cursor_ide',status:'indexed'}]};
    else if(p.endsWith('/source-batches'))data={items:[{id:'batch',project:'Research',state:'indexing',completed:12,total:16,failed:0,current_title:'Review model results',destination:'local'}]};
    await route.fulfill({json:data});});
}
const folder=path.resolve('../.runtime/visual-155');
test('LaTeX and dollar formulas render, code stays literal, no passive reader copy',async({page})=>{
  fs.mkdirSync(folder,{recursive:true});await setup(page);await page.setViewportSize({width:1440,height:950});await page.goto('/');await page.locator('.session-main').click();
  await expect(page.locator('.katex')).toHaveCount(4);await expect(page.locator('.katex-error')).toHaveCount(0);await expect(page.locator('.katex-display')).toHaveCount(2);
  await expect(page.locator('.code-frame')).toContainText(String.raw`\[literal_code\]`);await expect(page.locator('.code-frame .katex')).toHaveCount(0);await expect(page.getByRole('link',{name:'link',exact:true})).toHaveAttribute('href','https://example.com');
  await expect(page.locator('.reader-footnote')).toHaveCount(0);await page.screenshot({path:path.join(folder,'math.png'),animations:'disabled'});
});
test('compact import dialog distinguishes state and selection without explanatory paragraphs',async({page})=>{
  fs.mkdirSync(folder,{recursive:true});await setup(page);await page.setViewportSize({width:1440,height:1000});await page.goto('/');await page.getByRole('button',{name:'本机记录',exact:true}).click();const modal=page.getByRole('dialog',{name:'导入会话'});
  await expect(modal.locator('.source-row:visible')).toHaveCount(3);await expect(modal.locator('.modal-copy')).toHaveCount(0);await expect(modal.getByRole('button',{name:'发现记录',exact:true})).toHaveCount(1);await expect(modal.getByLabel('刷新来源列表')).toHaveCount(0);
  const indexed=await modal.locator('.source-status[data-state=indexed]').first().evaluate(el=>getComputedStyle(el).color),changed=await modal.locator('.source-status[data-state=changed]').evaluate(el=>getComputedStyle(el).color);expect(indexed).not.toBe(changed);
  await modal.locator('.source-row').filter({hasText:'Plan the next experiment'}).locator('input').check();await expect(modal.locator('.source-row.selected')).toHaveCount(1);await expect(modal.getByRole('button',{name:'导入所选',exact:true})).toBeEnabled();
  await expect(modal.getByText(/选择需要阅读|记录只读访问|入库任务按顺序/)).toHaveCount(0);await expect(modal.locator('.workspace-filter').getByLabel('工作区同步目标')).toBeVisible();
  await expect(modal.locator('.modal-footer')).toBeInViewport();await page.screenshot({path:path.join(folder,'import-light.png'),animations:'disabled'});
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:path.join(folder,'import-dark.png'),animations:'disabled'});
  await page.setViewportSize({width:390,height:844});await expect(modal.locator('.modal-footer')).toBeInViewport();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();await page.screenshot({path:path.join(folder,'import-mobile.png'),animations:'disabled'});
});
