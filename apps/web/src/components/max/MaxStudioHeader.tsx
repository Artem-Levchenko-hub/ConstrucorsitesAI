"use client";

import Link from "next/link";
import { ArrowLeft, ChevronDown, LogOut, Settings, User } from "lucide-react";

import { logoutAction } from "@/app/(auth)/actions";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function MaxStudioHeader({
  email,
  compact = false,
}: {
  email: string;
  compact?: boolean;
}) {
  const initial = email.slice(0, 1).toUpperCase();

  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-white/[0.07] bg-[#0b0c12]/90 px-5 backdrop-blur-xl sm:px-8">
      <div className="flex min-w-0 items-center gap-3">
        <Link
          href="/max"
          className="flex min-w-0 items-center gap-3"
          aria-label="MAX Studio — главная"
        >
          <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-[12px] bg-[linear-gradient(145deg,#7468ff,#5b5cff)] shadow-[0_10px_30px_-10px_rgba(102,92,255,0.9)]">
            <span className="h-3.5 w-4 rounded-[5px] border-2 border-white after:absolute after:bottom-[8px] after:left-[11px] after:h-1.5 after:w-1.5 after:rotate-45 after:border-b-2 after:border-r-2 after:border-white" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold tracking-tight text-white">
              MAX Studio
            </span>
            {!compact && (
              <span className="hidden text-[11px] text-white/40 sm:block">
                от Omnia.AI
              </span>
            )}
          </span>
        </Link>
        {compact && (
          <span className="hidden items-center gap-2 text-xs text-white/35 sm:flex">
            <span>/</span>
            <span>Конструктор мини-приложений</span>
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button
          asChild
          variant="ghost"
          size="sm"
          className="hidden text-white/60 hover:bg-white/[0.06] hover:text-white sm:inline-flex"
        >
          <Link href="/projects">
            <ArrowLeft className="h-3.5 w-3.5" />
            Все проекты
          </Link>
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="gap-2 px-1.5 text-white hover:bg-white/[0.06]"
            >
              <Avatar className="h-8 w-8">
                <AvatarFallback>{initial}</AvatarFallback>
              </Avatar>
              <ChevronDown className="h-3.5 w-3.5 text-white/35" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
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
            <DropdownMenuItem asChild>
              <Link href="/account">
                <Settings className="h-4 w-4" />
                Аккаунт
              </Link>
            </DropdownMenuItem>
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
