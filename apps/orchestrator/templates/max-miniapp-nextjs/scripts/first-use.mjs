import assert from "node:assert/strict";

/**
 * Run the product's existing test from its shipped entry. openVisitor must
 * open a fresh client/page for the same isolated actor each time, without
 * inserting app content. Handles own their session and expose close().
 * Browser and route-level tests can both supply their normal session fixture.
 */
export async function exerciseFirstUse({
  name, openVisitor, perform, expectResult, ownerSetup, persists = true,
}) {
  const seen = new WeakSet();
  async function visit(stage, open, use) {
    try {
      const session = await open();
      assert.ok(session && typeof session.close === "function", "Expected a closable test session");
      assert.ok(!seen.has(session), "Expected a fresh test session");
      seen.add(session);
      let value;
      try {
        value = await use(session);
      } catch (error) {
        // Teardown must not replace the failure of the customer's action.
        try { await session.close(); } catch { /* retain the primary error */ }
        throw error;
      }
      await session.close();
      return value;
    } catch (error) {
      // Preserve node:assert expected/actual, Playwright locator details and
      // the original error code for the existing product test diagnostics.
      if (error instanceof Error) error.message += `\nFirst use: ${name} / ${stage}`;
      throw error;
    }
  }

  async function verifyResult(session, value) {
    // Assertion-style checks resolve to undefined. A false predicate is a failure.
    assert.notStrictEqual(await expectResult(session, value), false,
      "Expected the primary action's visible result");
  }

  if (ownerSetup) {
    await visit("before owner setup", openVisitor, async (session) => {
      assert.notStrictEqual(await ownerSetup.expectUnavailable(session), false,
        "Expected the owner prerequisite to be missing");
    });
    await visit("owner setup", ownerSetup.openOwner, ownerSetup.configure);
  }
  const result = await visit("first action", openVisitor, async (visitor) => {
    const value = await perform(visitor);
    await verifyResult(visitor, value);
    return value;
  });
  if (persists) {
    await visit("reopen", openVisitor, (visitor) => verifyResult(visitor, result));
  }
  return result;
}
