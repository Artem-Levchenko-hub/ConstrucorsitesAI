import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { JSDOM, type DOMWindow } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const inspectorSource = readFileSync(
  resolve(
    process.cwd(),
    "../api/src/omnia_api/static/omnia-inspector.js",
  ),
  "utf8",
);

describe("canonical inspector ordinary viewing mode", () => {
  it("never intercepts normal navigation and reports lightweight activity", () => {
    const shell = new JSDOM('<iframe id="preview"></iframe>', {
      runScripts: "dangerously",
      url: "https://constructor.example/workspace",
    });
    const frame = shell.window.document.querySelector("iframe");
    const preview = frame?.contentWindow as DOMWindow | null | undefined;
    if (!preview) throw new Error("test iframe did not initialise");

    preview.document.body.innerHTML =
      '<button id="stats">Статистика</button><button id="profile">Профиль</button>';
    const screens: string[] = [];
    preview.document
      .querySelector("#stats")
      ?.addEventListener("click", () => screens.push("stats"));
    preview.document
      .querySelector("#profile")
      ?.addEventListener("click", () => screens.push("profile"));
    const parentPost = vi
      .spyOn(shell.window, "postMessage")
      .mockImplementation(() => undefined);

    preview.eval(inspectorSource);
    for (const id of ["#stats", "#profile"]) {
      preview.document.querySelector(id)?.dispatchEvent(
        new preview.MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    }

    expect(screens).toEqual(["stats", "profile"]);
    expect(
      parentPost.mock.calls.filter(
        ([message]) =>
          (message as { type?: string } | undefined)?.type === "omnia:pick",
      ),
    ).toHaveLength(0);
    expect(parentPost).toHaveBeenCalledWith(
      { type: "omnia:preview:activity" },
      "*",
    );
    shell.window.close();
  });
});

describe.each(["inspect", "style"] as const)(
  "canonical inspector single-shot %s mode",
  (mode) => {
    it("emits one pick, disables capture, and lets the next app click through", () => {
      const shell = new JSDOM('<iframe id="preview"></iframe>', {
        runScripts: "dangerously",
        url: "https://constructor.example/workspace",
      });
      const frame = shell.window.document.querySelector("iframe");
      const preview = frame?.contentWindow as DOMWindow | null | undefined;
      if (!preview) throw new Error("test iframe did not initialise");

      preview.document.body.innerHTML = '<button id="tab">Каталог</button>';
      const appEvents: string[] = [];
      const button = preview.document.querySelector<HTMLButtonElement>("#tab");
      if (!button) throw new Error("test button did not initialise");
      button.addEventListener("pointerdown", () => appEvents.push("pointerdown"));
      button.addEventListener("click", () => appEvents.push("click"));

      const parentPost = vi
        .spyOn(shell.window, "postMessage")
        .mockImplementation(() => undefined);
      preview.eval(inspectorSource);
      preview.dispatchEvent(
        new preview.MessageEvent("message", {
          data: {
            type:
              mode === "style"
                ? "omnia:style:enable"
                : "omnia:inspect:enable",
          },
          source: shell.window,
          origin: "https://constructor.example",
        }),
      );

      button.dispatchEvent(
        new preview.MouseEvent("pointerdown", { bubbles: true, cancelable: true }),
      );
      button.dispatchEvent(
        new preview.MouseEvent("click", { bubbles: true, cancelable: true }),
      );

      expect(appEvents).toEqual([]);
      expect(
        parentPost.mock.calls.filter(
          ([message]) =>
            (message as { type?: string } | undefined)?.type === "omnia:pick",
        ),
      ).toHaveLength(1);
      expect(parentPost).toHaveBeenCalledWith(
        expect.objectContaining({ type: "omnia:editor:state", mode: "off" }),
        "*",
      );

      button.dispatchEvent(
        new preview.MouseEvent("pointerdown", { bubbles: true, cancelable: true }),
      );
      button.dispatchEvent(
        new preview.MouseEvent("click", { bubbles: true, cancelable: true }),
      );

      expect(appEvents).toEqual(["pointerdown", "click"]);
      expect(
        parentPost.mock.calls.filter(
          ([message]) =>
            (message as { type?: string } | undefined)?.type === "omnia:pick",
        ),
      ).toHaveLength(1);
      shell.window.close();
    });
  },
);
