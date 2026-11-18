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
const maxUI = ts.transpileModule(readFileSync(dependencies.resolve("@maxhub/max-ui"), "utf8"), {
  fileName: "max-ui.js", compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const maxUIBundle = `(() => {
 const module={exports:{}};
 const jsx=(type,props,key)=>React.createElement(type,{...props,...(key===undefined?{}:{key})});
 function require(name) {
   if(name === "react") return window.React;
   if(name === "react/jsx-runtime") return {jsx,jsxs:jsx,Fragment:React.Fragment};
   throw new Error("Unexpected MAX UI import: "+name);
 }
 ((module,exports,require)=>{${maxUI}})(module,module.exports,require);
 window.maxUILibrary=module.exports;
})();`;
const bundle = `(() => {
 const factories = {${[...modules].map(([path, source]) => `${JSON.stringify(path)}:(module,exports,require)=>{${source}}`).join(",")}};
 const cache = {};
 function require(name) {
  if (name === "react") return window.React;
  if (name === "next/dynamic") return () => window.maxUILibrary.MaxUI;
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
      await page.addScriptTag({ content: maxUIBundle });
      await page.addStyleTag({ content: readFileSync(resolve(dirname(dependencies.resolve("@maxhub/max-ui")), "styles.css"), "utf8") });
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

test("actual MAX UI root and controls follow product fonts without overwriting explicit roles", async () => {
  const browser = await chromium.launch({ headless: true, executablePath });
  try {
    for (const [platform, colorScheme, family, ui, heading] of [
      ["android", "light", "Georgia", "Arial", "Courier New"],
      ["ios", "dark", "Verdana", "Tahoma", "Times New Roman"],
      ["android", "light", null, null, null],
    ]) {
      const page = await browser.newPage();
      // This fixture serves no app and cannot reach any real endpoint/provider.
      await page.route("**/*", route => {
        assert.equal(new URL(route.request().url()).origin, "https://fixture.invalid");
        return route.fulfill({contentType:"text/html",body:'<!doctype html><div id="root"></div>'});
      });
      await page.goto("https://fixture.invalid/");
      for (const [library, file] of [["react", "react.development.js"], ["react-dom", "react-dom.development.js"]]) {
        await page.addScriptTag({ content: readFileSync(resolve(dirname(dependencies.resolve(`${library}/package.json`)), "umd", file), "utf8") });
      }
      await page.addScriptTag({ content: maxUIBundle });
      await page.addStyleTag({ content: readFileSync(resolve(dirname(dependencies.resolve("@maxhub/max-ui")), "styles.css"), "utf8") });
      await page.addScriptTag({ content: bundle });
      await page.addStyleTag({content:'@layer base, utilities; @layer base { button,input,select,textarea { font-family:var(--font-ui); } h1,h2,h3 { font-family:var(--font-display); } } @layer utilities { .font-mono { font-family:monospace; } } .explicit-native-font { font-family:serif; }'});
      await page.evaluate(({platform, colorScheme, family, ui, heading}) => {
        document.body.style.fontFamily = family ? "var(--font-body)" : "serif";
        if (family) {
          document.body.style.setProperty("--font-body", family);
          document.body.style.setProperty("--font-display", heading);
          document.body.style.setProperty("--font-ui", ui);
        }
        window.WebApp = {initData:"signed-local-fixture", platform, colorScheme};
        window.fetch = async () => Response.json({user:{id:"42"}});
        const {Button, Input, Textarea, Typography} = window.maxUILibrary;
        const product=React.createElement("main", null,
          React.createElement("h1", {style:{fontFamily:"monospace"}}, "Explicit heading"),
          React.createElement("p", {id:"product-body"}, "Real product text"),
          React.createElement(Typography.Body, {id:"vendor-body"}, "Vendor body text"),
          React.createElement(Typography.Title, {asChild:true}, React.createElement("h2", {id:"vendor-heading"}, "Vendor title heading")),
          React.createElement(Typography.Headline, {asChild:true}, React.createElement("h3", {id:"vendor-headline"}, "Vendor headline heading")),
          React.createElement(Button, {id:"product-button"}, "Continue"),
          React.createElement(Button, {asChild:true}, React.createElement("a", {id:"product-link",href:"#local"}, "Open")),
          React.createElement(Input, {id:"product-input", defaultValue:""}),
          React.createElement(Textarea, {id:"product-textarea", defaultValue:""}),
          React.createElement("select", {id:"product-select"}, React.createElement("option",null,"Choose")),
          React.createElement("div", {id:"product-role-button",role:"button",tabIndex:0}, "Role control"),
          React.createElement("a", {id:"explicit-link",className:"font-mono",href:"#local"}, "Owner link role"),
          React.createElement("button", {id:"explicit-button",className:"font-mono"}, "Owner button role"),
          React.createElement("button", {id:"explicit-class",className:"explicit-native-font"}, "Owner class role"));
        ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(provider.MaxAppProvider,null,product));
      }, {platform,colorScheme,family,ui,heading});
      await page.waitForSelector("#product-body", {timeout:5000});
      const readFonts = () => page.evaluate(() => Object.fromEntries(
        ["body", ".yleum-max-ui-font", "#product-body", "#vendor-body", "#vendor-heading", "#vendor-headline", "#product-button", "#product-button span", "#product-link", "#product-input", "#product-textarea", "#product-select", "#product-role-button", "#explicit-link", "#explicit-button", "#explicit-class", "h1"].map(selector =>
          [selector, getComputedStyle(document.querySelector(selector)).fontFamily])));
      let fonts=await readFonts();
      for (const selector of [".yleum-max-ui-font", "#product-body", "#vendor-body"])
        assert.equal(fonts[selector], fonts.body, `${platform}/${colorScheme}: ${selector} inherits the product face`);
      for (const selector of ["#product-button", "#product-button span", "#product-link", "#product-input", "#product-textarea", "#product-select", "#product-role-button"])
        assert.equal(fonts[selector], ui || fonts.body, `${platform}/${colorScheme}: ${selector} uses the UI role`);
      assert.equal(fonts["#vendor-heading"], heading ? `"${heading}"` : fonts.body);
      assert.equal(fonts["#vendor-headline"], heading ? `"${heading}"` : fonts.body);
      for (const selector of ["#explicit-link", "#explicit-button"])
        assert.equal(fonts[selector],"monospace","Explicit owner utilities must survive the shell");
      assert.equal(fonts["#explicit-class"],"serif","An explicit owner class must survive the shell");
      assert.equal(fonts.h1,"monospace", "An explicit descendant role must remain authoritative");
      if (family) {
        const unbridged=await page.evaluate(() => {
          const bridge=document.querySelector(".yleum-max-ui-font > style");
          const rules=bridge.textContent;
          bridge.textContent="";
          const font=getComputedStyle(document.querySelector(".yleum-max-ui-font")).fontFamily;
          bridge.textContent=rules;
          return font;
        });
        assert.notEqual(unbridged,fonts.body,"The real vendor rule must reproduce the prior inheritance defect");
      }
      assert.ok(await page.locator(`.yleum-max-ui-font[class*="MaxUI_platform_${platform}"][class*="MaxUI_colorScheme_${colorScheme}"]`).count());
      await page.evaluate(() => {
        document.body.style.setProperty("--font-body","Tahoma");
        document.body.style.setProperty("--font-ui","Verdana");
        document.body.style.setProperty("--font-display","Georgia");
        document.body.style.fontFamily="var(--font-body)";
      });
      fonts=await readFonts();
      assert.equal(fonts["#product-body"], fonts.body);
      assert.equal(fonts["#vendor-body"], fonts.body);
      for (const selector of ["#product-button", "#product-button span", "#product-link", "#product-input", "#product-textarea", "#product-select", "#product-role-button"])
        assert.equal(fonts[selector], "Verdana");
      assert.equal(fonts["#vendor-heading"],"Georgia");
      assert.equal(fonts["#vendor-headline"],"Georgia");
      assert.equal(fonts["#explicit-link"],"monospace");
      assert.equal(fonts["#explicit-button"],"monospace");
      assert.equal(fonts["#explicit-class"],"serif");
      assert.equal(fonts.h1,"monospace");
      await page.close();
    }
  } finally { await browser.close(); }
});
