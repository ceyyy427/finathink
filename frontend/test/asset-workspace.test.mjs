import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeAssetPage, assetQuery } from '../src/asset-workspace.js';

const page = { schema_version: 'asset-workspace.v1', kind: 'methods', total: 1, limit: 20, offset: 0,
  items: [{ asset_id: 'method-example', title: '研究方法', kind: 'methods', availability: 'PACKAGED', loaded: false, tags: ['research'] }] };
test('asset data preserves source availability, not loaded execution status', () => {
  assert.deepEqual(normalizeAssetPage(page), page);
  for (const patch of [{ limit: 101 }, { total: -1 }, { items: [{ ...page.items[0], loaded: true }] },
    { items: [{ ...page.items[0], asset_id: '../outside' }] }]) assert.throws(() => normalizeAssetPage({ ...page, ...patch }));
});
test('query builder encodes values and does not accept execution fields', () => {
  const query = assetQuery({ kind: 'methods', q: '<script>', offset: 20 });
  assert.equal(new URLSearchParams(query).get('q'), '<script>');
  assert.throws(() => assetQuery({ kind: 'execute' }));
  assert.throws(() => assetQuery({ kind: 'methods', submit: true }));
});

test('source audit records share read-only filtering without becoming loaded methods', () => {
  const source = { ...page, kind: 'sources', items: [{ ...page.items[0], asset_id: 'source-qlib', kind: 'sources', version: 'a'.repeat(40) }] };
  assert.deepEqual(normalizeAssetPage(source), source);
  assert.equal(new URLSearchParams(assetQuery({ kind: 'sources', q: 'Qlib' })).get('kind'), 'sources');
  assert.throws(() => assetQuery({ kind: 'sources', approve: true }));
  assert.throws(() => normalizeAssetPage({ ...source, items: [{ ...source.items[0], version: null }] }));
});
