import { build } from 'esbuild';
import { cpSync, mkdirSync } from 'node:fs';

await build({
  entryPoints: ['src/research.js'],
  bundle: true,
  format: 'iife',
  minify: true,
  sourcemap: false,
  target: ['es2020'],
  outfile: '../site/assets/finathink-research.js',
  legalComments: 'eof',
});

// The stdlib local app serves the packaged copy, while the static site serves
// `site/assets`. Keep both explicit outputs in sync so local browser acceptance
// exercises the same bundle that was just built.
cpSync('../site/assets/finathink-research.js', '../src/finahinking/_package_data/assets/finathink-research.js');

// KaTeX's HTML+MathML output remains readable without JavaScript. Vendor its
// stylesheet and fonts into the same local asset tree so the CSP never needs
// a CDN or network font request.
mkdirSync('../site/assets/katex/fonts', { recursive: true });
cpSync('node_modules/katex/dist/katex.min.css', '../site/assets/katex/katex.min.css');
cpSync('node_modules/katex/dist/fonts', '../site/assets/katex/fonts', { recursive: true });
