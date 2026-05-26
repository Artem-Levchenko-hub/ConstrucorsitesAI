/**
 * Default landing for a freshly provisioned Vite SPA project.
 *
 * AI replaces this on the first user prompt. The route table here is the
 * one place that needs to be in sync between AI-generated pages and
 * `react-router-dom` — every new page goes in a new `<Route>` here.
 */

import { Routes, Route } from "react-router-dom";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
    </Routes>
  );
}

function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 px-6">
      <p className="text-sm uppercase tracking-widest text-zinc-500">
        Готово к работе
      </p>
      <h1 className="text-4xl font-semibold tracking-tight">
        Новый проект на Omnia.AI
      </h1>
      <p className="text-lg text-zinc-400">
        Это стартовый шаблон Vite + React. Напишите промпт слева — AI добавит
        страницы и интерактив. HMR подхватит изменения мгновенно, без рилоада.
      </p>
      <p className="text-sm text-zinc-500">
        Шаблон: <code className="font-mono">vite-react-spa</code>
      </p>
    </main>
  );
}
