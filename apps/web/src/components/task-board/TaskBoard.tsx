"use client";

import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  Circle,
  Clock3,
  Download,
  Edit3,
  FileText,
  GripVertical,
  Loader2,
  Paperclip,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import {
  taskBoardApi,
  type TaskBoardApi,
  type TaskBoardAssignee,
  type TaskBoardAttachment,
  type TaskBoardPriority,
  type TaskBoardStatus,
  type TaskBoardTask,
} from "@/lib/api/task-board";

const MEMBERS: Array<{
  id: TaskBoardAssignee;
  name: string;
  initials: string;
  avatarClass: string;
}> = [
  {
    id: "alexey",
    name: "Алексей",
    initials: "А",
    avatarClass: "bg-[#f4e36a] text-[#251f00]",
  },
  {
    id: "alexey_jr",
    name: "Алексей jr.",
    initials: "Аj",
    avatarClass: "bg-[#b9e7ff] text-[#00253a]",
  },
  {
    id: "artem",
    name: "Артем",
    initials: "А",
    avatarClass: "bg-[#c7f4d1] text-[#052d11]",
  },
  {
    id: "roman",
    name: "Роман",
    initials: "Р",
    avatarClass: "bg-[#d8c6ff] text-[#25124e]",
  },
];

const STATUSES: Array<{
  id: TaskBoardStatus;
  label: string;
  hint: string;
  dotClass: string;
  icon: typeof Circle;
}> = [
  {
    id: "backlog",
    label: "Новые",
    hint: "Что нужно сделать",
    dotClass: "bg-[#8b8e99]",
    icon: Circle,
  },
  {
    id: "in_progress",
    label: "В работе",
    hint: "Задачи в процессе",
    dotClass: "bg-[#4f81f7]",
    icon: Clock3,
  },
  {
    id: "review",
    label: "На проверке",
    hint: "Ждут обратной связи",
    dotClass: "bg-[#e8c547]",
    icon: AlertCircle,
  },
  {
    id: "done",
    label: "Готово",
    hint: "Завершённые задачи",
    dotClass: "bg-[#47b36b]",
    icon: CheckCircle2,
  },
];

const PRIORITIES: Array<{
  id: TaskBoardPriority;
  label: string;
  className: string;
}> = [
  { id: "low", label: "Низкий", className: "bg-[#283036] text-[#aeb8bf]" },
  { id: "medium", label: "Обычный", className: "bg-[#242f47] text-[#9bb9ff]" },
  { id: "high", label: "Важный", className: "bg-[#42252d] text-[#ff9eb8]" },
];

const MEMBER_STORAGE_KEY = "omnia-task-board-member";
const MEMBER_CHANGE_EVENT = "omnia-task-board-member-change";

type EditorState = { mode: "create" } | { mode: "edit"; taskId: string } | null;

interface TaskDraft {
  title: string;
  description: string;
  status: TaskBoardStatus;
  assignee: TaskBoardAssignee;
  priority: TaskBoardPriority;
}

interface TaskBoardProps {
  api?: TaskBoardApi;
  refreshIntervalMs?: number;
}

function memberById(id: TaskBoardAssignee) {
  return MEMBERS.find((member) => member.id === id) ?? MEMBERS[3];
}

function priorityById(id: TaskBoardPriority) {
  return PRIORITIES.find((priority) => priority.id === id) ?? PRIORITIES[1];
}

function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Не удалось связаться с сервером. Повторите действие.";
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) {
    return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(bytes / 1024)} КБ`;
  }
  return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(bytes / (1024 * 1024))} МБ`;
}

function getStoredMember(): TaskBoardAssignee {
  const stored = window.localStorage.getItem(MEMBER_STORAGE_KEY);
  return MEMBERS.some((member) => member.id === stored)
    ? (stored as TaskBoardAssignee)
    : "roman";
}

function subscribeToMemberChange(onStoreChange: () => void) {
  window.addEventListener("storage", onStoreChange);
  window.addEventListener(MEMBER_CHANGE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStoreChange);
    window.removeEventListener(MEMBER_CHANGE_EVENT, onStoreChange);
  };
}

export function TaskBoard({
  api = taskBoardApi,
  refreshIntervalMs = 5000,
}: TaskBoardProps) {
  const [tasks, setTasks] = useState<TaskBoardTask[]>([]);
  const activeMember = useSyncExternalStore<TaskBoardAssignee>(
    subscribeToMemberChange,
    getStoredMember,
    () => "roman" as TaskBoardAssignee,
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState>(null);
  const [draft, setDraft] = useState<TaskDraft>({
    title: "",
    description: "",
    status: "backlog",
    assignee: "roman",
    priority: "medium",
  });
  const [draggedTaskId, setDraggedTaskId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<TaskBoardStatus | null>(null);
  const [pendingTaskIds, setPendingTaskIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const requestSequence = useRef(0);
  const mutationSequence = useRef(0);
  const pendingTaskIdsRef = useRef(new Set<string>());

  const beginTaskMutation = useCallback((taskId: string) => {
    if (pendingTaskIdsRef.current.has(taskId)) return false;
    const next = new Set(pendingTaskIdsRef.current);
    next.add(taskId);
    pendingTaskIdsRef.current = next;
    setPendingTaskIds(next);
    mutationSequence.current += 1;
    return true;
  }, []);

  const endTaskMutation = useCallback((taskId: string) => {
    const next = new Set(pendingTaskIdsRef.current);
    next.delete(taskId);
    pendingTaskIdsRef.current = next;
    setPendingTaskIds(next);
    mutationSequence.current += 1;
  }, []);

  const loadTasks = useCallback(
    async () => {
      const requestId = ++requestSequence.current;
      const mutationId = mutationSequence.current;
      try {
        const result = await api.listTasks();
        if (
          requestId !== requestSequence.current ||
          mutationId !== mutationSequence.current
        ) {
          return;
        }
        setTasks(result);
        setRequestError(null);
      } catch (error) {
        if (requestId !== requestSequence.current) return;
        setRequestError(errorMessage(error));
      } finally {
        if (requestId === requestSequence.current) setLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void loadTasks(), 0);
    const interval =
      refreshIntervalMs > 0
        ? window.setInterval(() => void loadTasks(), refreshIntervalMs)
        : null;
    return () => {
      window.clearTimeout(initialLoad);
      if (interval !== null) window.clearInterval(interval);
    };
  }, [loadTasks, refreshIntervalMs]);

  const grouped = useMemo(() => {
    const result = Object.fromEntries(
      STATUSES.map((status) => [status.id, [] as TaskBoardTask[]]),
    ) as Record<TaskBoardStatus, TaskBoardTask[]>;
    for (const task of tasks) result[task.status].push(task);
    for (const status of STATUSES) {
      result[status.id].sort(
        (left, right) =>
          left.position - right.position ||
          left.created_at.localeCompare(right.created_at),
      );
    }
    return result;
  }, [tasks]);

  const selectMember = (member: TaskBoardAssignee) => {
    window.localStorage.setItem(MEMBER_STORAGE_KEY, member);
    window.dispatchEvent(new Event(MEMBER_CHANGE_EVENT));
  };

  const openCreate = () => {
    setDraft({
      title: "",
      description: "",
      status: "backlog",
      assignee: activeMember,
      priority: "medium",
    });
    setEditor({ mode: "create" });
  };

  const openEdit = (task: TaskBoardTask) => {
    if (pendingTaskIdsRef.current.has(task.id)) return;
    setDraft({
      title: task.title,
      description: task.description,
      status: task.status,
      assignee: task.assignee,
      priority: task.priority,
    });
    setEditor({ mode: "edit", taskId: task.id });
  };

  const saveTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const title = draft.title.trim();
    if (!title || saving) return;
    const editingTaskId = editor?.mode === "edit" ? editor.taskId : null;
    if (editingTaskId !== null && !beginTaskMutation(editingTaskId)) return;
    setSaving(true);
    setRequestError(null);
    if (editingTaskId === null) mutationSequence.current += 1;
    try {
      if (editingTaskId !== null) {
        const updated = await api.updateTask(editingTaskId, {
          ...draft,
          title,
          description: draft.description.trim(),
        });
        setTasks((current) =>
          current.map((task) => (task.id === updated.id ? updated : task)),
        );
      } else {
        const created = await api.createTask({
          ...draft,
          title,
          description: draft.description.trim(),
        });
        setTasks((current) => [...current, created]);
      }
      setEditor(null);
    } catch (error) {
      setRequestError(errorMessage(error));
    } finally {
      if (editingTaskId !== null) {
        endTaskMutation(editingTaskId);
      } else {
        mutationSequence.current += 1;
      }
      setSaving(false);
    }
  };

  const moveTask = async (taskId: string, status: TaskBoardStatus) => {
    const original = tasks.find((task) => task.id === taskId);
    if (!original || original.status === status) return;
    if (!beginTaskMutation(taskId)) return;
    setTasks((current) =>
      current.map((task) =>
        task.id === taskId
          ? { ...task, status, position: grouped[status].length }
          : task,
      ),
    );
    setRequestError(null);
    try {
      const updated = await api.updateTask(taskId, { status });
      setTasks((current) =>
        current.map((task) => (task.id === updated.id ? updated : task)),
      );
    } catch (error) {
      setTasks((current) =>
        current.map((task) => (task.id === original.id ? original : task)),
      );
      setRequestError(errorMessage(error));
    } finally {
      endTaskMutation(taskId);
    }
  };

  const deleteTask = async (task: TaskBoardTask) => {
    if (pendingTaskIdsRef.current.has(task.id)) return;
    if (!window.confirm(`Удалить задачу «${task.title}»?`)) return;
    if (!beginTaskMutation(task.id)) return;
    const snapshot = tasks;
    setTasks((current) => current.filter((item) => item.id !== task.id));
    setRequestError(null);
    try {
      await api.deleteTask(task.id);
      if (editor?.mode === "edit" && editor.taskId === task.id) setEditor(null);
    } catch (error) {
      setTasks(snapshot);
      setRequestError(errorMessage(error));
    } finally {
      endTaskMutation(task.id);
    }
  };

  const uploadAttachments = async (task: TaskBoardTask, files: File[]) => {
    if (files.length === 0 || !beginTaskMutation(task.id)) return;
    setRequestError(null);
    try {
      for (const file of files.slice(0, Math.max(0, 10 - task.attachments.length))) {
        const attachment = await api.uploadAttachment(task.id, file);
        setTasks((current) =>
          current.map((item) =>
            item.id === task.id
              ? { ...item, attachments: [...item.attachments, attachment] }
              : item,
          ),
        );
      }
    } catch (error) {
      setRequestError(errorMessage(error));
    } finally {
      endTaskMutation(task.id);
    }
  };

  const deleteAttachment = async (
    task: TaskBoardTask,
    attachment: TaskBoardAttachment,
  ) => {
    if (pendingTaskIdsRef.current.has(task.id)) return;
    if (!window.confirm(`Удалить файл «${attachment.filename}»?`)) return;
    if (!beginTaskMutation(task.id)) return;
    setRequestError(null);
    try {
      await api.deleteAttachment(task.id, attachment.id);
      setTasks((current) =>
        current.map((item) =>
          item.id === task.id
            ? {
                ...item,
                attachments: item.attachments.filter(
                  (currentAttachment) => currentAttachment.id !== attachment.id,
                ),
              }
            : item,
        ),
      );
    } catch (error) {
      setRequestError(errorMessage(error));
    } finally {
      endTaskMutation(task.id);
    }
  };

  const onDrop = async (
    event: React.DragEvent<HTMLElement>,
    status: TaskBoardStatus,
  ) => {
    event.preventDefault();
    const taskId =
      event.dataTransfer.getData("text/task-id") || draggedTaskId || "";
    setDraggedTaskId(null);
    setDropTarget(null);
    if (taskId) await moveTask(taskId, status);
  };

  const completedCount = grouped.done.length;
  const activeCount = grouped.in_progress.length;
  const reviewCount = grouped.review.length;

  return (
    <main className="min-h-dvh bg-[#111317] text-white" data-task-board>
      <div className="mx-auto flex min-h-dvh w-full max-w-[1780px] flex-col px-4 pb-8 pt-4 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-5 border-b border-[#2a2d34] pb-5 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex items-center gap-4">
            <div className="grid size-11 shrink-0 place-items-center rounded-xl bg-[#4f81f7] text-[#111317] shadow-[0_0_32px_rgba(79,129,247,0.25)]">
              <Check className="size-5 stroke-[2.6]" />
            </div>
            <div>
              <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-[#727784]">
                Omnia team
              </p>
              <h1 className="mt-1 text-xl font-semibold tracking-[-0.025em] sm:text-2xl">
                Доска задач
              </h1>
            </div>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="flex flex-wrap items-center gap-1 rounded-xl border border-[#2a2d34] bg-[#191c21] p-1.5" aria-label="Активный участник">
              {MEMBERS.map((member) => {
                const active = activeMember === member.id;
                return (
                  <button
                    key={member.id}
                    type="button"
                    data-member-id={member.id}
                    aria-pressed={active}
                    onClick={() => selectMember(member.id)}
                    className={`flex min-h-10 items-center gap-2 rounded-lg px-2.5 text-xs font-medium transition sm:text-sm ${
                      active
                        ? "bg-[#2b2f37] text-white shadow-sm"
                        : "text-[#8d929e] hover:bg-[#22252b] hover:text-white"
                    }`}
                  >
                    <span className={`grid size-7 place-items-center rounded-full text-[10px] font-bold ${member.avatarClass}`}>
                      {member.initials}
                    </span>
                    <span className="hidden md:inline">{member.name}</span>
                  </button>
                );
              })}
            </div>
            <button
              type="button"
              data-action="new-task"
              onClick={openCreate}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[#4f81f7] px-4 text-sm font-semibold text-[#0c152b] transition hover:bg-[#6a95fa]"
            >
              <Plus className="size-4" />
              Новая задача
            </button>
          </div>
        </header>

        <section className="grid gap-px overflow-hidden rounded-xl border border-[#2a2d34] bg-[#2a2d34] sm:grid-cols-2 xl:grid-cols-4" aria-label="Сводка доски">
          <SummaryItem label="Всего задач" value={tasks.length} detail="на общей доске" />
          <SummaryItem label="В работе" value={activeCount} detail="сейчас выполняются" />
          <SummaryItem label="На проверке" value={reviewCount} detail="нужна обратная связь" />
          <SummaryItem label="Готово" value={completedCount} detail="задач завершено" />
        </section>

        {requestError ? (
          <div role="alert" className="mt-4 flex items-start justify-between gap-4 rounded-xl border border-[#61313d] bg-[#2e1d22] px-4 py-3 text-sm text-[#ff9eb8]">
            <span className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
              {requestError}
            </span>
            <button type="button" onClick={() => {
              setLoading(true);
              void loadTasks();
            }} className="shrink-0 font-semibold text-white hover:underline">
              Повторить
            </button>
          </div>
        ) : null}

        <section className="mt-5 grid flex-1 gap-4 xl:grid-cols-4" aria-label="Колонки задач">
          {STATUSES.map((status) => {
            const cards = grouped[status.id];
            const StatusIcon = status.icon;
            const highlighted = dropTarget === status.id;
            return (
              <section
                key={status.id}
                data-board-column={status.id}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                  setDropTarget(status.id);
                }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node)) {
                    setDropTarget(null);
                  }
                }}
                onDrop={(event) => void onDrop(event, status.id)}
                className={`flex min-h-[260px] min-w-0 flex-col rounded-2xl border p-3 transition-colors sm:p-4 xl:min-h-[540px] ${
                  highlighted
                    ? "border-[#4f81f7] bg-[#17213a]"
                    : "border-[#272a31] bg-[#17191e]"
                }`}
              >
                <div className="flex items-start justify-between gap-3 px-1 pb-4">
                  <div>
                    <div className="flex items-center gap-2.5">
                      <span className={`size-2 rounded-full ${status.dotClass}`} />
                      <h2 className="text-sm font-semibold">{status.label}</h2>
                      <span className="rounded-md bg-[#25282f] px-2 py-0.5 font-mono text-[10px] text-[#9a9fab]">
                        {cards.length}
                      </span>
                    </div>
                    <p className="mt-1.5 pl-[18px] text-xs text-[#737884]">{status.hint}</p>
                  </div>
                  <StatusIcon className="mt-0.5 size-4 text-[#5f6470]" />
                </div>

                <div className="flex flex-1 flex-col gap-3">
                  {loading ? (
                    <LoadingCards />
                  ) : cards.length === 0 ? (
                    <div className={`grid min-h-28 flex-1 place-items-center rounded-xl border border-dashed px-5 text-center ${highlighted ? "border-[#4f81f7]/60 bg-[#4f81f7]/5" : "border-[#30333a]"}`}>
                      <div>
                        <p className="text-sm font-medium text-[#7e838f]">
                          {highlighted ? "Отпустите карточку" : "Здесь пока пусто"}
                        </p>
                        <p className="mt-1 text-xs leading-5 text-[#555a65]">
                          {status.id === "backlog"
                            ? "Добавьте первую задачу"
                            : "Перетащите задачу в эту колонку"}
                        </p>
                      </div>
                    </div>
                  ) : (
                    cards.map((task) => (
                      <TaskCard
                        key={task.id}
                        task={task}
                        dragging={draggedTaskId === task.id}
                        pending={pendingTaskIds.has(task.id)}
                        onEdit={() => openEdit(task)}
                        onDelete={() => void deleteTask(task)}
                        onUpload={(files) => void uploadAttachments(task, files)}
                        onDeleteAttachment={(attachment) =>
                          void deleteAttachment(task, attachment)
                        }
                        attachmentDownloadUrl={(attachmentId) =>
                          api.attachmentDownloadUrl(task.id, attachmentId)
                        }
                        onDragStart={(event) => {
                          if (pendingTaskIdsRef.current.has(task.id)) {
                            event.preventDefault();
                            return;
                          }
                          event.dataTransfer.effectAllowed = "move";
                          event.dataTransfer.setData("text/task-id", task.id);
                          setDraggedTaskId(task.id);
                        }}
                        onDragEnd={() => {
                          setDraggedTaskId(null);
                          setDropTarget(null);
                        }}
                      />
                    ))
                  )}
                </div>
              </section>
            );
          })}
        </section>
      </div>

      {editor ? (
        <div className="fixed inset-0 z-50 grid place-items-end bg-black/70 p-0 backdrop-blur-sm sm:place-items-center sm:p-6" role="presentation" onMouseDown={(event) => {
          if (event.currentTarget === event.target && !saving) setEditor(null);
        }}>
          <div role="dialog" aria-modal="true" aria-labelledby="task-dialog-title" className="max-h-[94dvh] w-full overflow-y-auto rounded-t-2xl border border-[#32363f] bg-[#191c21] shadow-2xl sm:max-w-xl sm:rounded-2xl">
            <div className="flex items-center justify-between border-b border-[#2a2d34] px-5 py-4 sm:px-6">
              <div>
                <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#6f7480]">
                  {editor.mode === "create" ? "Новая карточка" : "Редактирование"}
                </p>
                <h2 id="task-dialog-title" className="mt-1 text-lg font-semibold">
                  {editor.mode === "create" ? "Добавить задачу" : "Изменить задачу"}
                </h2>
              </div>
              <button type="button" aria-label="Закрыть" disabled={saving} onClick={() => setEditor(null)} className="grid size-10 place-items-center rounded-lg text-[#7f8490] transition hover:bg-[#25282f] hover:text-white disabled:opacity-50">
                <X className="size-5" />
              </button>
            </div>

            <form data-task-form onSubmit={saveTask} className="space-y-5 px-5 py-5 sm:px-6">
              <label className="block">
                <span className="text-sm font-medium">Название</span>
                <input
                  name="title"
                  autoFocus
                  required
                  maxLength={160}
                  value={draft.title}
                  onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
                  placeholder="Например, проверить новую форму"
                  className="mt-2 min-h-12 w-full rounded-xl border border-[#343842] bg-[#22252b] px-4 text-sm text-white outline-none transition placeholder:text-[#666b76] focus:border-[#4f81f7]"
                />
              </label>

              <label className="block">
                <span className="text-sm font-medium">Описание</span>
                <textarea
                  name="description"
                  maxLength={2000}
                  rows={4}
                  value={draft.description}
                  onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                  placeholder="Добавьте детали, чтобы задачу можно было выполнить без уточнений"
                  className="mt-2 w-full resize-y rounded-xl border border-[#343842] bg-[#22252b] px-4 py-3 text-sm leading-6 text-white outline-none transition placeholder:text-[#666b76] focus:border-[#4f81f7]"
                />
              </label>

              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block">
                  <span className="text-sm font-medium">Исполнитель</span>
                  <select name="assignee" value={draft.assignee} onChange={(event) => setDraft((current) => ({ ...current, assignee: event.target.value as TaskBoardAssignee }))} className="mt-2 min-h-12 w-full rounded-xl border border-[#343842] bg-[#22252b] px-3 text-sm text-white outline-none focus:border-[#4f81f7]">
                    {MEMBERS.map((member) => (
                      <option key={member.id} value={member.id}>{member.name}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-sm font-medium">Приоритет</span>
                  <select name="priority" value={draft.priority} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value as TaskBoardPriority }))} className="mt-2 min-h-12 w-full rounded-xl border border-[#343842] bg-[#22252b] px-3 text-sm text-white outline-none focus:border-[#4f81f7]">
                    {PRIORITIES.map((priority) => (
                      <option key={priority.id} value={priority.id}>{priority.label}</option>
                    ))}
                  </select>
                </label>
              </div>

              <label className="block">
                <span className="text-sm font-medium">Статус</span>
                <select name="status" value={draft.status} onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value as TaskBoardStatus }))} className="mt-2 min-h-12 w-full rounded-xl border border-[#343842] bg-[#22252b] px-3 text-sm text-white outline-none focus:border-[#4f81f7]">
                  {STATUSES.map((status) => (
                    <option key={status.id} value={status.id}>{status.label}</option>
                  ))}
                </select>
              </label>

              <div className="flex flex-col-reverse gap-3 border-t border-[#2a2d34] pt-5 sm:flex-row sm:justify-end">
                <button type="button" disabled={saving} onClick={() => setEditor(null)} className="min-h-11 rounded-xl border border-[#343842] px-4 text-sm font-semibold text-[#b3b7c0] transition hover:bg-[#25282f] hover:text-white disabled:opacity-50">
                  Отмена
                </button>
                <button type="submit" disabled={saving || !draft.title.trim()} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[#4f81f7] px-5 text-sm font-semibold text-[#0c152b] transition hover:bg-[#6a95fa] disabled:cursor-not-allowed disabled:opacity-50">
                  {saving ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
                  {editor.mode === "create" ? "Создать задачу" : "Сохранить"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </main>
  );
}

function SummaryItem({ label, value, detail }: { label: string; value: number; detail: string }) {
  return (
    <div className="bg-[#191c21] px-4 py-3.5 sm:px-5">
      <div className="flex items-baseline gap-2">
        <strong className="font-mono text-xl font-semibold tabular-nums">{value}</strong>
        <span className="text-sm font-medium text-[#c7cad0]">{label}</span>
      </div>
      <p className="mt-1 text-xs text-[#666b76]">{detail}</p>
    </div>
  );
}

function LoadingCards() {
  return (
    <div className="space-y-3" aria-label="Загрузка задач">
      {[0, 1].map((item) => (
        <div key={item} className="h-32 animate-pulse rounded-xl border border-[#2e3239] bg-[#202329]" />
      ))}
    </div>
  );
}

function TaskCard({
  task,
  dragging,
  pending,
  onEdit,
  onDelete,
  onUpload,
  onDeleteAttachment,
  attachmentDownloadUrl,
  onDragStart,
  onDragEnd,
}: {
  task: TaskBoardTask;
  dragging: boolean;
  pending: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onUpload: (files: File[]) => void;
  onDeleteAttachment: (attachment: TaskBoardAttachment) => void;
  attachmentDownloadUrl: (attachmentId: string) => string;
  onDragStart: (event: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
}) {
  const member = memberById(task.assignee);
  const priority = priorityById(task.priority);
  return (
    <article
      draggable={!pending}
      aria-busy={pending}
      data-task-id={task.id}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={`group rounded-xl border border-[#30343c] bg-[#202329] p-4 shadow-[0_12px_28px_rgba(0,0,0,0.14)] transition hover:border-[#424752] ${pending ? "cursor-wait opacity-65" : "cursor-grab hover:-translate-y-0.5 hover:bg-[#23262d] active:cursor-grabbing"} ${dragging ? "opacity-40" : ""}`}
    >
      <div className="flex items-start justify-between gap-3">
        <span className={`rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${priority.className}`}>
          {priority.label}
        </span>
        <div className="flex items-center gap-0.5 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
          <label
            title="Добавить файл"
            className={`grid size-8 place-items-center rounded-lg text-[#7d828e] hover:bg-[#30343c] hover:text-white ${pending || task.attachments.length >= 10 ? "pointer-events-none opacity-40" : "cursor-pointer"}`}
          >
            <Paperclip className="size-3.5" />
            <input
              type="file"
              multiple
              disabled={pending || task.attachments.length >= 10}
              data-attachment-input={task.id}
              aria-label={`Добавить файл: ${task.title}`}
              className="sr-only"
              onChange={(event) => {
                const files = Array.from(event.currentTarget.files ?? []);
                event.currentTarget.value = "";
                onUpload(files);
              }}
            />
          </label>
          <button type="button" disabled={pending} onClick={onEdit} aria-label={`Редактировать: ${task.title}`} className="grid size-8 place-items-center rounded-lg text-[#7d828e] hover:bg-[#30343c] hover:text-white disabled:pointer-events-none disabled:opacity-40">
            <Edit3 className="size-3.5" />
          </button>
          <button type="button" disabled={pending} onClick={onDelete} aria-label={`Удалить: ${task.title}`} className="grid size-8 place-items-center rounded-lg text-[#7d828e] hover:bg-[#3b242d] hover:text-[#ff8faa] disabled:pointer-events-none disabled:opacity-40">
            <Trash2 className="size-3.5" />
          </button>
          <GripVertical className="size-4 text-[#555a65]" aria-hidden="true" />
        </div>
      </div>
      <h3 className="mt-3 text-[15px] font-semibold leading-5 tracking-[-0.01em]">{task.title}</h3>
      {task.description ? (
        <p className="mt-2 line-clamp-3 text-xs leading-5 text-[#8e939e]">{task.description}</p>
      ) : null}
      {task.attachments.length > 0 ? (
        <div className="mt-3 space-y-1.5" aria-label="Вложения">
          {task.attachments.map((attachment) => (
            <div
              key={attachment.id}
              className="flex min-w-0 items-center gap-1 rounded-lg border border-[#303640] bg-[#191c21] p-1"
            >
              <a
                data-attachment-id={attachment.id}
                href={attachmentDownloadUrl(attachment.id)}
                download={attachment.filename}
                draggable={false}
                onClick={(event) => event.stopPropagation()}
                className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-[#aeb7c7] transition hover:bg-[#252a32] hover:text-white"
              >
                <FileText className="size-3.5 shrink-0 text-[#7599ef]" />
                <span className="min-w-0 flex-1 truncate text-[11px] font-medium">
                  {attachment.filename}
                </span>
                <span className="shrink-0 font-mono text-[9px] text-[#666d79]">
                  {formatFileSize(attachment.size)}
                </span>
                <Download className="size-3 shrink-0 text-[#666d79]" />
              </a>
              <button
                type="button"
                disabled={pending}
                onClick={(event) => {
                  event.stopPropagation();
                  onDeleteAttachment(attachment);
                }}
                aria-label={`Удалить вложение: ${attachment.filename}`}
                className="grid size-7 shrink-0 place-items-center rounded-md text-[#656b76] hover:bg-[#3b242d] hover:text-[#ff8faa] disabled:pointer-events-none disabled:opacity-40"
              >
                <X className="size-3" />
              </button>
            </div>
          ))}
        </div>
      ) : null}
      <div className="mt-4 flex items-center justify-between gap-3 border-t border-[#2c3037] pt-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className={`grid size-7 shrink-0 place-items-center rounded-full text-[10px] font-bold ${member.avatarClass}`}>
            {member.initials}
          </span>
          <span className="truncate text-xs font-medium text-[#b6bac3]">{member.name}</span>
        </div>
        <ArrowRight className="size-3.5 shrink-0 text-[#505561]" aria-hidden="true" />
      </div>
    </article>
  );
}
