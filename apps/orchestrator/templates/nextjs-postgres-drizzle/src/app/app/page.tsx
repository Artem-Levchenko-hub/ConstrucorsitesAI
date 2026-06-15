/**
 * Default signed-in cabinet for a freshly provisioned fullstack project.
 *
 * Sign-in lands here (`/signin` default `next`), so it is the FIRST thing an
 * owner sees once authed — the work surface, the dark counterpart to the public
 * landing. It opens with a <DashboardHero> — the signature brand-aurora band
 * (the kit's "surface zero") — so the first screen reads as one design product
 * with the landing and auth chrome, not a flat heading. AI replaces / extends
 * this when the user describes what to store and show: it adds nav items, a
 * focal metric on the hero, real Drizzle-backed StatCards (with inline
 * <Sparkline> trends), <TrendArea>/<DonutStat>/<BarMini> charts for the metrics
 * region, and DataTable lists — all from the same `@/components/omnia` kit, so
 * the cabinet grows premium from the first prompt instead of starting as a bare
 * shadcn page.
 *
 * Honest zero-state on purpose: a fresh project has no entities yet, so the
 * dashboard shows the real account plus an explicit "your data will appear
 * here" — never fake numbers. Self-contained on `share.accent` (zero per-app
 * model cost), server-rendered, Protected behind auth.
 */
import { Database, Home, LayoutDashboard, Layers, Rows3, Sparkles, User } from "lucide-react";

import { Protected } from "@/components/Protected";
import {
  AppShell,
  DashboardHero,
  DataTable,
  EmptyState,
  StatCard,
  type NavItem,
} from "@/components/omnia";
import { share } from "@/app/omnia-share";
import { brandName } from "@/lib/brand";
import { getCurrentUser } from "@/lib/session";

const NAV: NavItem[] = [
  { label: "Обзор", href: "/app", icon: <LayoutDashboard />, section: "Рабочая область" },
  { label: "На сайт", href: "/", icon: <Home />, section: "Рабочая область" },
];

export default async function CabinetHome() {
  return (
    <Protected next="/app">
      <Cabinet />
    </Protected>
  );
}

async function Cabinet() {
  const user = await getCurrentUser();
  const accent = share.accent || "#6366f1";
  const name = brandName();

  return (
    <AppShell
      brand={name}
      accent={accent}
      nav={NAV}
      user={user ? { name: user.name, email: user.email } : null}
      title="Обзор"
    >
      <DashboardHero
        eyebrow="Кабинет"
        title={`Здравствуйте${user?.name ? `, ${user.name.split(" ")[0]}` : ""}`}
        description="Стартовый кабинет готов. Опишите в чате слева, что нужно хранить и показывать — AI добавит сущности, таблицы и страницы прямо сюда, под этим же дизайном."
      />

      {/* KPI row — honest fresh-project state, no fabricated numbers. The
          `stagger` wrapper rises each tile in sequence (the "living dashboard"
          cascade) instead of one flat fade — obeys the per-app MOTION-DNA. */}
      <div className="stagger grid gap-4 sm:grid-cols-3">
        <StatCard
          label="Аккаунт"
          value="1"
          hint={user?.email ?? "вы вошли"}
          icon={<User />}
        />
        <StatCard label="Сущности" value="0" hint="ждут вашего промпта" icon={<Layers />} />
        <StatCard label="Записи" value="0" hint="появятся после генерации" icon={<Rows3 />} />
      </div>

      {/* Data region — premium empty list, the canonical cabinet zero-state. */}
      <div className="fade-up delay-2 mt-8">
        <DataTable
          caption="Последние записи"
          columns={[
            { key: "title", header: "Название" },
            { key: "status", header: "Статус" },
            { key: "created", header: "Создано", align: "right" },
          ]}
          rows={[]}
          empty={
            <EmptyState
              bare
              icon={<Database />}
              title="Здесь появятся ваши данные"
              description="Postgres и вход уже работают. Опишите первую сущность — например «заказы с клиентом, суммой и статусом» — и AI создаст таблицу, форму и эту страницу со списком."
              action={
                <span className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-4 py-2.5 text-sm font-medium text-zinc-200">
                  <Sparkles className="size-4 text-[var(--brand)]" />
                  Напишите промпт слева
                </span>
              }
            />
          }
        />
      </div>
    </AppShell>
  );
}
