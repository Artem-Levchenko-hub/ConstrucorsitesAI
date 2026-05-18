/**
 * Default landing for a freshly provisioned project.
 *
 * AI will replace this file when the user sends their first prompt. The page
 * exists so the iframe in the Omnia workspace has something to render
 * immediately after provision, before any AI write happens.
 */
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 px-6">
      <p className="text-sm uppercase tracking-widest text-zinc-500">
        Готово к работе
      </p>
      <h1 className="text-4xl font-semibold tracking-tight">
        Новый проект на Omnia.AI
      </h1>
      <p className="text-lg text-zinc-400">
        Это стартовый шаблон Next.js + Postgres. Напишите промпт слева —
        AI добавит страницы, таблицы и логику. Перезагрузка не нужна:
        изменения подхватятся через HMR.
      </p>
      <p className="text-sm text-zinc-500">
        Шаблон: <code className="font-mono">nextjs-postgres-drizzle</code>
      </p>
    </main>
  );
}
