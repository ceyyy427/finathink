// Public recovery copy only: never render parser diagnostics or transport payloads.
const MALFORMED = '本地服务响应的数据格式异常，请重新读取记录后重试。';
const safeChinese = (message) => typeof message === 'string' && message.length <= 240 && /[\u3400-\u9fff]/u.test(message) && !/(?:https?:|[/\\]|sk-|api[_ -]?key|bearer|authorization|password|密钥|凭证值|[\u0000-\u001f\u202a-\u202e\u2066-\u2069])/iu.test(message);

class LocalRequestError extends Error {}

export function userErrorMessage(error) {
  if (error instanceof LocalRequestError) return error.message;
  if (!(error instanceof TypeError || error instanceof SyntaxError) && safeChinese(error?.message)) return error.message;
  return MALFORMED;
}

export async function readLocalJSON(fetcher, url, options = {}) {
  let response;
  try { response = await fetcher(url, { credentials: 'same-origin', ...options }); }
  catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw new LocalRequestError('无法连接本地服务，请确认应用正在运行，再重新读取记录。');
  }
  let payload;
  try { payload = await response.json(); }
  catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw new LocalRequestError(MALFORMED);
  }
  if (!response.ok) {
    const status = response.status;
    const fallback = status === 429 ? '请求过于频繁，请稍后重新读取记录。' : status >= 500 ? '本地服务暂时无法完成请求，请检查磁盘空间和应用状态后重试。' : status === 403 ? '页面授权已失效，请刷新页面后重试。输入尚未提交。' : status === 404 ? '未找到所选记录，请重新读取并选择现有记录。' : '输入未通过校验，请检查数据、日期和资源上限后重试。';
    throw new LocalRequestError(status >= 400 && status < 500 && safeChinese(payload?.error) ? payload.error : fallback);
  }
  return payload;
}
