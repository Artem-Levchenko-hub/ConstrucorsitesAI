"use client";

import { motion, useReducedMotion } from "framer-motion";
import {
  Building2,
  ChevronDown,
  CreditCard,
  Receipt,
  Shield,
  UserRound,
  WalletCards,
} from "lucide-react";
import Link from "next/link";
import { useId, useRef, useState } from "react";

const accountLinks = [
  ["/account", UserRound, "Профиль"],
  ["/account/organization", Building2, "Организация"],
  ["/account/security", Shield, "Безопасность"],
  ["/billing", WalletCards, "Баланс"],
  ["/billing/transactions", Receipt, "Операции"],
  ["/billing/plan", CreditCard, "Тариф"],
] as const;

const smoothEase = [0.22, 1, 0.36, 1] as const;

export function MaxStudioAccountDisclosure() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const reduceMotion = useReducedMotion();

  function closeOnEscape(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape") return;
    setOpen(false);
    triggerRef.current?.focus();
  }

  return (
    <div className="mt-1" onKeyDown={closeOnEscape}>
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls={menuId}
        data-testid="max-studio-account-trigger"
        onClick={() => setOpen((current) => !current)}
        className="flex min-h-11 w-full items-center gap-3 rounded-[8px] px-3 py-2.5 text-left text-fg-secondary transition-colors hover:bg-surface-base hover:text-fg-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <UserRound className="size-4 shrink-0" />
        <span className="flex-1">Аккаунт</span>
        <motion.span
          aria-hidden="true"
          animate={{ rotate: open ? 180 : 0 }}
          transition={{
            duration: reduceMotion ? 0 : 0.18,
            ease: smoothEase,
          }}
          className="grid size-5 shrink-0 place-items-center text-fg-tertiary"
        >
          <ChevronDown className="size-3.5" />
        </motion.span>
      </button>

      <motion.div
        id={menuId}
        aria-hidden={!open}
        inert={!open}
        data-testid="max-studio-account-menu"
        initial={false}
        animate={{ height: open ? "auto" : 0, opacity: open ? 1 : 0 }}
        transition={{
          duration: reduceMotion ? 0.01 : 0.22,
          ease: smoothEase,
        }}
        className="overflow-hidden"
      >
        <nav
          aria-label="Разделы аккаунта"
          className="mb-1 ml-5 mt-1 border-l border-border-subtle py-1 pl-3"
        >
          {accountLinks.map(([href, Icon, label], index) => (
            <motion.div
              key={href}
              initial={false}
              animate={{
                opacity: open ? 1 : 0,
                y: open || reduceMotion ? 0 : -4,
              }}
              transition={{
                delay: open && !reduceMotion ? index * 0.025 : 0,
                duration: reduceMotion ? 0.01 : 0.16,
                ease: smoothEase,
              }}
            >
              <Link
                href={href}
                tabIndex={open ? 0 : -1}
                className="flex min-h-9 items-center gap-2.5 rounded-[7px] px-2.5 text-[11px] text-fg-secondary transition-colors hover:bg-surface-base hover:text-fg-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <Icon className="size-3.5 shrink-0 text-fg-tertiary" />
                {label}
              </Link>
            </motion.div>
          ))}
        </nav>
      </motion.div>
    </div>
  );
}
