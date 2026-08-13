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
  it("ignores stale atomic and legacy enables after a newer off transition", () => {
    const shell = new JSDOM('<iframe id="preview"></iframe>', {
      runScripts: "dangerously",
      url: "https://constructor.example/workspace",
    });
    const preview = shell.window.document.querySelector("iframe")
      ?.contentWindow as DOMWindow | null | undefined;
    if (!preview) throw new Error("test iframe did not initialise");
    preview.document.body.innerHTML = '<button id="product">Товар</button>';
    const appClicks: string[] = [];
    preview.document
      .querySelector("#product")
      ?.addEventListener("click", () => appClicks.push("product"));
    const parentPost = vi
      .spyOn(shell.window, "postMessage")
      .mockImplementation(() => undefined);
    preview.eval(inspectorSource);
    const send = (data: Record<string, unknown>) =>
      preview.dispatchEvent(
        new preview.MessageEvent("message", {
          data,
          source: shell.window,
          origin: "https://constructor.example",
        }),
      );
    const envelope = { editorSession: "workspace-rapid", seq: 1 };

    send({ type: "omnia:editor:set-mode", mode: "inspect", ...envelope });
    send({
      type: "omnia:editor:set-mode",
      mode: "off",
      editorSession: envelope.editorSession,
      seq: 2,
    });
    send({ type: "omnia:inspect:enable", ...envelope });
    send({
      type: "omnia:editor:set-mode",
      mode: "inspect",
      ...envelope,
    });

    for (let index = 0; index < 5; index += 1) {
      preview.document.querySelector("#product")?.dispatchEvent(
        new preview.MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    }

    expect(appClicks).toHaveLength(5);
    expect(preview.document.documentElement.style.cursor).toBe("");
    const states = parentPost.mock.calls
      .map(([message]) => message as Record<string, unknown>)
      .filter((message) => message.type === "omnia:editor:state");
    expect(states).toHaveLength(4);
    expect(states.at(-1)).toEqual(
      expect.objectContaining({
        mode: "off",
        editorSession: envelope.editorSession,
        seq: 2,
      }),
    );
    shell.window.close();
  });

  it("does not return to a retired parent editor session", () => {
    const shell = new JSDOM('<iframe id="preview"></iframe>', {
      runScripts: "dangerously",
      url: "https://constructor.example/workspace",
    });
    const preview = shell.window.document.querySelector("iframe")
      ?.contentWindow as DOMWindow | null | undefined;
    if (!preview) throw new Error("test iframe did not initialise");
    preview.eval(inspectorSource);
    const send = (data: Record<string, unknown>) =>
      preview.dispatchEvent(
        new preview.MessageEvent("message", {
          data,
          source: shell.window,
          origin: "https://constructor.example",
        }),
      );

    send({
      type: "omnia:editor:set-mode",
      mode: "inspect",
      editorSession: "old-workspace",
      seq: 10,
    });
    send({
      type: "omnia:editor:set-mode",
      mode: "off",
      editorSession: "new-workspace",
      seq: 1,
    });
    send({
      type: "omnia:editor:set-mode",
      mode: "inspect",
      editorSession: "old-workspace",
      seq: 11,
    });

    expect(preview.document.documentElement.style.cursor).toBe("");
    shell.window.close();
  });

  it("leaves repeated normal navigation entirely inside the application", () => {
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
    const documentListener = vi.spyOn(preview.document, "addEventListener");

    preview.eval(inspectorSource);
    const messagesBeforeClicks = parentPost.mock.calls.length;
    expect(
      documentListener.mock.calls.filter(([type]) =>
        ["click", "change", "pointerdown", "mousemove"].includes(type),
      ),
    ).toHaveLength(0);
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
    expect(parentPost.mock.calls).toHaveLength(messagesBeforeClicks);
    shell.window.close();
  });

  it("keeps the visible responsive preview off when a stale hidden preview is armed", () => {
    const shell = new JSDOM(
      '<iframe id="hidden-desktop"></iframe><iframe id="visible-drawer"></iframe>',
      {
        runScripts: "dangerously",
        url: "https://constructor.example/workspace",
      },
    );
    const hidden = shell.window.document.querySelector<HTMLIFrameElement>(
      "#hidden-desktop",
    )?.contentWindow as DOMWindow | null | undefined;
    const visible = shell.window.document.querySelector<HTMLIFrameElement>(
      "#visible-drawer",
    )?.contentWindow as DOMWindow | null | undefined;
    if (!hidden || !visible) throw new Error("test previews did not initialise");

    for (const preview of [hidden, visible]) {
      preview.document.body.innerHTML =
        '<button id="stats">Статистика</button><button id="workouts">Тренировки</button>';
      preview.eval(inspectorSource);
    }

    // This reproduces the former responsive composition: a CSS-hidden desktop
    // iframe was still alive when the drawer iframe mounted. Editor commands
    // must be owned by one frame; a stale mode in the old branch cannot arm the
    // visible application.
    hidden.dispatchEvent(
      new hidden.MessageEvent("message", {
        data: { type: "omnia:editor:set-mode", mode: "inspect" },
        source: shell.window,
        origin: "https://constructor.example",
      }),
    );
    visible.dispatchEvent(
      new visible.MessageEvent("message", {
        data: { type: "omnia:editor:set-mode", mode: "off" },
        source: shell.window,
        origin: "https://constructor.example",
      }),
    );

    const screens: string[] = [];
    visible.document
      .querySelector("#stats")
      ?.addEventListener("click", () => screens.push("stats"));
    visible.document
      .querySelector("#workouts")
      ?.addEventListener("click", () => screens.push("workouts"));
    for (const id of ["#stats", "#workouts"]) {
      visible.document.querySelector(id)?.dispatchEvent(
        new visible.MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    }

    expect(screens).toEqual(["stats", "workouts"]);
    expect(visible.document.documentElement.style.cursor).toBe("");
    expect(hidden.document.documentElement.style.cursor).toBe("crosshair");
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
