"use client";

import Link from "next/link";
import { ArrowLeft, ChevronDown, LogOut, Settings, User } from "lucide-react";

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

export function MaxStudioHeader({
  email,
  compact = false,
}: {
  email: string;
  compact?: boolean;
}) {
  const initial = email.slice(0, 1).toUpperCase();

  return (
    <header data-product-shell className="flex h-16 shrink-0 items-center justify-between border-b border-[#2b2d32] bg-[#191b20]/95 px-5 text-white backdrop-blur-xl sm:px-8">
      <div className="flex min-w-0 items-center gap-3">
        <BrandMark href="/max" label="MAX Studio" />
        {compact && (
          <span className="hidden items-center gap-2 text-xs text-[#828491] sm:flex">
            <span className="h-4 w-px bg-[#2b2d32]" />
            <span>Редактор</span>
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button
          asChild
          variant="ghost"
          size="sm"
          className="hidden text-[#9fa1b1] hover:bg-[#121519] hover:text-white sm:inline-flex"
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
              className="gap-2 px-1.5 text-white hover:bg-[#121519]"
            >
              <Avatar className="h-8 w-8">
                <AvatarFallback>{initial}</AvatarFallback>
              </Avatar>
              <ChevronDown className="h-3.5 w-3.5 text-[#828491]" />
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
