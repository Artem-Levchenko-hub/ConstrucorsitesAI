// Real React 18/native-dialog gate. Existing locked Chromium; all requests intercepted.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, isAbsolute, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const base = resolve(dirname(fileURLToPath(import.meta.url)), "../../../orchestrator/templates/max-miniapp-nextjs");
const dependencies = createRequire(resolve(base, "package.json"));
const ts = dependencies("typescript");
assert.equal(dependencies("react/package.json").version, "18.3.1");
assert.equal(dependencies("react-dom/package.json").version, "18.3.1");
const { chromium } = dependencies(process.env.QA_PLAYWRIGHT_MODULE || "playwright");
const executablePath = process.env.QA_BEHAVIOR_CHROMIUM_EXECUTABLE;
assert.ok(isAbsolute(executablePath));
assert.equal(createHash("sha256").update(readFileSync(executablePath)).digest("hex"), process.env.QA_BEHAVIOR_CHROMIUM_SHA256);

const modules = new Map();
function compile(path) {
  if (modules.has(path)) return;
  const file = [`${path}.tsx`, `${path}.ts`].find(name => existsSync(resolve(base, "src", name)));
  assert.ok(file, `Missing actual protected module ${path}`);
  const source = ts.transpileModule(readFileSync(resolve(base, "src", file), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  modules.set(path, source);
  for (const match of source.matchAll(/require\("(@\/[^\"]+)"\)/g)) {
    if (match[1] !== "@/components/YleumCompliance") compile(match[1].slice(2));
  }
}
compile("components/MaxAppProvider");
const bundle = `(() => {
 const factories = {${[...modules].map(([path, source]) => `${JSON.stringify(path)}:(module,exports,require)=>{${source}}`).join(",")}};
 const cache = {};
 function require(name) {
  if (name === "react") return window.React;
  if (name === "next/dynamic") return () => ({children}) => React.createElement(React.Fragment,null,children);
  if (name === "@/components/YleumCompliance") return {YleumCompliance:()=>null};
  const path = name.startsWith("@/") ? name.slice(2) : name;
  if (!cache[path]) { cache[path]={exports:{}}; factories[path](cache[path],cache[path].exports,require); }
  return cache[path].exports;
 }
 window.provider = require("components/MaxAppProvider");
})();`;

test("native auth gate covers and blocks an ordinary high-z portal without discarding same-actor state", async () => {
  const browser = await chromium.launch({ headless: true, executablePath });
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 800 } });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
      await page.route("**/*", route => {
        assert.equal(new URL(route.request().url()).origin, "https://fixture.invalid");
        return route.fulfill({ contentType: "text/html", body: '<!doctype html><div id="root"></div>' });
      });
      await page.goto("https://fixture.invalid/");
      for (const [library, file] of [["react", "react.development.js"], ["react-dom", "react-dom.development.js"]]) {
        await page.addScriptTag({ content: readFileSync(resolve(dirname(dependencies.resolve(`${library}/package.json`)), "umd", file), "utf8") });
      }
      await page.addScriptTag({ content: bundle });
      assert.deepEqual(errors, []);
      await page.evaluate(() => {
        window.fixture = { actor: "42", hold: false, mounts: 0, clicks: 0 };
        window.fetch = async () => {
          if (fixture.fail) throw new TypeError("Fixture network unavailable");
          const session = () => Response.json({ user: { id: fixture.actor } });
          return fixture.hold ? new Promise(resolve => { fixture.resume = () => resolve(session()); }) : session();
        };
        function Product() {
          const actor = provider.useMaxApp().user.id;
          const [contact, setContact] = React.useState(actor === "42" ? "A-private-fixture" : "");
          React.useEffect(() => { fixture.mounts++; }, []);
          return ReactDOM.createPortal(React.createElement("aside", {
            id: "product-portal", "data-actor": actor,
            style: { position: "fixed", inset: 0, zIndex: 2147483647, background: "magenta" },
          }, React.createElement("input", { id: "contact", value: contact, onChange: event => setContact(event.target.value) }),
          React.createElement("button", { id: "private-action", onClick: () => fixture.clicks++ }, "Private action")), document.body);
        }
        ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(provider.MaxAppProvider, null, React.createElement(Product)));
      });
      await page.waitForSelector('#product-portal[data-actor="42"]');
      await page.evaluate(() => { fixture.hold = true; window.dispatchEvent(new Event("focus")); });
      await page.waitForFunction(() => !!fixture.resume);
      assert.equal(await page.evaluate(() => document.getElementById("product-portal").contains(
        document.elementFromPoint(innerWidth / 2, innerHeight / 2))), false, "Checking must cover the old actor's portal");
      await page.waitForSelector("dialog[open]", { timeout: 2000 });
      const gate = await page.evaluate(() => {
        const dialog = document.querySelector("dialog[open]");
        const covered = [.1,.5,.9].every(x => [.1,.5,.9].every(y => dialog.contains(document.elementFromPoint(innerWidth*x, innerHeight*y))));
        document.getElementById("contact").focus();
        return { modal: dialog.matches(":modal"), covered, focusBlocked: dialog.contains(document.activeElement) };
      });
      assert.deepEqual(gate, { modal: true, covered: true, focusBlocked: true });
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("dialog:modal").count(), 1);
      const button = await page.locator("#private-action").boundingBox();
      await page.mouse.click(button.x + button.width / 2, button.y + button.height / 2);
      assert.equal(await page.evaluate(() => fixture.clicks), 0);
      await page.evaluate(() => { fixture.hold = false; fixture.resume(); });
      await page.waitForSelector("dialog", { state: "detached" });
      assert.equal(await page.inputValue("#contact"), "A-private-fixture");
      assert.equal(await page.evaluate(() => fixture.mounts), 1);
      await page.evaluate(() => { fixture.fail = true; window.dispatchEvent(new Event("focus")); });
      await page.waitForSelector('dialog:modal [role="alert"]');
      assert.equal(await page.evaluate(() => document.querySelector("dialog:modal").contains(
        document.elementFromPoint(innerWidth / 2, innerHeight / 2))), true);
      await page.evaluate(() => { fixture.fail = false; });
      await page.locator("dialog button").click();
      await page.waitForSelector("dialog", { state: "detached" });
      assert.equal(await page.inputValue("#contact"), "A-private-fixture");
      assert.equal(await page.evaluate(() => fixture.mounts), 1);
      await page.evaluate(() => { fixture.hold = true; fixture.actor = "43"; window.dispatchEvent(new Event("omnia:authchange")); });
      await page.waitForSelector("dialog:modal");
      await page.evaluate(() => { fixture.hold = false; fixture.resume(); });
      await page.waitForSelector('#product-portal[data-actor="43"]');
      assert.equal(await page.inputValue("#contact"), "");
      assert.equal(await page.evaluate(() => fixture.mounts), 2);
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
});
