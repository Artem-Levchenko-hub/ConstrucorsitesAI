import { expect, it } from "vitest";
import { readMaxEditorEntry, maxEditorLinkEntry } from "@/lib/max-editor-entry";

it("recognizes every dialog and ignores unknown query values", () => {
  for (const panel of ["max", "services", "publish"]) expect(readMaxEditorEntry(new URLSearchParams(`panel=${panel}`))).toBe(panel);
  // Own-server hosting moved out with the site builder: a saved link must not land on a blank editor.
  expect(readMaxEditorEntry(new URLSearchParams("panel=hosting"))).toBe("publish");
  expect(readMaxEditorEntry(new URLSearchParams("data=owner"))).toBe("data:owner");
  expect(readMaxEditorEntry(new URLSearchParams("panel=unknown"))).toBeNull();
  expect(readMaxEditorEntry(new URLSearchParams("panel=max&data=policies"))).toBe("data:policies");
});

it("handles only this project's editor links, preserving external and management navigation", () => {
  const origin = "https://studio.example";
  expect(maxEditorLinkEntry("/max/p?panel=max", "p", origin)).toBe("max");
  expect(maxEditorLinkEntry("/max/p?data=content", "p", origin)).toBe("data:content");
  expect(maxEditorLinkEntry("/max/p", "p", origin)).toBeNull();
  for (const href of ["/max/other?panel=max", "/max/p/dashboard", "https://other.example/max/p?panel=max", "/max/p?starter=1", "/max/p?panel=bogus"])
    expect(maxEditorLinkEntry(href, "p", origin)).toBeUndefined();
});
