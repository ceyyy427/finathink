"""Progressive comparison and portable-research controls in the existing shell."""
from html import escape


def reproduction_page(csrf, runs):
    available = [item for item in runs if item.get('report_status') == 'AVAILABLE']
    def options(selected):
        return ''.join(f'<option value="{escape(item["run_id"], quote=True)}"'+(' selected' if index == selected else '')+f'>{escape(item["run_id"])} · {escape(str(item.get("as_of") or "日期未知"))}</option>' for index, item in enumerate(available))
    return f'''<section class="section panel" data-reproduction-workspace>
      <h2>研究差异与复现</h2>
      <p>逐项核对研究条件，再阅读量化证据。带数据的研究包可在另一工作区复算已覆盖的确定性产物。</p>
      <input type="hidden" data-repro-csrf value="{escape(csrf, quote=True)}">
      <form data-repro-compare-form class="form-grid">
        <label>左侧研究<select data-repro-left required>{options(0)}</select></label>
        <label>右侧研究<select data-repro-right required>{options(1)}</select></label>
        <button type="submit" data-repro-compare disabled>比较研究条件</button>
      </form>
      <p data-repro-error role="alert" class="error-state" hidden></p>
      <div data-repro-comparison aria-live="polite"><p>选择两项已有报告查看差异；尚无报告时，请先完成研究。</p></div>
      <div data-repro-evidence></div>
      <section class="section"><h3>迁移这项研究</h3>
        <p>默认导出条件、数值产物和缺依赖清单。勾选后才携带标准化价格与成交量；原数据出处身份和脱敏数据身份分别保留。</p>
        <label class="repro-consent"><input type="checkbox" data-repro-data> 在研究包中携带已发表的标准化数据</label>
        <button type="button" data-repro-export disabled>导出左侧研究包</button>
      </section>
      <section class="section"><h3>在此工作区恢复</h3>
        <form data-repro-import class="form-grid"><label>选择 JSON 研究包（最多 8 MiB）<input type="file" data-repro-file accept=".json,application/json" required></label>
        <button type="submit" data-repro-restore disabled>恢复研究包</button></form>
        <label>已恢复的研究包<select data-repro-packages><option value="">暂无研究包</option></select></label>
        <button type="button" data-repro-recompute disabled>复算已恢复产物</button>
        <p data-repro-import-status role="status"></p><div data-repro-receipt aria-live="polite"></div>
      </section>
      <noscript><p>已有研究和量化条件可在本页下方阅读。交互比较、导出与恢复需要启用 JavaScript。</p></noscript>
    </section>'''
