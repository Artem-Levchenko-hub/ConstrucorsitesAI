import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth-mock";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSession();
  if (!session) redirect("/login");

  return <div className="min-h-svh flex flex-col">{children}</div>;
}
