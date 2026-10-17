// Explicit browser gate: use existing template dependencies and a separately installed Playwright.
// QA_TEMPLATE_NODE_MODULES and QA_PLAYWRIGHT_MODULE select existing caches; no install/fallback/skip.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { createHash } from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../orchestrator/templates/max-miniapp-nextjs');
const dependencies = createRequire(path.join(process.env.QA_TEMPLATE_NODE_MODULES || path.join(root, 'node_modules'), '_fixture.cjs'));
const ts = dependencies('typescript');
const React = dependencies('react');
const { renderToStaticMarkup } = dependencies('react-dom/server');
const { chromium } = dependencies(process.env.QA_PLAYWRIGHT_MODULE || 'playwright');
const source = fs.readFileSync(path.join(root, 'src/components/YleumCompliance.tsx'), 'utf8');
const javascript = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS } }).outputText;
const exports = {};
vm.runInNewContext(javascript, {
  exports,
  require(name) {
    if (name === '@/lib/omnia/max-config') return { omniaMaxConfig: { legal: { age_rating: '18+' } } };
    if (name === '@/lib/omnia/integration-client') return { getYleumAppConfig() { throw new Error('SSR must not request config'); } };
    return dependencies(name);
  },
});
const footer = renderToStaticMarkup(React.createElement(exports.YleumCompliance));
const executablePath = process.env.QA_BEHAVIOR_CHROMIUM_EXECUTABLE;
if (executablePath) {
  assert.ok(path.isAbsolute(executablePath));
  assert.equal(createHash('sha256').update(fs.readFileSync(executablePath)).digest('hex'), process.env.QA_BEHAVIOR_CHROMIUM_SHA256);
}
const browser = await chromium.launch({ headless: true, executablePath });
const results = [];
const failures = [];
try {
  for (const width of [390, 768, 1440]) {
    for (const safeArea of [0, 34]) {
     for (const rows of [7, 9, 14]) {
      const page = await browser.newPage({ viewport: { width, height: 1024 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      // Substitute only the UA env() value: desktop Chromium supplies zero without a device cutout.
      const actualFooter = footer.replaceAll('env(safe-area-inset-bottom)', `${safeArea}px`);
      const html = `<!doctype html><html><head><meta charset="utf-8"><style>
        *{box-sizing:border-box}body{margin:0;font:16px Arial}main{padding:24px}
        article{height:92px;padding:20px;border:1px solid #ddd;margin-bottom:12px}
        nav{position:fixed;bottom:0;left:0;right:0;height:${72 + safeArea}px;background:#fff;z-index:50;border-top:1px solid #999;display:flex}
        nav button{flex:1}a:focus-visible{outline:3px solid #0645ad;outline-offset:2px}
      </style></head><body><main><h1>Каталог</h1>${Array.from({length:rows}, (_,i)=>`<article>Товар ${i+1}</article>`).join('')}</main>
      <nav aria-label="Разделы"><button>Каталог</button><button id="last-nav">Мои записи</button></nav>${actualFooter}</body></html>`;
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        assert.equal(url.origin, 'https://fixture.invalid');
        return route.fulfill({ contentType:'text/html', body:url.pathname === '/' ? html : `<h1>${url.pathname}</h1>` });
      });
      const links = ['/support', '/legal/privacy', '/legal/terms'];
      for (const [index, href] of links.entries()) {
        await page.goto('https://fixture.invalid/');
        await page.locator('#last-nav').focus();
        await page.keyboard.press('Control+Home');
        for (let tab = 0; tab <= index; tab++) await page.keyboard.press('Tab');
        const measured = await page.evaluate(() => {
          const a = document.activeElement;
          const r = a.getBoundingClientRect();
          const nav = document.querySelector('nav').getBoundingClientRect();
          const style = getComputedStyle(a);
          return { href: a.getAttribute('href'), top:r.top, bottom:r.bottom, navTop:nav.top,
            hit:a.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)),
            outline:parseFloat(style.outlineWidth), scrollY, maxScroll:document.documentElement.scrollHeight-innerHeight };
        });
        results.push({ width, safeArea, rows, ...measured });
        assert.equal(measured.href, href, 'real Tab order');
        if (!(measured.top >= 5 && measured.bottom + 5 <= measured.navTop && measured.hit && measured.outline >= 2)) {
          failures.push(results.at(-1));
        }
        if (process.env.QA_COMPLIANCE_ARTIFACT_DIR && safeArea === 34 && rows === 9 && index === 0) {
          await page.screenshot({ path:path.join(process.env.QA_COMPLIANCE_ARTIFACT_DIR, `footer-${width}.png`) });
        }
        await page.keyboard.press('Enter');
        await page.waitForURL(`https://fixture.invalid${href}`);
        assert.equal(await page.locator('h1').textContent(), href);
      }
      assert.deepEqual(errors, []);
      await page.close();
     }
    }
  }
} finally {
  await browser.close();
  console.log(JSON.stringify({ fixture:'actual-managed-footer', results }));
}
assert.deepEqual(failures, [], 'focused links must be visible above the fixed navigation');
