/**
 * Default page for a freshly provisioned project — a working Task list wired to
 * the entity engine via the SDK. It proves the backend end-to-end (auth →
 * entities.Task CRUD) the moment the container boots, before any AI write.
 *
 * The AI replaces this file on the user's first prompt. It's a client component
 * because the SDK talks to the same-origin API with the session cookie.
 */
"use client";

import { useCallback, useEffect, useState } from "react";

import { auth, entities, type Me, type Row } from "@/lib/sdk";

export default function Home() {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [tasks, setTasks] = useState<Row[]>([]);
  const [title, setTitle] = useState("");

  const refresh = useCallback(async () => {
    try {
      setTasks(await entities.Task.list({ sort: "created_at", order: "desc" }));
    } catch {
      setTasks([]);
    }
  }, []);

  useEffect(() => {
    (async () => {
      const u = await auth.me().catch(() => null);
      setMe(u);
      if (u) await refresh();
      setReady(true);
    })();
  }, [refresh]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const t = title.trim();
    if (!t) return;
    setTitle("");
    await entities.Task.create({ title: t });
    await refresh();
  }

  async function toggle(task: Row) {
    await entities.Task.update(task.id, { done: !task.done });
    await refresh();
  }

  async function remove(task: Row) {
    await entities.Task.delete(task.id);
    await refresh();
  }

  if (!ready) return null;

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col gap-6 px-6 py-16">
      <header className="space-y-1">
        <p className="text-xs uppercase tracking-widest text-zinc-500">
          Omnia · entity engine
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Мои задачи</h1>
        <p className="text-sm text-zinc-400">
          Бэкенд из коробки: сущность <code className="font-mono">Task</code> →
          авто-CRUD, авторизация и владение — без бэкенд-кода.
        </p>
      </header>

      {!me ? (
        <a
          href="/signin"
          className="self-start rounded-lg bg-white px-4 py-2 text-sm font-medium text-zinc-900 transition hover:bg-zinc-200"
        >
          Войти, чтобы увидеть свои задачи →
        </a>
      ) : (
        <>
          <form onSubmit={add} className="flex gap-2">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Новая задача…"
              className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-zinc-500"
            />
            <button
              type="submit"
              className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-zinc-900 transition hover:bg-zinc-200"
            >
              Добавить
            </button>
          </form>

          <ul className="space-y-2">
            {tasks.map((t) => (
              <li
                key={t.id}
                className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2"
              >
                <input
                  type="checkbox"
                  checked={Boolean(t.done)}
                  onChange={() => toggle(t)}
                  className="h-4 w-4 accent-emerald-500"
                />
                <span
                  className={
                    t.done ? "flex-1 text-zinc-500 line-through" : "flex-1"
                  }
                >
                  {String(t.title)}
                </span>
                <button
                  onClick={() => remove(t)}
                  className="text-xs text-zinc-500 transition hover:text-red-400"
                >
                  удалить
                </button>
              </li>
            ))}
            {tasks.length === 0 && (
              <li className="rounded-lg border border-dashed border-zinc-800 px-3 py-6 text-center text-sm text-zinc-500">
                Пока пусто. Добавьте первую задачу.
              </li>
            )}
          </ul>
        </>
      )}
    </main>
  );
}
