/**
 * Default landing for a freshly provisioned full-stack project.
 *
 * This file lives in the api-side template snapshot so that on the very
 * first prompt the LLM sees the Next.js layout in `current_files` and
 * generates code that matches the project's real filesystem (the same
 * file is also baked into the orchestrator dev container at provision
 * time). AI will overwrite it after the first prompt.
 */
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 px-6">
      <p className="text-sm uppercase tracking-widest text-zinc-500">
        Готово к работе
      </p>
      <h1 className="text-4xl font-semibold tracking-tight">
        Новый full-stack проект на Omnia.AI
      </h1>
      <p className="text-lg text-zinc-400">
        Стек: Next.js 15 + Postgres + Drizzle ORM. Напишите промпт — AI
        добавит страницы, таблицы и server actions. HMR подхватит изменения
        без перезагрузки.
      </p>
    </main>
  );
}
