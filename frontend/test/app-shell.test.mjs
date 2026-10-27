import test from 'node:test';
import assert from 'node:assert/strict';
import { searchNavigation } from '../src/app-shell.js';

test('entry search filters real local routes without inventing result links', () => {
  const entries = [{ href: '/', label: '研究总览' }, { href: '/research/jobs', label: '研究任务' }, { href: '/knowledge', label: '知识学习' }];
  assert.deepEqual(searchNavigation(entries, '研究'), entries.slice(0, 2));
  assert.deepEqual(searchNavigation(entries, ' 知识 '), [entries[2]]);
  assert.deepEqual(searchNavigation(entries, '没有这个入口'), []);
});

test('entry search cannot turn a page entry into an external or executable URL', () => {
  const entries = [
    { href: 'https://untrusted.invalid', label: '研究' },
    { href: '//untrusted.invalid', label: '研究' },
    { href: 'javascript:alert(1)', label: '研究' },
    { href: '/\\untrusted.invalid', label: '研究' },
    { href: '/research/jobs', label: '研究任务' },
  ];
  assert.deepEqual(searchNavigation(entries, ''), [{ href: '/research/jobs', label: '研究任务' }]);
});

test('entry search is bounded and deduplicated even with repeated navigation', () => {
  const entry = { href: '/', label: '研究总览' };
  assert.deepEqual(searchNavigation([entry, entry, ...Array.from({ length: 30 }, (_, i) => ({ href: `/page-${i}`, label: '研究' }))], '' ).length, 12);
  assert.deepEqual(searchNavigation([entry, entry], ''), [entry]);
});
