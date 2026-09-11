import {test,expect} from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';

test('Cursor-style workspace tree, tabs, side docking and responsive previews',async({page})=>{
  const shots=path.resolve('../.runtime/workbench-visual');fs.mkdirSync(shots,{recursive:true});
  const sessions=[
    {id:'a',title:'Review model evaluation results',project:'research_workspace'},
    {id:'b',title:'Virtual cell world model criteria',project:'research_workspace'},
    {id:'c',title:'Minecraft modpack server',project:'minecraft'},
    {id:'d',title:'Prepare the experiment report',project:'paper_typesetting'},
    {id:'e',title:'Inspect the parser output',project:'session_hub'},
  ].map(s=>({...s,current_revision:'rev',owner_id:'local',source_kind:'cursor_ide',round_count:3,updated_at:Date.now()/1000-172800}));
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/v1/**',async route=>{
    const url=new URL(route.request().url());let data:any={items:[],next_cursor:null};
    if(url.pathname.endsWith('/capabilities'))data={mode:'local'};
    else if(url.pathname.endsWith('/remote/me'))data={user:null};
    else if(url.pathname.endsWith('/activity'))return route.fulfill({contentType:'text/event-stream',body:'data: {"kind":"heartbeat"}\n\n'});
    else if(url.pathname.endsWith('/updates'))data={status:'ok',available:false};
    else if(url.pathname.endsWith('/sessions'))data={items:sessions};
    else if(url.pathname.endsWith('/rounds'))data={items:[{number:1,preview:'Check evaluation results',count:2}]};
    else if(url.pathname.endsWith('/events'))data={items:[{id:'u',seq:1,round_number:1,event:{kind:'user',text:'请检查这次实验结果，并整理主要结论。'}},{id:'a',seq:2,round_number:1,event:{kind:'assistant',blocks:[{type:'text',text:'## Evaluation results\n\nThe complete conversation remains available in this preview.\n\n- **Coverage:** all recorded messages\n- **Reading:** three rounds at a time\n\n```python\nresults = evaluate(model, dataset)\nprint(results.summary())\n```\n\n模型计算：$y = Wx + b$。'},{type:'tool_use',name:'read_file',input:{path:'results/evaluation.json'},result:{text:'All evaluations passed.'}}]}}]};
    await route.fulfill({json:data});
  });
  await page.setViewportSize({width:1440,height:900});await page.goto('/');
  await expect(page.locator('.session-main')).toHaveCount(5);
  await expect(page.locator('.preview-home')).toBeVisible();
  const tree=await page.locator('.sidebar').boundingBox(), main=await page.locator('.workspace').boundingBox();
  expect(tree!.width).toBeLessThan(330);expect(main!.width).toBeGreaterThan(1000);
  await page.screenshot({animations:'disabled',path:path.join(shots,'home.png')});
  await page.getByRole('button',{name:'research_workspace',exact:true}).click();
  await expect(page.locator('.session-row:visible')).toHaveCount(3);
  await page.getByRole('button',{name:'research_workspace',exact:true}).click();
  await page.locator('.session-main').filter({hasText:sessions[0].title}).click();
  await expect(page.locator('.code-frame')).toBeVisible();
  await expect(page.locator('.katex')).toHaveCount(1);
  await page.locator('.session-main').filter({hasText:sessions[1].title}).click();
  await expect(page.getByRole('tab')).toHaveCount(2);
  await page.getByRole('tab',{name:sessions[0].title}).click();
  await expect(page.getByRole('tab',{name:sessions[0].title})).toHaveAttribute('aria-selected','true');
  await page.screenshot({animations:'disabled',path:path.join(shots,'reader.png')});
  await page.getByRole('button',{name:'将工作区移至右侧'}).click();
  expect((await page.locator('.sidebar').boundingBox())!.x).toBeGreaterThan(1000);
  await page.screenshot({animations:'disabled',path:path.join(shots,'right-dock.png')});
  await page.getByRole('button',{name:'将工作区移至左侧'}).click();
  await page.keyboard.press('Control+b');await expect(page.locator('.sidebar')).not.toBeVisible();
  await page.keyboard.press('Control+b');await expect(page.locator('.sidebar')).toBeVisible();
  await expect(page.getByLabel('搜索会话与正文')).toHaveCount(0);
  await page.getByRole('button',{name:'View',exact:true}).click();await page.getByRole('menuitem',{name:'切换深浅色主题'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await page.screenshot({animations:'disabled',path:path.join(shots,'dark-reader.png')});
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'展开主菜单',exact:true}).click();
  await expect(page.locator('.sidebar')).toBeInViewport();
  await page.screenshot({animations:'disabled',path:path.join(shots,'mobile.png')});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});
