import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const base = resolve(dirname(fileURLToPath(import.meta.url)), "../../../orchestrator/templates/max-miniapp-nextjs");
const requireTemplate = createRequire(resolve(base, "package.json"));
const requireWeb = createRequire(resolve(base, "../../../web/package.json"));
const { JSDOM } = requireWeb("jsdom");
const ts = requireTemplate("typescript");
const React = requireTemplate("react");
assert.equal(requireTemplate("react/package.json").version, "18.3.1");
assert.equal(requireTemplate("react-dom/package.json").version, "18.3.1");

function deferred() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}
const session = (actor, preview = false) => Response.json({ user: { id: actor }, ...(preview ? { mode: "preview" } : {}) });

async function mounted(transport, { embedded = false, launch = "" } = {}) {
  const dom = new JSDOM("<!doctype html><div id='root'></div>", { url: "https://one.app.test/" });
  const boundary = dom.window;
  // jsdom verifies React lifecycle, not top-layer layout/inertness. The separate
  // locked-Chromium fixture exercises the real native dialog implementation.
  boundary.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  boundary.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  Object.assign(globalThis, { window: boundary, document: boundary.document,
    HTMLElement: boundary.HTMLElement, Node: boundary.Node, IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(globalThis, "navigator", { value: boundary.navigator, configurable: true });
  boundary.fetch = transport;
  if (launch) boundary.WebApp = { initData: launch };
  let parentDom;
  if (embedded) {
    parentDom = new JSDOM("<!doctype html>", { url: "https://yleum.ru/" });
    Object.defineProperty(boundary, "parent", { value: parentDom.window });
    parentDom.window.postMessage = (message) => queueMicrotask(() => boundary.dispatchEvent(
      new boundary.MessageEvent("message", { origin: "https://yleum.ru", source: parentDom.window,
        data: { type: "omnia:preview-session:result", nonce: message.nonce,
          url: "https://one.app.test/api/omnia/preview-session?fixture=renew" } }),
    ));
  }
  const cache = new Map();
  const load = path => {
    if (cache.has(path)) return cache.get(path);
    const file = [path, `${path}.ts`, `${path}.tsx`].find(name => existsSync(resolve(base, "src", name)));
    assert.ok(file, `Missing real provider dependency ${path}`);
    const exports = {};
    cache.set(path, exports);
    const source = ts.transpileModule(readFileSync(resolve(base, "src", file), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    vm.runInNewContext(source, { exports, module: { exports }, window: boundary,
      document: boundary.document, navigator: boundary.navigator, console,
      Request, Response, Headers, URL, AbortController, AbortSignal, Error, TextEncoder, TextDecoder,
      setTimeout, clearTimeout, fetch: (...args) => boundary.fetch(...args),
      require: name => {
        if (name === "next/dynamic") return () => ({ children }) => React.createElement(React.Fragment, null, children);
        if (name === "@/components/YleumCompliance") return { YleumCompliance: () => null };
        if (name.startsWith("@/")) return load(name.slice(2));
        return requireTemplate(name);
      },
    }, { filename: file });
    return exports;
  };
  const { MaxAppProvider, useMaxApp } = load("components/MaxAppProvider");
  const late = deferred();
  let mounts = 0;
  function Product() {
    const actor = useMaxApp().user?.id;
    const [name, setName] = React.useState("");
    const [contact, setContact] = React.useState("");
    const [cart, setCart] = React.useState("");
    const [history, setHistory] = React.useState("");
    React.useEffect(() => {
      mounts++;
      if (actor === "42" && mounts === 1) late.promise.then(value => setHistory(value));
    }, []);
    return React.createElement("main", { "data-actor": actor },
      React.createElement("button", { onClick() { setName("A-name"); setContact("A-contact"); setCart("A-cart"); setHistory("A-history"); } }, "Fill draft"),
      React.createElement("input", { "aria-label": "Name", value: name, onChange: event => setName(event.target.value) }),
      React.createElement("input", { "aria-label": "Contact", value: contact, onChange: event => setContact(event.target.value) }),
      React.createElement("output", { "data-cart": true }, cart),
      React.createElement("output", { "data-history": true }, history));
  }
  const { createRoot } = requireTemplate("react-dom/client");
  const element = boundary.document.getElementById("root");
  const root = createRoot(element);
  const flush = () => React.act(async () => { await new Promise(resolve => setImmediate(resolve)); });
  await React.act(async () => { root.render(React.createElement(MaxAppProvider, null, React.createElement(Product))); });
  await flush();
  return { boundary, root, element, late, flush, load, mounts: () => mounts,
    async fill() { await React.act(async () => element.querySelector("main[data-actor] button").click()); },
    async event(name) { await React.act(async () => boundary.dispatchEvent(new boundary.Event(name))); await flush(); },
    async remount(launch) {
      boundary.WebApp = { initData: launch };
      await React.act(async () => root.render(React.createElement(MaxAppProvider, { key: "replacement" }, React.createElement(Product))));
      await flush();
    },
    async close() { await React.act(async () => root.unmount()); dom.window.close(); parentDom?.window.close(); },
  };
}
function visible(element) {
  for (let current = element; current; current = current.parentElement) {
    if (current.hidden || window.getComputedStyle(current).display === "none") return false;
  }
  return true;
}

function cookieServer(initial = "42") {
  let cookie = initial, hold = false, active = 0, maximum = 0;
  const sent = [];
  const transport = (url, options = {}) => {
    const bootstrap = String(url).includes("/api/omnia/preview-session");
    const login = url === "/api/max/session" && options.method === "POST";
    if (!bootstrap && !login) return cookie ? session(cookie, cookie === "preview") : new Response("expired", { status: 401 });
    const actor = bootstrap ? "preview" : JSON.parse(options.body).initData;
    if (!hold) { cookie = actor; return session(actor, actor === "preview"); }
    const deferredResponse = deferred(); active++; maximum = Math.max(maximum, active);
    const request = { actor, signal: options.signal, done: false, complete() {
      assert.equal(request.done, false); request.done = true; active--;
      // The browser applies Set-Cookie when response headers arrive, before
      // the Provider can inspect a body nonce or ignore a stale JSON result.
      cookie = actor; deferredResponse.resolve(session(actor, actor === "preview"));
    } };
    sent.push(request); return deferredResponse.promise;
  };
  return { transport, sent, hold() { hold = true; }, expire() { cookie = ""; }, cookie: () => cookie,
    maximum: () => maximum, pending: actor => sent.find(row => row.actor === actor && !row.done) };
}

async function finishCookieRace(server, app, firstActor, latestActor) {
  const first = server.pending(firstActor), latest = server.pending(latestActor);
  if (latest) {
    latest.complete(); await app.flush(); first.complete(); await app.flush();
  } else {
    first.complete(); await app.flush();
    assert.ok(server.pending(latestActor), "latest live attempt must run after the old headers settle");
    server.pending(latestActor).complete(); await app.flush();
  }
}

test("superseded readonly session GET cannot hold a newer native login hostage", async () => {
  const server = cookieServer(), oldGet = deferred(); let reads = 0, signal;
  const app = await mounted((url, options = {}) => {
    if (url === "/api/max/session" && options.method !== "POST" && ++reads === 2) {
      signal = options.signal; return oldGet.promise; // Deliberately ignores abort.
    }
    return server.transport(url, options);
  });
  try {
    server.hold(); await app.event("focus");
    app.boundary.WebApp = { initData: "43" }; await app.event("omnia:authchange");
    assert.ok(server.pending("43"), "POST B must start without resolving the old readonly GET");
    assert.equal(signal.aborted, true);
    server.pending("43").complete(); await app.flush();
    oldGet.resolve(session("42")); await app.flush();
    assert.equal(server.cookie(), "43");
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
  } finally { await app.close(); }
});

test("superseded readonly recovery probe releases the auth queue without bootstrap replay", async () => {
  const server = cookieServer(""), oldProbe = deferred(); let reads = 0, signal;
  const app = await mounted((url, options = {}) => {
    if (url === "/api/max/session" && options.method !== "POST" && ++reads === 2) {
      signal = options.signal; return oldProbe.promise;
    }
    return server.transport(url, options);
  }, { embedded: true });
  try {
    server.hold(); app.boundary.WebApp = { initData: "43" }; await app.event("omnia:authchange");
    assert.ok(server.pending("43")); assert.equal(signal.aborted, true);
    server.pending("43").complete(); await app.flush();
    oldProbe.resolve(new Response("expired", { status: 401 })); await app.flush();
    assert.equal(server.sent.some(row => row.actor === "preview"), false);
    assert.equal(server.cookie(), "43");
  } finally { await app.close(); }
});

test("readonly cancellation cannot bypass an earlier cookie writer", async () => {
  const server = cookieServer(); let reads = 0;
  const app = await mounted((url, options = {}) => {
    if (url === "/api/max/session" && options.method !== "POST") reads++;
    return server.transport(url, options);
  }, { launch: "42" });
  try {
    const timer = app.boundary.setTimeout.bind(app.boundary);
    app.boundary.setTimeout = (callback, delay, ...args) => timer(callback, Math.min(delay, 1), ...args);
    server.hold(); await app.event("focus");
    app.boundary.WebApp.initData = ""; await app.event("omnia:authchange");
    app.boundary.WebApp.initData = "43"; await app.event("omnia:authchange");
    await new Promise(resolve => setTimeout(resolve, 20)); await app.flush();
    assert.equal(reads, 0); assert.equal(server.pending("43"), undefined);
    assert.equal(server.sent.length, 1);
    server.pending("42").complete(); await app.flush();
    assert.equal(reads, 0, "superseded queued read never starts");
    assert.ok(server.pending("43")); server.pending("43").complete(); await app.flush();
    assert.equal(server.maximum(), 1); assert.equal(server.cookie(), "43");
  } finally { await app.close(); }
});

test("a stalled readonly GET has a finite error gate even if transport ignores abort", async () => {
  let stalled = false, signal;
  const app = await mounted((_url, options) => {
    if (stalled) { signal = options.signal; return new Promise(() => {}); }
    return session("42");
  });
  try {
    await app.fill();
    const timer = app.boundary.setTimeout.bind(app.boundary);
    app.boundary.setTimeout = (callback, delay, ...args) => timer(callback, Math.min(delay, 1), ...args);
    stalled = true; await app.event("focus");
    await React.act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    assert.ok(app.element.querySelector('dialog[open] [role="alert"]'));
    assert.equal(signal.aborted, true);
    stalled = false;
    await React.act(async () => app.element.querySelector("dialog button").click()); await app.flush();
    assert.equal(app.element.querySelector("dialog"), null);
    assert.equal(app.element.querySelector('[aria-label="Name"]').value, "A-name");
  } finally { await app.close(); }
});

test("late Set-Cookie A cannot overwrite B after native login", async () => {
  const server = cookieServer(), app = await mounted(server.transport, { launch: "42" });
  try {
    server.hold(); await app.event("focus");
    app.boundary.WebApp.initData = "43"; await app.event("omnia:authchange");
    await finishCookieRace(server, app, "42", "43");
    assert.equal(server.cookie(), "43"); assert.equal(server.maximum(), 1);
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
  } finally { await app.close(); }
});

test("A-B-A cookie authentication coalesces the unproven queued B attempt", async () => {
  const server = cookieServer(), app = await mounted(server.transport, { launch: "42" });
  try {
    await app.fill(); server.hold(); await app.event("focus");
    app.boundary.WebApp.initData = "43"; await app.event("omnia:authchange");
    app.boundary.WebApp.initData = "42"; await app.event("omnia:authchange");
    for (const row of [...server.sent].reverse()) { row.complete(); await app.flush(); }
    for (const row of server.sent.filter(row => !row.done)) { row.complete(); await app.flush(); }
    assert.equal(server.sent.filter(row => row.actor === "43").length, 0);
    assert.equal(server.maximum(), 1); assert.equal(server.cookie(), "42");
    assert.equal(app.element.querySelector('[aria-label="Name"]').value, "A-name");
  } finally { await app.close(); }
});

test("an unmounted Provider cannot overwrite its replacement's cookie", async () => {
  const server = cookieServer(), app = await mounted(server.transport, { launch: "42" });
  try {
    server.hold(); await app.event("focus"); await app.remount("43");
    await finishCookieRace(server, app, "42", "43");
    assert.equal(server.maximum(), 1); assert.equal(server.cookie(), "43");
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
  } finally { await app.close(); }
});

test("an in-flight preview bootstrap settles its Set-Cookie before a newer native login", async () => {
  const server = cookieServer(""); server.hold();
  const app = await mounted(server.transport, { embedded: true });
  try {
    assert.ok(server.pending("preview"));
    app.boundary.WebApp = { initData: "43" }; await app.event("omnia:authchange");
    await finishCookieRace(server, app, "preview", "43");
    assert.equal(server.maximum(), 1); assert.equal(server.cookie(), "43");
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
  } finally { await app.close(); }
});

test("disposed installed recovery waits for cookie headers instead of trusting abort", async () => {
  const server = cookieServer("preview"), app = await mounted(server.transport, { embedded: true });
  try {
    server.hold(); server.expire();
    const oldRequest = app.boundary.fetch("/api/fixture"); await app.flush();
    const bootstrap = server.pending("preview"); assert.ok(bootstrap);
    await app.remount("43");
    // This transport deliberately does not treat abort as proof that a real
    // browser/network can no longer accept response headers and Set-Cookie.
    assert.equal(bootstrap.signal.aborted, false);
    await finishCookieRace(server, app, "preview", "43");
    await oldRequest;
    assert.equal(server.maximum(), 1); assert.equal(server.cookie(), "43");
  } finally { await app.close(); }
});

test("terminal network failure leaves a finite closed gate and a same-actor retry keeps the draft", async () => {
  let failed = false;
  const app = await mounted(() => failed ? Promise.reject(new TypeError("Fixture network unavailable")) : session("42"));
  try {
    await app.fill(); const mounts = app.mounts(); failed = true;
    await app.event("focus");
    assert.ok(app.element.querySelector('dialog[open] [role="alert"]'));
    assert.equal(visible(app.element.querySelector('[aria-label="Name"]')), false);
    failed = false;
    await React.act(async () => app.element.querySelector("dialog button").click()); await app.flush();
    assert.equal(app.element.querySelector("dialog"), null);
    assert.equal(app.element.querySelector('[aria-label="Name"]').value, "A-name");
    assert.equal(app.mounts(), mounts);
  } finally { await app.close(); }
});

test("startup recovery bypasses an already installed preview guard without queue recursion", async () => {
  const server = cookieServer("preview"), app = await mounted(server.transport, { embedded: true });
  try {
    let result;
    const recovered = app.load("lib/max/owner-preview-renewal").recoverOwnerPreviewSession(app.boundary)
      .then(value => { result = value; });
    await app.flush();
    assert.equal(result, true);
    await recovered;
  } finally { await app.close(); }
});

test("same-actor preview focus does not turn an ordinary guarded read into an authentication failure", async () => {
  const server = cookieServer("preview"), app = await mounted(server.transport, { embedded: true });
  try {
    let read;
    await React.act(async () => {
      app.boundary.dispatchEvent(new app.boundary.Event("focus"));
      read = app.boundary.fetch("/api/fixture");
    });
    assert.equal((await read).status, 200);
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "preview");
  } finally { await app.close(); }
});

test("verified actor change remounts all generated state and late A completion cannot restore it", async () => {
  let actor = "42";
  const app = await mounted(() => session(actor));
  try {
    await app.fill();
    actor = "43";
    await app.event("focus");
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
    assert.equal(app.element.querySelector('[aria-label="Name"]').value, "");
    assert.equal(app.element.querySelector('[aria-label="Contact"]').value, "");
    assert.equal(app.element.querySelector("[data-cart]").textContent, "");
    assert.equal(app.element.querySelector("[data-history]").textContent, "");
    await React.act(async () => app.late.resolve("A-late-receipt"));
    assert.equal(app.element.querySelector("[data-history]").textContent, "");
  } finally { await app.close(); }
});

test("same actor refresh hides uncertain data but preserves draft and mounted subtree", async () => {
  const refresh = deferred(); let calls = 0;
  const app = await mounted(() => ++calls === 1 ? session("42") : refresh.promise);
  try {
    await app.fill(); const mounts = app.mounts();
    await app.event("focus");
    assert.equal(visible(app.element.querySelector('[aria-label="Name"]')), false);
    await React.act(async () => refresh.resolve(session("42"))); await app.flush();
    assert.equal(app.element.querySelector('[aria-label="Name"]').value, "A-name");
    assert.equal(app.element.querySelector('[aria-label="Contact"]').value, "A-contact");
    assert.equal(app.element.querySelector("[data-cart]").textContent, "A-cart");
    assert.equal(app.mounts(), mounts);
    assert.equal(visible(app.element.querySelector('[aria-label="Name"]')), true);
  } finally { await app.close(); }
});

test("A-B-A is a new boundary even when the actor string returns to A", async () => {
  let actor = "42";
  const app = await mounted(() => session(actor));
  try {
    const generation = () => app.load("lib/omnia/identity-scope").getIdentitySnapshot().generation;
    await app.fill(); const first = generation();
    actor = "43"; await app.event("omnia:authchange");
    const second = generation();
    actor = "42"; await app.event("focus");
    assert.ok(first < second && second < generation());
    assert.equal(app.mounts(), 3);
    await React.act(async () => app.late.resolve("old-A-receipt"));
    assert.equal(app.element.querySelector('[aria-label="Contact"]').value, "");
    assert.equal(app.element.querySelector("[data-history]").textContent, "");
  } finally { await app.close(); }
});

test("a current plain 401 remounts the same actor and preserves intent without POST replay", async () => {
  let posts = 0;
  const app = await mounted(url => {
    if (url === "/api/max/session") return session("42");
    posts++; return new Response("expired", { status: 401 });
  });
  try {
    await app.fill();
    const client = app.load("lib/omnia/business-client");
    await React.act(async () => {
      await assert.rejects(client.createManagedLead({ operationKey: "11111111-1111-4111-8111-111111111111",
        customerName: "Fixture", comment: "fixture" }), error => error.code === "unauthorized");
    });
    await app.flush();
    assert.equal(posts, 1);
    assert.equal(app.mounts(), 2);
    assert.equal(app.element.querySelector('[aria-label="Name"]').value, "");
    assert.equal(app.element.querySelector('[aria-label="Contact"]').value, "");
    assert.equal(app.element.querySelector("[data-cart]").textContent, "");
    assert.equal(app.element.querySelector("[data-history]").textContent, "");
    assert.equal(app.boundary.localStorage.length, 1);
    assert.equal((await client.loadPendingManagedIntent("leads")).customerName, "Fixture");
    assert.equal(posts, 1);
  } finally { await app.close(); }
});

test("late A 401 cannot trigger a new authentication or invalidate the real B Provider", async () => {
  let actor = "42", reads = 0, posts = 0; const response = deferred();
  const app = await mounted(url => {
    if (url === "/api/max/session") { reads++; return session(actor); }
    posts++; return response.promise;
  });
  try {
    const pending = app.load("lib/omnia/business-client").createManagedLead({
      operationKey: "11111111-1111-4111-8111-111111111111", customerName: "Fixture", comment: "fixture",
    }).then(() => assert.fail("late receipt exposed"), error => error.code);
    await app.flush();
    actor = "43"; await app.event("focus"); const before = reads;
    response.resolve(new Response("expired", { status: 401 }));
    assert.equal(await pending, "actor_changed"); await app.flush();
    assert.equal(reads, before); assert.equal(posts, 1);
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
    assert.equal(visible(app.element.querySelector('[aria-label="Name"]')), true);
  } finally { await app.close(); }
});

test("an unmounted authentication attempt cannot establish a principal", async () => {
  const response = deferred(), app = await mounted(() => response.promise);
  assert.equal(app.mounts(), 0);
  const scope = app.load("lib/omnia/identity-scope");
  await app.close(); const disposed = scope.getIdentitySnapshot();
  response.resolve(session("42")); await app.flush();
  assert.equal(scope.getIdentitySnapshot(), disposed);
  assert.equal(disposed.principal, null);
});

test("launch transport attaches signed data only to same-origin API calls and cleans up", async () => {
  const calls = [];
  const transport = (input, options) => {
    calls.push({ input, options }); return session("42");
  };
  const app = await mounted(transport, { launch: "fixture-launch-only" });
  try {
    const native = calls[0];
    assert.equal(new Headers(native.options.headers).get("X-Omnia-MAX-Init-Data"), "fixture-launch-only");
    for (const path of ["/api/fixture", "https://external.invalid/api/fixture", "/asset"]) await app.boundary.fetch(path);
    assert.equal(new Headers(calls[1].options.headers).get("X-Omnia-MAX-Init-Data"), "fixture-launch-only");
    assert.equal(calls[2].options, undefined);
    assert.equal(calls[3].options, undefined);
    app.boundary.WebApp.initData = "";
    await app.event("focus");
    assert.equal(calls.at(-1).options.headers, undefined);
  } finally { await app.close(); }
  assert.equal(app.boundary.fetch, transport);
});

test("stale authentication result cannot replace a newer actor", async () => {
  const older = deferred(); let calls = 0;
  const app = await mounted(() => ++calls === 1 ? session("42") : calls === 2 ? older.promise : session("43"));
  try {
    await app.fill(); await app.event("focus"); await app.event("omnia:authchange");
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
    await React.act(async () => older.resolve(session("42"))); await app.flush();
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
    assert.equal(app.element.querySelector('[aria-label="Name"]').value, "");
  } finally { await app.close(); }
});

test("authentication JSON decoded after a newer actor cannot replace its principal", async () => {
  const reading = deferred(); let body, calls = 0;
  const response = new Response(new ReadableStream({ start(controller) { body = controller; } }));
  const json = response.json.bind(response);
  response.json = () => { reading.resolve(); return json(); };
  const app = await mounted(() => ++calls === 1 ? session("42") : calls === 2 ? response : session("43"));
  try {
    await app.event("focus"); await reading.promise;
    await app.event("omnia:authchange");
    body.enqueue(new TextEncoder().encode(JSON.stringify({ user: { id: "42" } }))); body.close();
    await app.flush();
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
  } finally { await app.close(); }
});

test("visibility revalidates when returning to a visible page, not while hidden", async () => {
  let actor = "42", reads = 0;
  const app = await mounted(() => { reads++; return session(actor); });
  try {
    await app.fill(); actor = "43";
    const change = async state => {
      Object.defineProperty(app.boundary.document, "visibilityState", { value: state, configurable: true });
      await React.act(async () => app.boundary.document.dispatchEvent(new app.boundary.Event("visibilitychange")));
      await app.flush();
    };
    await change("hidden"); assert.equal(reads, 1);
    await change("visible"); assert.equal(reads, 2);
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "43");
    assert.equal(app.element.querySelector('[aria-label="Contact"]').value, "");
  } finally { await app.close(); }
});

test("preview recovery reuses scoped proof and never replays a business POST", async () => {
  let renewed = false, posts = 0;
  const transport = (url, options = {}) => {
    if (String(url).startsWith("https://one.app.test/api/omnia/preview-session")) { renewed = true; return session("preview", true); }
    if (url === "/api/max/session") return renewed ? session("preview", true) : new Response("expired", { status: 401 });
    posts++; return new Response("expired", { status: 401 });
  };
  const app = await mounted(transport, { embedded: true });
  try {
    assert.equal(app.element.querySelector("main[data-actor]")?.dataset.actor, "preview");
    const response = await app.boundary.fetch("/api/omnia/business/leads", { method: "POST", body: "fixture" });
    assert.equal(response.status, 401);
    assert.equal(posts, 1);
  } finally { await app.close(); }
});
