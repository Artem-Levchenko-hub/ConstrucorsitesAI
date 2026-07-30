import { Check } from "lucide-react";

import { AccountControlCenter } from "@/components/account/AccountControlCenter";
import { AccountShell } from "@/components/account/AccountShell";
import { getSession } from "@/lib/auth-mock";
import { redirect } from "next/navigation";

export default async function PlanPage() {
  const session = await getSession();
  if (!session) redirect("/login");
  return (
    <AccountShell email={session.email} active="plan">
      <section className="mb-6 rounded-[12px] border-2 border-[#f15a38] bg-[#fcfbf7] p-6">
        <p className="omnia-kicker text-[#f15a38]">Текущий режим</p>
        <h2 className="mt-2 text-2xl font-semibold">MAX Studio</h2>
        <p className="mt-2 text-sm text-[#6d6962]">Создание, интеграции, публикация и эксплуатация MAX Mini Apps.</p>
        <div className="mt-6 grid gap-3 text-sm sm:grid-cols-2">
          {["Продуктовый AI-агент", "Рабочий backend и база", "MAX Bot API и webhook", "Версии, деплой и откат"].map((item) => <p key={item} className="flex items-center gap-2 text-[#6d6962]"><Check className="size-4 text-[#248a4b]" />{item}</p>)}
        </div>
      </section>
      <AccountControlCenter email={session.email} view="plan" />
    </AccountShell>
  );
}
