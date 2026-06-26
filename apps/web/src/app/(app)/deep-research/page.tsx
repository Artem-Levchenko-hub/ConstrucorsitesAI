import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth-mock";
import { TopBar } from "@/components/workspace/TopBar";
import { DeepResearchEntry } from "@/components/deep-research/DeepResearchEntry";

export const metadata = {
  title: "Облачный Claude Code — Omnia.AI",
  description:
    "Опиши задачу — агент с полным доступом к среде сделает всё сам: файлы, команды, логи, любой стек.",
};

/**
 * `/deep-research` — the cloud Claude Code entry. A signed-in user lands here,
 * describes any task, and is dropped into the agentic workspace where the agent
 * acts with full tools and a live transcript. Auth-gated like the rest of (app);
 * an anonymous visitor is sent to login (and bounced back after).
 */
export default async function DeepResearchPage() {
  const session = await getSession();
  if (!session) redirect("/login?next=/deep-research");

  return (
    <>
      <TopBar user={session} showProjectControls={false} />
      <DeepResearchEntry />
    </>
  );
}
