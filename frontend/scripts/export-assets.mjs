import { cp, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'node_modules/katex/dist');
const target = path.join(root, 'public/export');
await mkdir(target, {recursive: true});
for (const name of ['katex.min.css', 'katex.min.js', 'fonts'])
  await cp(path.join(source, name), path.join(target, name), {recursive: true});
await cp(path.join(source, 'contrib/auto-render.min.js'), path.join(target, 'auto-render.min.js'));
