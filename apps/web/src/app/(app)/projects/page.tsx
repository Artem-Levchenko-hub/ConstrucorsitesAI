import { getSession } from "@/lib/auth-mock";
import { TopBar } from "@/components/workspace/TopBar";
import { ProjectsList } from "@/components/projects/ProjectsList";
import { NewProjectDialog } from "@/components/projects/NewProjectDialog";

export default async function ProjectsPage() {
  const session = await getSession();
  if (!session) return null;

  return (
    <>
      <TopBar user={session} showProjectControls={false} />
      <div className="mx-auto w-full max-w-[1240px] px-8 py-10 space-y-8">
        <div className="flex items-center justify-between gap-4">
          <div className="space-y-1">
            <h1 className="text-3xl font-semibold tracking-tight">Проекты</h1>
            <p className="text-sm text-fg-secondary">
              Каждый проект — отдельный сайт с git-историей и preview.
            </p>
          </div>
          <NewProjectDialog />
        </div>

        <ProjectsList />
      </div>
    </>
  );
}
