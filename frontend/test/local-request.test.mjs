import test from 'node:test';
import assert from 'node:assert/strict';
import { readLocalJSON, userErrorMessage } from '../src/local-request.js';

test('malformed JSON and native schema errors have actionable Chinese copy, not diagnostics', async () => {
  await assert.rejects(readLocalJSON(async () => ({ ok: true, json: async () => { throw new SyntaxError('Unexpected token < at /Users/private'); } }), '/local'), (error) => {
    assert.match(userErrorMessage(error), /响应.*格式异常/);
    assert.doesNotMatch(userErrorMessage(error), /Unexpected|Users|SyntaxError/);
    return true;
  });
  assert.match(userErrorMessage(new TypeError("Cannot read properties of undefined (reading 'map')")), /响应.*格式异常/);
});

test('safe Chinese server validation survives, private material and server faults do not', async () => {
  const message = '证据可用时间晚于观察期，请重新选择数据。';
  for (const [status, raw, expected] of [
    [422, message, message],
    [422, '读取 /Users/private/secret.json 失败', '输入未通过校验'],
    [422, '密钥 sk-private-example 无效', '输入未通过校验'],
    [500, message, '本地服务暂时无法完成请求'],
    [429, 'rate limit', '请求过于频繁'],
  ]) {
    await assert.rejects(readLocalJSON(async () => ({ ok: false, status, json: async () => ({ error: raw }) }), '/local'), (error) => {
      assert.ok(userErrorMessage(error).includes(expected));
      assert.doesNotMatch(userErrorMessage(error), /Users|sk-private|rate limit/);
      return true;
    });
  }
});

test('offline errors are Chinese and intentional cancellation retains its identity', async () => {
  await assert.rejects(readLocalJSON(async () => { throw new TypeError('Failed to fetch'); }, '/local'), (error) => /无法连接本地服务/.test(userErrorMessage(error)));
  const abort = new DOMException('aborted', 'AbortError');
  await assert.rejects(readLocalJSON(async () => { throw abort; }, '/local'), (error) => error === abort);
});
