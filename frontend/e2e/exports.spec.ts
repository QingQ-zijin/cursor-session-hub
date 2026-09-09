import { test, expect } from '@playwright/test';

for (const [format, extension, mime, label] of [['markdown','md','text/markdown','Markdown'],['html','html','text/html','HTML'],['pdf','pdf','application/pdf','PDF']]) {
  test(`${label} download uses the actual extension and save type`, async ({page}) => {
    await page.addInitScript(() => {
      (window as any).showSaveFilePicker = async (options: any) => {
        (window as any).savedOptions = options;
        return {createWritable: async () => new WritableStream({write() {}})};
      };
    });
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/download')) return route.fulfill({body:'file-content',contentType:mime});
      let value: any = {items:[],next_cursor:null};
      if (path.endsWith('/capabilities')) value={mode:'local'};
      else if (path.endsWith('/remote/me')) value={user:null};
      else if (path.endsWith('/activity')) return route.fulfill({contentType:'text/event-stream',body:'data: {"kind":"heartbeat"}\n\n'});
      else if (path.endsWith('/jobs')) value={items:[{id:'export-job',kind:'export',state:'succeeded',progress:2,total:2,export_format:format,download_filename:'研究结论.'+extension}]};
      await route.fulfill({json:value});
    });
    await page.goto('/?token=local-test-token');
    await page.getByRole('button',{name:'同步任务',exact:true}).click();
    await page.getByRole('button',{name:'下载 '+label,exact:true}).click();
    await expect.poll(()=>page.evaluate(()=>(window as any).savedOptions?.suggestedName)).toBe('研究结论.'+extension);
    const options=await page.evaluate(()=>(window as any).savedOptions);
    expect(options.types[0].accept[mime]).toEqual(['.'+extension]);
  });
}
