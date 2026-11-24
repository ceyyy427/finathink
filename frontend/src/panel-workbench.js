import { readLocalJSON, userErrorMessage } from './local-request.js';

const BASE = '/api/research/panels/';
const SNAPSHOTS = '/api/research/snapshots';
const DIGEST = /^[a-f0-9]{64}$/u;
const PRICE_FIELDS = ['instrument', 'timestamp', 'open', 'high', 'low', 'close', 'volume'];
const CLOCK_FIELDS = new Set(['instrument', 'timestamp', 'available_at', 'announcement_at', 'revision_at',
  'report_period_end', 'effective_from', 'effective_until']);

function node(doc, tag, text) {
  const el = doc.createElement(tag);
  if (text != null) el.textContent = text;
  return el;
}

function options(select, choices, placeholder, selected = select.value) {
  const doc = select.ownerDocument;
  const blank = node(doc, 'option', placeholder);
  blank.value = '';
  select.replaceChildren(blank, ...choices.map(([value, label]) => {
    const option = node(doc, 'option', label);
    option.value = value;
    return option;
  }));
  if (choices.some(([value]) => value === selected)) select.value = selected;
}

function link(doc, text, url) {
  const a = node(doc, 'a', text);
  a.href = url;
  return a;
}

function savedLabel(item) {
  return [item.name, item.data_mode, item.instrument_ids.join('、'), item.last.slice(0, 10)].filter(Boolean).join(' · ');
}

export function mountSavedPanelSelectors(doc) {
  const selectors = [...doc.querySelectorAll('[data-saved-panel-select]')];
  if (!selectors.length) return;
  const linked = new URL(doc.location.href).searchParams.get('panel');
  const chosen = DIGEST.test(linked || '') ? linked : '';
  for (const select of selectors) {
    const form = select.form;
    const mode = form.elements.namedItem('mode');
    const show = () => { select.closest('[data-panel-picker]').hidden = Boolean(mode && !['saved', 'user'].includes(mode.value)); };
    if (chosen && mode) mode.value = mode.querySelector('option[value="saved"]') ? 'saved' : 'user';
    show();
    mode?.addEventListener('change', show);
  }
  readLocalJSON(globalThis.fetch, BASE + 'saved').then(({ panels }) => {
    const choices = panels.filter(item => DIGEST.test(item.panel_ref))
      .map(item => [item.panel_ref, savedLabel(item)]);
    for (const select of selectors) {
      options(select, choices, choices.length ? '请选择研究面板' : '还没有保存的面板', chosen);
    }
  }).catch(() => {
    for (const select of selectors) options(select, [], '读取失败，请刷新或打开研究面板');
  });
}

function checkboxes(target, choices, name, selected, empty) {
  const doc = target.ownerDocument;
  const nodes = choices.map(([value, text]) => {
    const label = node(doc, 'label');
    const input = node(doc, 'input');
    input.type = 'checkbox';
    input.name = name;
    input.value = value;
    input.checked = selected.includes(value);
    label.append(input, node(doc, 'span', text));
    return label;
  });
  target.replaceChildren(...(nodes.length ? nodes : [node(doc, 'p', empty)]));
}

function renderPreview(output, preview) {
  const doc = output.ownerDocument;
  const issues = [...preview.issues, ...preview.target_issues];
  output.replaceChildren();
  if (preview.panel_fingerprint) {
    output.append(node(doc, 'p', '面板指纹 ' + preview.panel_fingerprint));
    output.append(node(doc, 'p', preview.data_mode + ' · ' + preview.row_count + ' 行 · ' + preview.eligible_rows + ' 行符合纸面交易条件'));
  }
  if (issues.length) {
    const list = node(doc, 'ul');
    list.append(...issues.map(issue => node(doc, 'li', issue)));
    output.append(list);
  }
  if (!preview.fields.length) return;
  const wrapper = node(doc, 'div');
  wrapper.className = 'table-wrap';
  wrapper.tabIndex = 0;
  wrapper.setAttribute('role', 'region');
  wrapper.setAttribute('aria-label', '时点覆盖表，可左右滚动');
  const table = node(doc, 'table');
  table.createCaption().textContent = '字段覆盖及可用时间（UTC）';
  const head = table.createTHead().insertRow();
  for (const label of ['字段', '实际单位', '方法所需单位', '覆盖行数', '最早／最晚可用时间']) {
    const th = node(doc, 'th', label);
    th.scope = 'col';
    head.append(th);
  }
  const body = table.createTBody();
  for (const item of preview.fields) {
    const row = body.insertRow();
    const label = node(doc, 'th', item.field + (item.required ? '（所需）' : ''));
    label.scope = 'row';
    row.append(label, ...[item.unit || '—', item.expected_unit || item.allowed_units?.join('、') || '—',
      item.covered == null ? '—' : item.covered + ' / ' + (item.row_count ?? preview.row_count),
      item.available_at?.join(' 至 ') || '—'].map(value => node(doc, 'td', value)));
  }
  wrapper.append(table);
  output.append(wrapper);
}

export function mountPanelWorkbench(root, { fetcher = globalThis.fetch } = {}) {
  if (root.dataset.panelMounted) return;
  root.dataset.panelMounted = 'true';
  const doc = root.ownerDocument;
  const query = name => root.querySelector('[data-panel-' + name + ']');
  const draft = query('draft');
  const price = query('price');
  const fields = query('fields');
  const extend = query('extend');
  const previewButton = draft.querySelector('button[type="submit"]');
  const save = query('save');
  const snapshot = draft.elements.namedItem('snapshot_ref');
  const status = query('status');
  const output = query('preview');
  let definitions;
  let connections = [];
  let selection;
  let generation = 0;
  let edit = 0;
  let draftLoad = 0;
  let preview;

  const post = (path, body) => readLocalJSON(fetcher, path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, _csrf: root.dataset.csrf }),
  });

  function invalidate() {
    edit += 1;
    preview = null;
    save.disabled = true;
    output.replaceChildren();
    previewButton.disabled = !selection;
    status.textContent = selection ? '输入已更新，可预览面板。' : '';
  }

  function draftValues() {
    const data = new FormData(draft);
    const [target_kind, target_id] = (data.get('target') || '').split(':');
    return {
      name: data.get('name'), snapshot_ref: snapshot.value, instrument_ids: data.getAll('asset'),
      start: data.get('start'), end: data.get('end'), timezone: data.get('timezone'),
      attachment_refs: data.getAll('attachment'),
      rules: Object.fromEntries(Object.keys(definitions.rules).filter(key => data.get(key)).map(key => [key, data.get(key)])),
      ...(target_id ? { target_kind, target_id } : {}),
    };
  }

  function fillUnits() {
    const id = fields.elements.namedItem('connection_id').value;
    const connection = connections.find(item => item.connection_id === id);
    const names = Object.keys(connection?.field_mapping || {}).filter(name => !CLOCK_FIELDS.has(name));
    const valid = names.length && names.every(name => definitions.fields[name]);
    const controls = names.map(name => {
      const label = node(doc, 'label', name + ' 单位');
      const select = node(doc, 'select');
      select.name = 'unit:' + name;
      select.required = true;
      const units = definitions.fields[name] || [];
      options(select, units.map(unit => [unit, unit]), '请选择单位', units.length === 1 ? units[0] : '');
      label.append(select);
      return label;
    });
    query('field-units').replaceChildren(...(valid ? controls : []));
    fields.querySelector('button').disabled = !valid || !selection;
  }

  async function chooseSnapshot(ref, prior) {
    const operation = ++generation;
    selection = null;
    invalidate();
    query('extension').hidden = true;
    for (const name of ['assets', 'rules', 'attachments', 'field-units']) query(name).replaceChildren();
    fields.querySelector('button').disabled = true;
    const message = query('snapshot-status');
    message.textContent = ref ? '正在读取行情与附件…' : '选择快照后查看可用标的。';
    if (!ref) return;
    try {
      const item = await readLocalJSON(fetcher, BASE + 'options?snapshot_ref=' + encodeURIComponent(ref));
      if (operation !== generation) return;
      selection = item;
      message.textContent = item.data_mode + ' · ' + item.instruments.join('、') + ' · 快照截至 ' + item.as_of;
      const assets = item.instruments.map(id => [id, id]);
      checkboxes(query('assets'), assets, 'asset', prior?.instrument_ids || item.instruments, '没有可选标的。');
      const attachmentOptions = item.attachments.map(attachment => [attachment.attachment_ref,
        Object.keys(attachment.units).join('、') + ' · ' + attachment.row_count + ' 条 · ' + attachment.attachment_ref.slice(0, 10)]);
      checkboxes(query('attachments'), attachmentOptions, 'attachment', prior?.attachment_refs || [], '尚无字段附件。');
      for (const [name, rule] of Object.entries(definitions.rules)) {
        const label = node(doc, 'label', rule.label);
        const select = node(doc, 'select');
        select.name = name;
        const refs = new Set(item.attachments.filter(a => rule.fields.every(field => field in a.units)).map(a => a.attachment_ref));
        const available = attachmentOptions.filter(([ref]) => refs.has(ref));
        options(select, available, '请选择' + rule.label, prior?.rules?.[name] || (available.length === 1 ? available[0][0] : ''));
        label.append(select);
        query('rules').append(label);
      }
      draft.elements.namedItem('start').value = prior?.start || item.first;
      draft.elements.namedItem('end').value = prior?.end || item.last;
      if (prior?.timezone) draft.elements.namedItem('timezone').value = prior.timezone;
      if (prior?.name) draft.elements.namedItem('name').value = prior.name;
      query('extension').hidden = !item.extendable;
      invalidate();
      fillUnits();
    } catch (error) {
      if (operation === generation) message.textContent = userErrorMessage(error);
    }
  }

  async function refreshSnapshots(selected) {
    const { snapshots } = await readLocalJSON(fetcher, SNAPSHOTS);
    options(snapshot, snapshots.map(item => [item.snapshot_ref,
      item.mode + ' · ' + item.instruments.join('、') + ' · ' + item.as_of.slice(0, 10) + ' · ' + item.snapshot_ref.slice(0, 8)]),
    snapshots.length ? '请选择行情快照' : '先获取行情快照', typeof selected === 'function' ? selected() : selected);
  }

  async function refreshSaved() {
    const { panels } = await readLocalJSON(fetcher, BASE + 'saved');
    query('saved-status').textContent = panels.length ? panels.length + ' 份面板可用于研究。' : '尚未保存研究面板。';
    query('saved').replaceChildren(...panels.filter(item => DIGEST.test(item.panel_ref)).map(item => {
      const li = node(doc, 'li');
      const param = '?panel=' + item.panel_ref;
      const actions = node(doc, 'div');
      actions.className = 'action-row';
      const reuse = node(doc, 'button', '加载为草稿');
      reuse.type = 'button';
      reuse.className = 'button-secondary';
      reuse.addEventListener('click', async () => {
        const operation = generation;
        const revision = edit;
        const load = ++draftLoad;
        try {
          const { draft: previous } = await readLocalJSON(fetcher, BASE + 'saved?panel_ref=' + item.panel_ref);
          if (operation !== generation || revision !== edit || load !== draftLoad) return;
          snapshot.value = previous.snapshot_ref;
          await chooseSnapshot(previous.snapshot_ref, previous);
          snapshot.focus();
        } catch (error) { query('saved-status').textContent = userErrorMessage(error); }
      });
      actions.append(link(doc, '检验因子', '/research/factor-suites' + param),
        link(doc, '研究策略', '/research/strategies' + param),
        link(doc, '从问题开始', '/research/workbench' + param + '#qw-start'), reuse);
      li.append(node(doc, 'strong', savedLabel(item)), actions);
      return li;
    }));
  }

  draft.addEventListener('input', event => {
    if (event.target === snapshot && definitions) chooseSnapshot(snapshot.value);
    else invalidate();
  });
  fields.elements.namedItem('connection_id').addEventListener('change', fillUnits);
  draft.addEventListener('submit', async event => {
    event.preventDefault();
    if (previewButton.disabled || !selection) return;
    invalidate();
    const revision = edit;
    const values = draftValues();
    previewButton.disabled = true;
    status.textContent = '正在核对时点和覆盖…';
    try {
      const result = await post(BASE + 'preview', values);
      if (revision !== edit) return;
      renderPreview(output, result);
      if (DIGEST.test(result.preview_fingerprint || '')) {
        preview = { values, signature: JSON.stringify(values), ref: result.preview_fingerprint };
        save.disabled = false;
      }
      status.textContent = result.status === 'READY'
        ? (result.target_issues.length ? '面板可以保存。所选方法还需要补充字段或单位。' : '面板可以保存。')
        : '请按下方提示修复数据后重新预览。';
    } catch (error) {
      if (revision === edit) status.textContent = userErrorMessage(error);
    } finally {
      if (revision === edit) previewButton.disabled = false;
    }
  });
  save.addEventListener('click', async () => {
    if (!preview || save.disabled || !selection) return;
    const bound = preview;
    if (bound.signature !== JSON.stringify(draftValues())) {
      invalidate();
      return;
    }
    save.disabled = true;
    status.textContent = '正在保存面板…';
    try {
      await post(BASE + 'save', { ...bound.values, preview_fingerprint: bound.ref });
      if (preview === bound) status.textContent = '面板已保存，可从因子、策略或研究工作台选用。';
      await refreshSaved();
    } catch (error) {
      if (preview === bound) { status.textContent = userErrorMessage(error); save.disabled = false; }
    }
  });

  function bindCapture(form, url, values, onSuccess, statusName) {
    const button = form.querySelector('button[type="submit"]');
    const message = query(statusName);
    let busy = false;
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (button.disabled || busy) return;
      busy = true;
      button.disabled = true;
      message.textContent = '正在读取数据接口…';
      try {
        const revision = edit;
        const body = values(Object.fromEntries(new FormData(form)));
        const result = await post(url, body);
        message.textContent = await onSuccess(result, body, revision);
      } catch (error) { message.textContent = userErrorMessage(error); }
      finally {
        busy = false;
        if (form === fields) fillUnits();
        else button.disabled = false;
      }
    });
  }
  async function showCaptured(result, _body, revision) {
    if (!DIGEST.test(result.snapshot_ref)) throw new TypeError('invalid snapshot reference');
    await refreshSnapshots(() => revision === edit ? result.snapshot_ref : snapshot.value);
    if (revision !== edit) return '新快照已保存，可从快照列表选择。';
    await chooseSnapshot(result.snapshot_ref);
    return '快照已保存。请选择该快照对应的规则和字段附件。';
  }
  bindCapture(price, SNAPSHOTS + '/capture', data => {
    const assets = [data.equities, data.etfs].map(value => value.split(/[,，]/u).map(id => id.trim()).filter(Boolean));
    const ids = assets.flat();
    if (!ids.length || ids.length > 20 || new Set(ids).size !== ids.length) throw new Error('请填写 1 至 20 个标的，每个标的只能出现一次。');
    return { connection_id: data.connection_id, instrument: ids.join(','), start: data.start, end: data.end,
      price_currency: data.price_currency, volume_unit: 'shares', adjustment: 'RAW',
      asset_classes: Object.fromEntries(assets.flatMap((codes, i) => codes.map(id => [id, i ? 'ETF' : 'EQUITY']))) };
  }, showCaptured, 'price-status');
  bindCapture(fields, BASE + 'capture-fields', data => {
    if (!selection) throw new Error('请先选择行情快照。');
    const { instrument_ids } = draftValues();
    return { snapshot_ref: snapshot.value, connection_id: data.connection_id, instrument_ids,
      ...(data.start ? { start: data.start } : {}), ...(data.end ? { end: data.end } : {}),
      units: Object.fromEntries(Object.entries(data).filter(([key]) => key.startsWith('unit:')).map(([key, val]) => [key.slice(5), val])) };
  }, async (_, body) => {
    if (snapshot.value === body.snapshot_ref && selection) await chooseSnapshot(body.snapshot_ref, draftValues());
    return '字段附件已保存，可按上方规则或附加字段选用。';
  }, 'fields-status');
  bindCapture(extend, SNAPSHOTS + '/capture', data => ({ base_ref: snapshot.value, end: data.end }), showCaptured, 'extend-status');

  Promise.all([readLocalJSON(fetcher, BASE + 'definitions'), readLocalJSON(fetcher, '/api/data/connections'),
    refreshSnapshots(), refreshSaved()]).then(([defs, catalog]) => {
    definitions = defs;
    connections = catalog.connections;
    options(price.elements.namedItem('connection_id'), connections.filter(item => PRICE_FIELDS.every(name => name in item.field_mapping))
      .map(item => [item.connection_id, item.display_name]), '请选择行情连接');
    options(fields.elements.namedItem('connection_id'), connections.filter(item => {
      const map = item.field_mapping;
      return ['instrument', 'timestamp', 'available_at'].every(name => name in map)
        && Object.keys(map).some(name => definitions.fields[name])
        && Object.keys(map).every(name => CLOCK_FIELDS.has(name) || definitions.fields[name]);
    }).map(item => [item.connection_id, item.display_name]), '请选择字段连接');
    options(draft.elements.namedItem('target'), [
      ...defs.factors.map(item => ['factor:' + item.id, '因子 · ' + item.label]),
      ...defs.strategies.map(item => ['strategy:' + item.id, '策略 · ' + item.label]),
    ], '查看基础字段');
    price.querySelector('button').disabled = false;
    if (snapshot.value) chooseSnapshot(snapshot.value);
  }).catch(error => { query('snapshot-status').textContent = userErrorMessage(error); });
}
