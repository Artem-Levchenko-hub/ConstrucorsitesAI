import { MessagesSquare, History, Rocket } from "lucide-react";

const FEATURES = [
  {
    icon: MessagesSquare,
    title: "Чат → сайт",
    body: "Просто опиши, что хочешь. AI пишет HTML/CSS/JS, мы коммитим в git и сразу показываем превью.",
  },
  {
    icon: History,
    title: "Лента версий",
    body: "Каждый промпт = коммит. Не понравился результат — один клик, и сайт откатывается на любую версию.",
  },
  {
    icon: Rocket,
    title: "Деплой одной кнопкой",
    body: "Поддомен `*.omnia.ai` сразу после первого промпта. Свой домен — в один клик.",
  },
];

export function Features() {
  return (
    <section
      id="features"
      className="border-b border-border-subtle"
    >
      <div className="mx-auto max-w-6xl px-6 py-24">
        <div className="max-w-2xl mb-16">
          <h2 className="text-3xl font-semibold tracking-tight mb-4">
            Платформа, а не плагин
          </h2>
          <p className="text-fg-secondary">
            От промпта до домена в продакшне — без переключений между Wix,
            Hostinger, GitHub и фрилансером.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="rounded-lg border border-border-default bg-surface-raised p-6 space-y-4"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-md border border-accent/40 bg-accent-subtle">
                <f.icon className="h-5 w-5 text-accent" />
              </div>
              <h3 className="text-lg font-medium">{f.title}</h3>
              <p className="text-sm text-fg-secondary leading-6">{f.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
