import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import postcss from "postcss";

import { describe, expect, it } from "vitest";

const styles = readFileSync(
  resolve(process.cwd(), "src/components/max/max-studio.css"),
  "utf8",
);

describe("MAX project card contrast", () => {
  it("keeps the management action readable before and during hover", () => {
    const css = postcss.parse(styles);
    const declaration = (selector: string, property: string) => {
      let value = "";
      css.walkRules(selector, rule => { rule.walkDecls(property, decl => { value = decl.value; }); });
      return value;
    };
    const color = (value: string): string => value.startsWith("var(") ? declaration("[data-max-studio]", value.slice(4, -1)) : value;
    const luminance = (hex: string) => {
      const rgb = hex.length === 4 ? hex.slice(1).split("").map(value => value + value).join("") : hex.slice(1);
      return [0, 2, 4].map(index => parseInt(rgb.slice(index, index + 2), 16) / 255)
        .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
        .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    };
    const foreground = luminance(color(declaration(".max-project-next", "color")));
    for (const background of [declaration(".max-projects-list", "background"), declaration(".max-project-next:hover", "background")]) {
      const surface = luminance(color(background));
      expect((Math.max(foreground, surface) + .05) / (Math.min(foreground, surface) + .05)).toBeGreaterThanOrEqual(4.5);
    }
  });
});
