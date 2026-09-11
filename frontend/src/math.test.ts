import {it,expect} from 'vitest';
import fs from 'node:fs';
import {normalizeMath} from './math';
const cases=JSON.parse(fs.readFileSync('../tests/fixtures/math-delimiters.json','utf8'));
for(const item of cases)it(item.name,()=>expect(normalizeMath(item.source)).toBe(item.expected));
