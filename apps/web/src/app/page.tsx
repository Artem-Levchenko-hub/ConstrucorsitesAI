import { Button } from "@/components/ui/button";

export default function HomePage() {
  return (
    <main className="min-h-svh flex items-center justify-center px-6">
      <div className="max-w-2xl mx-auto text-center space-y-6">
        <span className="inline-flex items-center px-3 py-1 rounded-sm border border-border-default text-fg-secondary text-xs font-mono">
          Beta · Запуск октябрь 2026
        </span>

        <h1 className="text-5xl font-semibold tracking-tight">
          Пиши промпты,
          <br />
          получай готовый сайт.
        </h1>

        <p className="text-fg-secondary text-base">
          AI-сайт-билдер с backend, доменом и кнопкой «вернуться назад» для
          каждого промпта. Всё в рублях.
        </p>

        <div className="pt-4">
          <Button size="lg">Начать бесплатно</Button>
        </div>
      </div>
    </main>
  );
}
