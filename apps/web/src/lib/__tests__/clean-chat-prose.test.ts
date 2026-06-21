import { describe, expect, it } from "vitest";
import { cleanChatProse, parseAssistantContent } from "@/lib/parse-assistant";

describe("cleanChatProse", () => {
  it("keeps ordinary prose", () => {
    expect(cleanChatProse("Готово, обновил заголовок.")).toBe(
      "Готово, обновил заголовок.",
    );
  });

  it("strips a fenced code block but keeps the sentence", () => {
    expect(cleanChatProse("Применил.\n```html\n<div>x</div>\n```")).toBe(
      "Применил.",
    );
  });

  it("blanks a bare-HTML code dump", () => {
    const dump =
      "<section class='hero'><h1>Заголовок</h1><p>Текст</p></section>";
    expect(cleanChatProse(dump)).toBe("");
  });

  it("blanks a full HTML document dump", () => {
    expect(cleanChatProse("<!doctype html><html><body>x</body></html>")).toBe(
      "",
    );
  });

  it("keeps a short status line with no code", () => {
    expect(cleanChatProse("*Пересобираю страницу…*")).toBe(
      "*Пересобираю страницу…*",
    );
  });
});

describe("parseAssistantContent still chips edit blocks", () => {
  it("splits an <edit> block out of prose", () => {
    const content =
      'Готово!\n<edit path="index.html">\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n</edit>';
    const parts = parseAssistantContent(content);
    expect(parts.some((p) => p.kind === "edit")).toBe(true);
  });
});
