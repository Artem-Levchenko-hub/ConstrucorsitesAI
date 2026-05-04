import Link from "next/link";

const COLUMNS = [
  {
    heading: "Продукт",
    links: [
      { label: "Возможности", href: "#features" },
      { label: "Цены", href: "#pricing" },
      { label: "Демо", href: "/register" },
      { label: "Дорожная карта", href: "#" },
    ],
  },
  {
    heading: "Ресурсы",
    links: [
      { label: "Документация", href: "#" },
      { label: "Шаблоны", href: "#" },
      { label: "Блог", href: "#" },
      { label: "Status", href: "#" },
    ],
  },
  {
    heading: "Юридическое",
    links: [
      { label: "Оферта", href: "#" },
      { label: "Конфиденциальность", href: "#" },
      { label: "Cookies", href: "#" },
      { label: "Безопасность", href: "#" },
    ],
  },
  {
    heading: "Контакты",
    links: [
      { label: "hello@omnia.ai", href: "mailto:hello@omnia.ai" },
      { label: "Telegram", href: "#" },
      { label: "Партнёрам", href: "#" },
      { label: "Помощь", href: "#" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="bg-surface-raised">
      <div className="mx-auto max-w-6xl px-6 py-16">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-8">
          <div className="col-span-2 md:col-span-1">
            <Link
              href="/"
              className="flex items-center gap-2 text-fg-primary font-semibold tracking-tight"
            >
              <span className="inline-block h-5 w-5 rounded-md bg-accent" />
              <span>Omnia.AI</span>
            </Link>
            <p className="text-xs text-fg-tertiary mt-3 leading-5">
              Промпт → сайт.<br />
              Made with care в РФ.
            </p>
          </div>

          {COLUMNS.map((col) => (
            <div key={col.heading} className="space-y-3">
              <div className="text-xs font-mono text-fg-tertiary uppercase tracking-wider">
                {col.heading}
              </div>
              <ul className="space-y-2">
                {col.links.map((l) => (
                  <li key={l.label}>
                    <Link
                      href={l.href}
                      className="text-sm text-fg-secondary hover:text-fg-primary transition"
                    >
                      {l.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-12 pt-6 border-t border-border-subtle flex flex-col md:flex-row items-center justify-between gap-3 text-xs text-fg-tertiary">
          <span>© 2026 Omnia.AI. Все права защищены.</span>
          <span className="font-mono">v0.1.0 · beta</span>
        </div>
      </div>
    </footer>
  );
}
