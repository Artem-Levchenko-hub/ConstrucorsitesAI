import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Владелец дважды просил одно и то же: на экранах входа и регистрации не
 * объяснять, как выглядит email и пароль. Человек, который открыл форму входа,
 * это знает; человек, который регистрируется, тем более не нуждается в примере
 * «name@company.ru» внутри поля.
 *
 * Подсказка внутри поля — не безобидное украшение: она исчезает при первом
 * нажатии клавиши, поэтому переспросить «а что тут было?» уже нельзя, и её
 * нередко принимают за уже введённое значение.
 *
 * Проверка смотрит на исходники, а не на отрисовку: эти формы — серверные
 * действия с состоянием, поднимать их целиком ради одного атрибута дороже, чем
 * польза от такой проверки.
 */
const FORMS = [
  "src/components/auth/LoginForm.tsx",
  "src/components/max/MaxRegisterForm.tsx",
];

const root = join(__dirname, "..", "..", "..");

describe("формы входа и регистрации", () => {
  it.each(FORMS)("в %s нет подсказок внутри полей", (file) => {
    const source = readFileSync(join(root, file), "utf8");

    expect(source).not.toContain("placeholder");
  });

  it("проверка читает настоящие файлы, а не пустоту", () => {
    for (const file of FORMS) {
      const source = readFileSync(join(root, file), "utf8");
      expect(source.length).toBeGreaterThan(500);
      // Файл должен действительно быть формой с полями, иначе проверка ничего не стоит.
      expect(source).toContain("name=\"email\"");
    }
  });
});
