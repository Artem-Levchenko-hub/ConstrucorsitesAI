/**
 * Default page for a freshly provisioned project — a working Task list wired to
 * the entity engine via the SDK, built with the Omnia app kit. It proves the
 * backend end-to-end (auth → entities.Task CRUD) and that the component kit
 * renders, the moment the container boots, before any AI write.
 *
 * The AI replaces this file on the user's first prompt. It's a client component
 * because the SDK talks to the same-origin API with the session cookie.
 */
"use client";

import { useCallback, useEffect, useState } from "react";
import { ListTodo, Plus, Trash2 } from "lucide-react";

import { auth, entities, type Me, type Row } from "@/lib/sdk";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/omnia";

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

  const open = tasks.filter((t) => !t.done).length;

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col gap-6 px-6 py-16">
      <header className="space-y-2">
        <Badge variant="secondary" className="gap-1.5">
          <ListTodo className="size-3.5" />
          Omnia · entity engine
        </Badge>
        <h1 className="text-3xl font-semibold tracking-tight">Мои задачи</h1>
        <p className="text-sm text-muted-foreground">
          Бэкенд из коробки: сущность <code className="rounded bg-muted px-1 font-mono text-xs">Task</code>{" "}
          → авто-CRUD, авторизация и владение — без бэкенд-кода.
        </p>
      </header>

      {!ready ? (
        <div className="space-y-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : !me ? (
        <Card className="items-start gap-4 p-6">
          <p className="text-sm text-muted-foreground">
            Войдите, чтобы увидеть и вести свои задачи.
          </p>
          <Button asChild>
            <a href="/signin">Войти →</a>
          </Button>
        </Card>
      ) : (
        <>
          <form onSubmit={add} className="flex gap-2">
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Новая задача…"
            />
            <Button type="submit">
              <Plus />
              Добавить
            </Button>
          </form>

          {tasks.length === 0 ? (
            <EmptyState
              icon={<ListTodo />}
              title="Пока пусто"
              description="Добавьте первую задачу — она сразу сохранится в базе."
            />
          ) : (
            <Card className="gap-0 divide-y divide-border p-0">
              <div className="flex items-center justify-between px-4 py-3">
                <span className="text-sm font-medium">Список</span>
                <Badge variant="outline" className="tabular-nums">
                  {open} активных
                </Badge>
              </div>
              <ul className="divide-y divide-border">
                {tasks.map((t) => (
                  <li key={t.id} className="flex items-center gap-3 px-4 py-3">
                    <Checkbox
                      checked={Boolean(t.done)}
                      onCheckedChange={() => toggle(t)}
                    />
                    <span
                      className={
                        t.done
                          ? "flex-1 text-muted-foreground line-through"
                          : "flex-1"
                      }
                    >
                      {String(t.title)}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => remove(t)}
                      aria-label="Удалить"
                      className="text-muted-foreground hover:text-destructive"
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}
    </main>
  );
}
