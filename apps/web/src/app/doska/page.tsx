import type { Metadata } from "next";

import { TaskBoard } from "@/components/task-board/TaskBoard";

export const metadata: Metadata = {
  title: "Доска задач — Omnia",
  description: "Общая доска задач команды Omnia",
  robots: { index: false, follow: false },
};

export default function TaskBoardPage() {
  return <TaskBoard />;
}
