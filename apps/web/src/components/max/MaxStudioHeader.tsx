"use client";

import Link from "next/link";
import { Building2, ChevronDown, CircleHelp, CreditCard, LogOut, Receipt, Shield, User, WalletCards } from "lucide-react";

import { logoutAction } from "@/app/(auth)/actions";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/marketing/BrandMark";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import "./max-studio.css";

const accountLinks = [
  ["/account", User, "Профиль"],
  ["/account/organization", Building2, "Организация"],
  ["/account/security", Shield, "Безопасность"],
  ["/billing", WalletCards, "Баланс"],
  ["/billing/transactions", Receipt, "Операции"],
  ["/billing/plan", CreditCard, "Тариф"],
] as const;

export function MaxStudioHeader({
  email,
  compact = false,
}: {
  email: string;
  compact?: boolean;
}) {
  const initial = email.slice(0, 1).toUpperCase();

  return (
    <header data-max-studio className="max-studio-header">
      <div className="flex min-w-0 items-center gap-3">
        <BrandMark href="/max" label="MAX Studio" />
        {compact && (
          <span className="hidden items-center gap-2 text-xs text-fg-secondary sm:flex">
            <span className="h-4 w-px bg-border-default" />
            <span>Редактор</span>
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button
          asChild
          variant="ghost"
          size="sm"
          className="min-h-11 px-2"
        >
          <Link href="/max/start" aria-label="Помощь и быстрый старт">
            <CircleHelp className="h-4 w-4" />
            <span className="hidden sm:inline">Помощь</span>
          </Link>
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              aria-label="Аккаунт"
              className="min-h-11 gap-2 px-1.5"
            >
              <Avatar className="h-8 w-8">
                <AvatarFallback>{initial}</AvatarFallback>
              </Avatar>
              <span className="hidden sm:inline">Аккаунт</span>
              <ChevronDown className="h-3.5 w-3.5 text-fg-secondary" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent data-max-studio align="end">
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
            {accountLinks.map(([href, Icon, label]) => <DropdownMenuItem asChild key={href}><Link href={href}><Icon className="h-4 w-4" />{label}</Link></DropdownMenuItem>)}
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <form action={logoutAction} className="w-full">
                <button type="submit" className="flex w-full items-center gap-2">
                  <LogOut className="h-4 w-4" />
                  Выйти
                </button>
              </form>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
