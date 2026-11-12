import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import { ClubDemo } from "@/components/marketing/landing/ClubDemo";
import { PhoneFrame } from "@/components/marketing/landing/ScreenChrome";

let host: HTMLDivElement;
let root: Root;

beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(
    <PhoneFrame><ClubDemo now={Date.parse("2026-11-12T10:00:00Z")} /></PhoneFrame>,
  ));
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

function button(label: string, scope: ParentNode = host) {
  const control = [...scope.querySelectorAll<HTMLButtonElement>("button")].find(element =>
    (element.getAttribute("aria-label") ?? element.textContent)?.includes(label),
  );
  expect(control, label).toBeDefined();
  return control!;
}

async function click(control: HTMLButtonElement) {
  await act(async () => control.click());
}

function dialog(title: string) {
  const panel = host.querySelector<HTMLElement>('[role="dialog"]');
  expect(panel).not.toBeNull();
  expect(document.getElementById(panel!.getAttribute("aria-labelledby")!)?.textContent).toBe(title);
  return panel!;
}

it("opens upcoming and past rosters without replacing the phone body and restores focus on Escape", async () => {
  await click(button("Записаться на встречу"));
  const upcoming = button("15 участников");
  const phoneBody = host.querySelector<HTMLElement>(".ys-screen-body")!;
  phoneBody.scrollTop = 70;
  upcoming.focus();
  await click(upcoming);

  const roster = dialog("Участники встречи");
  expect(roster.querySelectorAll("li")).toHaveLength(15);
  expect(roster.textContent).toContain("Вы");
  expect(upcoming.closest("[inert]")).not.toBeNull();
  const close = button("Закрыть окно", roster);
  expect(document.activeElement).toBe(close);
  await act(async () => close.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true })));
  const list = roster.querySelector<HTMLElement>('[role="region"]')!;
  expect(document.activeElement).toBe(list);
  await act(async () => list.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })));
  expect(document.activeElement).toBe(close);
  await act(async () => close.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(upcoming);
  expect(host.querySelector(".ys-screen-body")).toBe(phoneBody);
  expect(phoneBody.scrollTop).toBe(70);

  const past = button("«Мартин Иден»");
  await click(past);
  const pastRoster = dialog("Участники встречи");
  expect(pastRoster.textContent).toContain("«Мартин Иден»");
  expect(pastRoster.querySelectorAll("li")).toHaveLength(12);
  await click(button("Закрыть окно", pastRoster));
  expect(document.activeElement).toBe(past);
});

it("keeps a custom ballot draft and confirmation across closing and reopening the window", async () => {
  const title = "Обсуждаемая книга на октябрь";
  const vote = button(title);
  await click(vote);
  const ballot = dialog(title);
  const custom = [...ballot.querySelectorAll("label")].find(label => label.textContent === "Свой вариант")!;
  await act(async () => custom.click());
  const input = ballot.querySelector<HTMLInputElement>('input[type="text"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "  Дюна  ");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click(button("Закрыть окно", ballot));

  await click(vote);
  const reopened = dialog(title);
  expect(reopened.querySelector<HTMLInputElement>('input[type="text"]')?.value).toBe("  Дюна  ");
  await click(button("Проголосовать", reopened));
  expect(reopened.querySelector('[role="status"]')?.textContent).toBe("Голос принят. Вы выбрали «Дюна».");
  await click(button("Закрыть окно", reopened));

  await click(vote);
  expect(dialog(title).querySelector('[role="status"]')?.textContent).toBe("Голос принят. Вы выбрали «Дюна».");
});
