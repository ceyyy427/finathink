import test from 'node:test';
import assert from 'node:assert/strict';
import { latestOperation, safeWorkspaceHref } from '../src/quant-workspace.js';

test('changing the selection revokes both late success and late errors', () => {
  const selection = latestOperation();
  const old = selection.next();
  const fresh = selection.next();
  assert.equal(old.signal.aborted, true);
  assert.equal(old.current(), false);
  assert.equal(fresh.current(), true);
  selection.invalidate();
  assert.equal(fresh.current(), false);
});

test('catalogue links stay on approved local research routes', () => {
  assert.equal(safeWorkspaceHref('/research/factor-library?factor=lib-trend-roc'),
    '/research/factor-library?factor=lib-trend-roc');
  for (const url of ['//external.test/', 'javascript:alert(1)', '/research/strategies\\test', 'https://example.org/']) {
    assert.equal(safeWorkspaceHref(url), null);
  }
});
