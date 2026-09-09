import { test, expect } from '@playwright/test';

test('expanded rounds load every event, every block and complete long text automatically', async ({page}) => {
  const requested: number[] = [];
  const session = {id:'complete', title:'完整对话', current_revision:'rev', owner_id:'local', round_count:5};
  const rows = Array.from({length:45}, (_, i) => ({id:'event-'+i, seq:i+1, round_number:3, event:{kind:i ? 'assistant':'user', text:'消息 '+i}}));
  rows[40].event = {kind:'assistant', text:'开头' + 'a'.repeat(25000) + '长文本结尾'};
  rows[41].event = {kind:'assistant', blocks:Array.from({length:31}, (_, i) => ({type:'text', text:'内容块 '+i}))} as any;
  rows[42].event = {kind:'assistant', blocks:[{type:'text', text:'预览', content_id:'long', has_more:true}]} as any;
  rows[43].event = {kind:'assistant', text:'Large message', content_id:'whole', _full_content_id:'whole'} as any;
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url()); let data: any = {items:[], next_cursor:null};
    if (url.pathname.endsWith('/capabilities')) data = {mode:'local'};
    else if (url.pathname.endsWith('/remote/me')) data = {user:null};
    else if (url.pathname.endsWith('/activity')) return route.fulfill({contentType:'text/event-stream',body:'data: {"kind":"heartbeat"}\n\n'});
    else if (url.pathname.endsWith('/sessions')) data={items:[session]};
    else if (url.pathname.endsWith('/sessions/complete')) data=session;
    else if (url.pathname.endsWith('/rounds')) data={items:[3,4,5].map(number=>({number,start_seq:1,end_seq:45,count:45,preview:'第'+number+'轮'}))};
    else if (url.pathname.endsWith('/events')) {
      const round=Number(url.searchParams.get('round')); requested.push(round);
      data=round===3 ? url.searchParams.get('cursor') ? {items:rows.slice(40),next_cursor:null} : {items:rows.slice(0,40),next_cursor:'40'} : {items:[]};
    } else if (url.pathname.endsWith('/contents/long')) data=url.searchParams.get('cursor') ? {text:'第二部分完整结尾', next_cursor:null} : {text:'第一部分\n\n', next_cursor:'100'};
    else if (url.pathname.endsWith('/contents/whole')) data={text:JSON.stringify({kind:'assistant',blocks:[{type:'text',text:'结构化消息完整结尾'}]}),next_cursor:null};
    await route.fulfill({json:data});
  });
  await page.goto('/#scope=local&session=complete');
  await expect(page.locator('.round-content')).toHaveCount(3);
  await expect(page.locator('#event-event-44')).toContainText('消息 44');
  await expect(page.locator('#event-event-40')).toContainText('长文本结尾');
  await expect(page.locator('#event-event-41')).toContainText('内容块 30');
  await expect(page.locator('#event-event-42')).toContainText('第一部分');
  await expect(page.locator('#event-event-42')).toContainText('第二部分完整结尾');
  await expect(page.locator('#event-event-43')).toContainText('结构化消息完整结尾');
  await expect(page.locator('#event-event-43')).not.toContainText('"blocks"');
  expect(new Set(requested)).toEqual(new Set([3,4,5]));
  await expect(page.getByRole('button',{name:/下一段|继续加载本轮内容|下一组内容/})).toHaveCount(0);
});

test('Cursor discovery groups by workspace and shows native title instead of IDs', async ({page}) => {
  const rows=[{id:'1',native_id:'abc-123-long-id',title:'Cursor 中的原始标题',project:'D:/Project A',source_kind:'cursor_ide'},
              {id:'2',native_id:'def-456-long-id',title:'第二个工作区会话',project:'D:/Project B',source_kind:'cursor_jsonl'}];
  await page.route('**/api/v1/**', async route => {
    const url=new URL(route.request().url());let data:any={items:[],next_cursor:null};
    if(url.pathname.endsWith('/capabilities'))data={mode:'local'};
    else if(url.pathname.endsWith('/remote/me'))data={user:null};
    else if(url.pathname.endsWith('/activity'))return route.fulfill({contentType:'text/event-stream',body:'data: {"kind":"heartbeat"}\n\n'});
    else if(url.pathname.endsWith('/sources/workspaces'))data={items:rows.map(r=>({project:r.project,count:1}))};
    else if(url.pathname.endsWith('/sources'))data={items:rows.filter(r=>!url.searchParams.has('project')||r.project===url.searchParams.get('project'))};
    await route.fulfill({json:data});
  });
  await page.goto('/');await page.getByRole('button',{name:'本机记录',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await expect(dialog.locator('.workspace-group')).toHaveCount(2);
  await expect(dialog.getByText('Cursor 中的原始标题',{exact:true})).toBeVisible();
  await expect(dialog.getByText('ID：abc-123-long-id',{exact:true})).not.toBeVisible();
  await dialog.getByRole('combobox',{name:'选择 Cursor 工作区'}).selectOption('D:/Project B');
  await expect(dialog.locator('.source-row')).toHaveCount(1);
  await expect(dialog.locator('.source-row')).toContainText('第二个工作区会话');
});
