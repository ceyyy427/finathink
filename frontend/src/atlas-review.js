export function mountAtlasReview(root) {
  const input = root.querySelector('[data-atlas-scrub]');
  const table = root.querySelector('[data-atlas-values]');
  if (!input || !table) return;
  const output = root.querySelector('output');
  const cursor = root.querySelector('[data-atlas-cursor]');
  function inspect() {
    const index = input.valueAsNumber;
    const cells = table.rows[index]?.cells;
    if (!cells) return;
    const value = Array.from(cells, cell => cell.textContent).join(' · ');
    output.textContent = value;
    input.setAttribute('aria-valuetext', value);
    const x = index / Math.max(1, table.rows.length - 1) * 800;
    cursor.setAttribute('x1', x);
    cursor.setAttribute('x2', x);
    cursor.removeAttribute('hidden');
  }
  input.addEventListener('input', inspect);
  inspect();
}
