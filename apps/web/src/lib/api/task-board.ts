import { apiFetch, apiUrl, postBlob } from "./client";

export type TaskBoardStatus = "backlog" | "in_progress" | "review" | "done";
export type TaskBoardAssignee = "alexey" | "alexey_jr" | "artem" | "roman";
export type TaskBoardPriority = "low" | "medium" | "high";

export interface TaskBoardAttachment {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  created_at: string;
}

export interface TaskBoardTask {
  id: string;
  title: string;
  description: string;
  status: TaskBoardStatus;
  assignee: TaskBoardAssignee;
  priority: TaskBoardPriority;
  position: number;
  attachments: TaskBoardAttachment[];
  created_at: string;
  updated_at: string;
}

export interface CreateTaskPayload {
  title: string;
  description?: string;
  status?: TaskBoardStatus;
  assignee: TaskBoardAssignee;
  priority?: TaskBoardPriority;
}

export type UpdateTaskPayload = Partial<CreateTaskPayload>;

export interface TaskBoardApi {
  listTasks(): Promise<TaskBoardTask[]>;
  createTask(payload: CreateTaskPayload): Promise<TaskBoardTask>;
  updateTask(id: string, payload: UpdateTaskPayload): Promise<TaskBoardTask>;
  deleteTask(id: string): Promise<void>;
  uploadAttachment(id: string, file: File): Promise<TaskBoardAttachment>;
  deleteAttachment(taskId: string, attachmentId: string): Promise<void>;
  attachmentDownloadUrl(taskId: string, attachmentId: string): string;
}

export const taskBoardApi: TaskBoardApi = {
  listTasks: () => apiFetch<TaskBoardTask[]>("/api/task-board/tasks"),
  createTask: (payload) =>
    apiFetch<TaskBoardTask>("/api/task-board/tasks", {
      method: "POST",
      json: payload,
    }),
  updateTask: (id, payload) =>
    apiFetch<TaskBoardTask>(`/api/task-board/tasks/${id}`, {
      method: "PATCH",
      json: payload,
    }),
  deleteTask: (id) =>
    apiFetch<void>(`/api/task-board/tasks/${id}`, { method: "DELETE" }),
  uploadAttachment: (id, file) =>
    postBlob<TaskBoardAttachment>(
      `/api/task-board/tasks/${id}/attachments`,
      file,
      {
        timeoutMs: 60_000,
        headers: { "X-File-Name": encodeURIComponent(file.name) },
      },
    ),
  deleteAttachment: (taskId, attachmentId) =>
    apiFetch<void>(
      `/api/task-board/tasks/${taskId}/attachments/${attachmentId}`,
      { method: "DELETE" },
    ),
  attachmentDownloadUrl: (taskId, attachmentId) =>
    apiUrl(`/api/task-board/tasks/${taskId}/attachments/${attachmentId}`),
};
