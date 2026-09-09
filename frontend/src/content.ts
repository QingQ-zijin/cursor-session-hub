import type { Client } from './api';
import { query } from './api';

// Only two large-content reads at once, shared by the three expanded rounds.
let active = 0;
const waiting: (() => void)[] = [];
async function acquire() {
  if (active >= 2) await new Promise<void>(resolve => waiting.push(resolve));
  else active++;
}
function release() {
  const next = waiting.shift();
  if (next) next(); else active--;
}
export async function readContent(api: Client, id: string, alive: () => boolean, progress?: (bytes: number) => void) {
  const parts: string[] = [];
  let cursor: string | null = '';
  let size = 0;
  do {
    await acquire();
    try {
      if (!alive()) return null;
      const page: {text: string; next_cursor: string | null} = await api.get('/contents/' + encodeURIComponent(id) + query({cursor, limit: 262144}));
      if (!alive()) return null;
      if (page.next_cursor !== null && page.next_cursor === cursor) throw new Error('内容读取未能前进，请重试');
      parts.push(page.text);
      size += page.text.length;
      progress?.(size);
      cursor = page.next_cursor;
    } finally { release(); }
  } while (cursor !== null);
  return parts.join('');
}
