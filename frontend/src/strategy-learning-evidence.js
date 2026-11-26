// Render only frozen receipt facts. Opening a window reads its referenced ledger fills.
export function renderStrategyWindows(body, receipt, { label, fmt, readFills, errorMessage }) {
  const doc = body.ownerDocument;
  const element = (tag, text) => {
    const node = doc.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const codeLabel = code => label(code) === code ? code : label(code) + '（' + code + '）';
  const date = stamp => stamp?.slice(0, 10) || '未到期';
  const windows = [...receipt.samples, ...receipt.excluded_windows].sort((a, b) => a.start_at.localeCompare(b.start_at));
  let loading;
  function fills() {
    if (!loading) loading = readFills(receipt.observation_ref).catch(error => { loading = null; throw error; });
    return loading;
  }
  function list(parent, heading, values, attr) {
    if (!values.length) return;
    parent.append(element('h4', heading));
    const items = element('ul');
    values.forEach(value => {
      const item = element('li', value);
      if (attr) item.setAttribute(attr, '');
      items.append(item);
    });
    parent.append(items);
  }
  function facts(pairs) {
    const dl = element('dl');
    pairs.forEach(([name, value]) => dl.append(element('dt', name), element('dd', value ?? '未记录')));
    return dl;
  }
  function ledgerEvidence(fill, settlement, row) {
    const item = element('li');
    item.setAttribute('data-sl-fill', String(fill.order_id));
    const direction = { buy: '买入', sell: '卖出' }[fill.side] ?? fill.side;
    item.append(element('strong', ['订单 ' + fill.order_id, fill.instrument, direction,
      fill.timestamp <= row.start_at ? '前窗转入结算' : '本窗成交'].join(' · ')));
    item.append(facts([['决策时间', fill.decision_at], ['成交时间', fill.timestamp],
      ['成交量', fill.quantity], ['成交价', fill.price], ['成交金额', fill.notional],
      ['手续费', fill.fees], ['滑点成本', fill.slippage],
      ['结算间隔（交易日）', settlement?.settlement_sessions ?? fill.settlement_sessions],
      ['结算状态', settlement ? label(settlement.status) : '收据未记录'],
      ['实际结算时间', settlement?.settled_at ?? (settlement?.status === 'UNSETTLED' ? '截止时未结算' : '未记录')]]));
    return item;
  }
  function detail(row, state) {
    const details = element('details');
    details.setAttribute('data-sl-window-detail', '');
    details.dataset.status = state;
    details.dataset.startAt = row.start_at;
    details.append(element('summary', '查看 ' + date(row.start_at) + ' 至 ' + date(row.end_at) + ' 的窗口证据'));
    const sample = element('p', row.sample_ref ? '经济样本指纹 ' + row.sample_ref : '该窗口没有登记经济样本。');
    sample.setAttribute('data-sl-sample-ref', row.sample_ref || '');
    details.append(sample, facts([['开始时间', row.start_at], ['结束时间', row.end_at],
      ['证据可用时间', row.available_at], ['首次登记收据', row.first_observation_ref],
      ['成交费用合计', row.fees], ['滑点合计', row.slippage]]));
    const reasons = new Set(row.reasons || []);
    if (row.duplicate) reasons.add('DUPLICATE_SOURCE');
    list(details, '排除与去重原因', [...reasons].map(codeLabel), 'data-sl-exclusion');
    list(details, '订单执行原因', (row.execution_reasons || []).map(codeLabel), 'data-sl-order-reason');
    list(details, '受限的决策', (row.suppressed_decisions || []).map(decision => [
      decision.decision_at, codeLabel(decision.status), decision.reason,
      ...(decision.gates || []).map(rule => [rule.rule_id || rule.policy_field, codeLabel(rule.status),
        '观察值 ' + fmt(rule.value), '限值 ' + fmt(rule.limit)].join(' · ')),
    ].filter(Boolean).join(' · ')), 'data-sl-suppressed');
    const records = row.settlements || [];
    const ids = new Set([...(row.fill_order_ids || []), ...records.map(record => record.order_id)]);
    if (!ids.size) {
      details.append(element('p', row.fill_count === 0 ? '本窗无成交。' : '收据未列出可逐笔核对的成交。'));
      return details;
    }
    const target = element('div');
    target.setAttribute('aria-live', 'polite');
    details.append(target);
    let rendered = false;
    details.addEventListener('toggle', async () => {
      if (!details.open || rendered) return;
      rendered = true;
      target.textContent = '正在核对原账本的逐笔成交…';
      try {
        const data = await fills();
        if (data.observation_ref !== receipt.observation_ref || !Array.isArray(data.fills)) throw new TypeError();
        const byId = new Map(data.fills.map(fill => [fill.order_id, fill]));
        const items = element('ul');
        for (const id of ids) {
          const fill = byId.get(id);
          const settlement = records.find(record => record.order_id === id);
          if (!fill || (settlement && (fill.instrument !== settlement.instrument || fill.timestamp !== settlement.booked_at))) throw new TypeError();
          items.append(ledgerEvidence(fill, settlement, row));
        }
        target.replaceChildren(element('h4', '成交与结算明细'), items);
      } catch (error) {
        rendered = false;
        target.textContent = errorMessage(error) + ' 可收起后再次展开。';
      }
    });
    return details;
  }
  const fragment = doc.createDocumentFragment();
  for (const window of windows) {
    const state = window.duplicate ? 'DUPLICATE_SOURCE' : window.status;
    const record = element('tr');
    const fields = [date(window.start_at), date(window.end_at), label(state), fmt(window.net_return, true),
      fmt(window.benchmark_return, true), fmt(window.fill_count)];
    fields.forEach(value => record.append(element('td', value)));
    const supplemental = element('tr');
    const cell = element('td');
    cell.colSpan = fields.length;
    cell.append(detail(window, state));
    supplemental.append(cell);
    fragment.append(record, supplemental);
  }
  body.replaceChildren(fragment);
}
