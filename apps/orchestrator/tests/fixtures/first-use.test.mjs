import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import test from "node:test";
import { exerciseFirstUse } from "../../templates/max-miniapp-nextjs/scripts/first-use.mjs";

test("a visitor books the owner's published slot and finds that booking on return", async (t) => {
  const slots = [];
  const bookings = [];
  const server = createServer((req, res) => {
    const actor = req.headers["x-test-actor"];
    if (req.method === "POST" && req.url === "/slots" && actor === "owner") {
      slots.push("10:00");
    } else if (req.method === "POST" && req.url === "/book" && slots.length) {
      bookings.push({ actor, slot: slots.shift() });
    } else if (req.method !== "GET" || req.url !== "/") {
      res.writeHead(403).end();
      return;
    }
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ slots, bookings: bookings.filter(row => row.actor === actor) }));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => new Promise(resolve => server.close(resolve)));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const visits = [];
  const open = actor => async () => {
    visits.push(actor);
    const abort = new AbortController();
    const request = (path = "/", method = "GET") => fetch(new URL(path, origin), {
      method, headers: { "x-test-actor": actor }, signal: abort.signal,
    });
    const state = async () => (await request()).json();
    const entry = await state();
    return { request, state, entry, close: () => abort.abort() };
  };
  await exerciseFirstUse({
    name: "book a visit", openVisitor: open("visitor"),
    ownerSetup: {
      openOwner: open("owner"),
      expectUnavailable: async visitor => {
        assert.deepEqual(visitor.entry.slots, []);
        assert.equal((await visitor.request("/slots", "POST")).status, 403);
      },
      configure: async owner => assert.equal((await owner.request("/slots", "POST")).status, 200),
    },
    perform: async visitor => {
      assert.deepEqual(visitor.entry.slots, ["10:00"]);
      assert.equal((await visitor.request("/book", "POST")).status, 200);
      return { actor: "visitor", slot: "10:00" };
    },
    expectResult: async (visitor, booking) => assert.deepEqual((await visitor.state()).bookings, [booking]),
  });
  assert.deepEqual(visits, ["visitor", "owner", "visitor", "visitor"]);
  assert.equal(bookings.length, 1);
});

test("a result lost after reopen retains expected, actual and the failing stage", async () => {
  let count = 0;
  await assert.rejects(exerciseFirstUse({
    name: "save plan", openVisitor: () => ({ plan: null, close() {} }),
    perform(visitor) { count++; visitor.plan = "saved"; },
    expectResult(visitor) { assert.equal(visitor.plan, "saved"); },
  }), err => err.code === "ERR_ASSERTION" && err.expected === "saved" &&
    err.actual === null && err.message.includes("save plan / reopen"));
  assert.equal(count, 1);
});

test("a rejected primary action retains its diagnostic if closing also fails", async () => {
  const original = Object.assign(new Error("locator.click: Timeout 100ms exceeded"), { code: "ETIMEDOUT" });
  await assert.rejects(exerciseFirstUse({
    name: "open lesson", openVisitor: () => ({ close() { throw Error("transport lost"); } }),
    perform() { throw original; }, expectResult() { assert.fail("no result"); },
  }), err => err === original && err.code === "ETIMEDOUT" &&
    err.message.startsWith("locator.click: Timeout") && err.message.includes("open lesson / first action"));
});

test("a read-only action does not open a second session", async () => {
  let opens = 0;
  await exerciseFirstUse({
    name: "read article", persists: false,
    openVisitor() { opens++; return { close() {} }; },
    perform: () => "article", expectResult: (_, article) => assert.equal(article, "article"),
  });
  assert.equal(opens, 1);
});

test("false predicates stop at the unfulfilled product assertion", async () => {
  const openVisitor = () => ({ close() {} });
  for (const stage of ["before owner setup", "first action", "reopen"]) {
    const actions = [];
    let views = 0;
    await assert.rejects(exerciseFirstUse({
      name: "visit", openVisitor,
      ownerSetup: {
        openOwner: openVisitor,
        expectUnavailable: () => stage !== "before owner setup",
        configure: () => actions.push("configured"),
      },
      perform: () => actions.push("performed"),
      expectResult: () => ++views === 1 && stage === "reopen",
    }), err => err.code === "ERR_ASSERTION" && err.actual === false &&
      err.message.endsWith(`visit / ${stage}`));
    assert.deepEqual(actions, stage === "before owner setup" ? [] : ["configured", "performed"]);
  }
});
