"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  Building2,
  CreditCard,
  LogOut,
  Receipt,
  Settings,
  Shield,
  UserRound,
  WalletCards,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { logoutAction } from "@/app/(auth)/actions";
import { popIn, springSnappy, tapSubtle } from "@/lib/motion";

const accountLinks = [
  ["/account", UserRound, "Профиль"],
  ["/account/organization", Building2, "Организация"],
  ["/account/security", Shield, "Безопасность"],
  ["/billing", WalletCards, "Баланс"],
  ["/billing/transactions", Receipt, "Операции"],
  ["/billing/plan", CreditCard, "Тариф"],
] as const;

export function MaxAccountMenu({
  email,
  onNavigate,
}: {
  email: string;
  onNavigate?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;

    function closeOnOutsidePress(event: PointerEvent) {
      if (
        event.target instanceof Node &&
        !rootRef.current?.contains(event.target)
      ) {
        setOpen(false);
      }
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    }

    document.addEventListener("pointerdown", closeOnOutsidePress);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePress);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  function followLink() {
    setOpen(false);
    onNavigate?.();
  }

  return (
    <>
      <div ref={rootRef} className="relative">
        <motion.button
          ref={triggerRef}
          type="button"
          aria-expanded={open}
          aria-controls={menuId}
          aria-label={open ? "Скрыть меню аккаунта" : "Открыть меню аккаунта"}
          data-testid="max-account-menu-trigger"
          onClick={() => setOpen((value) => !value)}
          whileTap={tapSubtle}
          className="flex min-h-11 w-full min-w-0 items-center gap-2.5 rounded-[8px] p-2 text-left transition-colors hover:bg-surface-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/35"
        >
          <span className="grid size-8 shrink-0 place-items-center rounded-full bg-fg-primary text-[11px] font-semibold text-fg-on-accent">
            {email.slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-medium">
              {email.split("@")[0]}
            </span>
            <span className="block truncate text-[9px] text-fg-tertiary">
              {email}
            </span>
          </span>
          <motion.span
            aria-hidden="true"
            animate={{ rotate: open ? 45 : 0 }}
            transition={springSnappy}
            className="grid size-6 shrink-0 place-items-center text-fg-tertiary"
          >
            <Settings className="size-3.5" />
          </motion.span>
        </motion.button>

        <AnimatePresence mode="wait" initial={false}>
          {open && (
            <motion.nav
              key="account-menu"
              id={menuId}
              aria-label="Разделы аккаунта"
              data-testid="max-account-menu"
              variants={popIn}
              initial="hidden"
              animate="visible"
              exit="exit"
              className="absolute inset-x-0 bottom-full z-30 mb-2 max-h-[min(17rem,calc(100dvh-9.5rem))] origin-bottom overflow-y-auto overscroll-contain rounded-[10px] border border-border-default bg-surface-raised p-1.5 shadow-md"
            >
              <p className="omnia-kicker px-2.5 pb-1.5 pt-1 text-fg-tertiary">
                Аккаунт
              </p>
              {accountLinks.map(([href, Icon, label]) => (
                <Link
                  key={href}
                  href={href}
                  onClick={followLink}
                  className="flex h-10 items-center gap-2.5 rounded-[7px] px-2.5 text-[11px] text-fg-secondary transition-colors hover:bg-surface-base hover:text-fg-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/35"
                >
                  <Icon className="size-3.5 shrink-0 text-fg-tertiary" />
                  {label}
                </Link>
              ))}
            </motion.nav>
          )}
        </AnimatePresence>
      </div>

      <form action={logoutAction} className="mt-1">
        <button
          type="submit"
          className="flex min-h-11 w-full items-center gap-2 rounded-[8px] px-2 text-[10px] text-fg-tertiary transition-colors hover:bg-surface-base hover:text-fg-primary"
        >
          <LogOut className="size-3.5" />
          Выйти
        </button>
      </form>
    </>
  );
}
