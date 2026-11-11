"use client";

import Link from "next/link";
import { ChevronDown, CircleHelp, CreditCard, LogOut, Receipt, Shield, User, UsersRound, WalletCards } from "lucide-react";

import { logoutAction } from "@/app/(auth)/actions";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/marketing/BrandMark";
import { LearningButton } from "@/components/learning/ProductLearning";
import { MAX_STAFF_PATH, maxStaffEnabled } from "@/lib/max-staff-path";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import "./max-account-dropdown.css";
import "./max-studio.css";

const accountLinks = [
  [MAX_STAFF_PATH, UsersRound, "Кабинет сотрудника"],
  ["/account", User, "Профиль"],
  ["/account/security", Shield, "Безопасность"],
  ["/billing", WalletCards, "Баланс"],
  ["/billing/transactions", Receipt, "Операции"],
  ["/billing/plan", CreditCard, "Тариф"],
] as const;

export function MaxStudioHeader({
  email,
  compact = false,
  showLearning = true,
}: {
  email: string;
  compact?: boolean;
  showLearning?: boolean;
}) {
  const initial = email.slice(0, 1).toUpperCase();

  return (
    <header data-max-studio className="max-topbar">
      <div className="flex min-w-0 items-center gap-3">
        <BrandMark href="/max" />
        {compact && (
          <span className="hidden items-center gap-2 text-xs text-fg-secondary sm:flex">
            <span className="h-4 w-px bg-border-default" />
            <span>Редактор</span>
          </span>
        )}
      </div>

      <div className="max-topbar-actions">
        {showLearning && <LearningButton className="max-[360px]:w-11 max-[360px]:px-0 max-[360px]:[&>span]:sr-only" />}
        <Button asChild variant="secondary" size="nav" className="max-sm:w-11 max-sm:px-0">
          <Link href="/max/start" aria-label="Помощь и быстрый старт" data-learning="projects-help">
            <CircleHelp aria-hidden="true" />
            <span className="hidden sm:inline">Помощь</span>
          </Link>
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="secondary"
              size="nav"
              aria-label="Аккаунт"
              data-learning="account-menu"
              className="group data-[state=open]:bg-surface-3 max-sm:w-11 max-sm:px-0"
            >
              <Avatar aria-hidden="true" className="size-6">
                <AvatarFallback>{initial}</AvatarFallback>
              </Avatar>
              <span className="hidden sm:inline">Аккаунт</span>
              <ChevronDown aria-hidden="true" className="hidden text-fg-secondary transition-transform duration-[var(--yleum-motion-control)] ease-[var(--yleum-motion-ease)] group-data-[state=open]:rotate-180 motion-reduce:transition-none sm:block" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            data-max-studio
            align="end"
            className="max-account-dropdown"
          >
            <DropdownMenuLabel className="font-normal">
              <div className="text-xs text-fg-tertiary">Вошли как</div>
              <div className="max-w-[220px] truncate text-sm text-fg-primary">
                {email}
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/max">
                <User className="h-4 w-4" />
                Мои MAX-приложения
              </Link>
            </DropdownMenuItem>
            {accountLinks.filter(([href]) => maxStaffEnabled || href !== MAX_STAFF_PATH).map(([href, Icon, label]) => (
              <DropdownMenuItem asChild key={href}><Link href={href}><Icon className="h-4 w-4" />{label}</Link></DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <form action={logoutAction} className="w-full">
              <DropdownMenuItem asChild onSelect={(event) => event.preventDefault()}>
                <button type="submit">
                  <LogOut className="h-4 w-4" />
                  Выйти
                </button>
              </DropdownMenuItem>
            </form>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
