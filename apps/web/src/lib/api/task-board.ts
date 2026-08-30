import { apiFetch } from "./client";

export type TaskBoardStatus = "backlog" | "in_progress" | "review" | "done";
export type TaskBoardAssignee = "alexey" | "alexey_jr" | "artem" | "roman";
export type TaskBoardPriority = "low" | "medium" | "high";

export interface TaskBoardTask {
  id: string;
  title: string;
  description: string;
  status: TaskBoardStatus;
  assignee: TaskBoardAssignee;
  priority: TaskBoardPriority;
  position: number;
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
};
