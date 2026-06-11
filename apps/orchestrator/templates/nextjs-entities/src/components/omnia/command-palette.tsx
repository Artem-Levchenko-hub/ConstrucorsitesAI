"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

/* ⌘K command palette — the keyboard-first jump-to anywhere every enterprise app
 * ships (Mobbin refs: Vapi «Search pages… / Actions / All Pages», Linear, Fey,
 * Superhuman «Command», Replit «Search & run commands ⌘K»). Press ⌘K (⌃K on
 * Windows/Linux) anywhere to open, type to filter, ↑↓ to move, ↵ to run, esc to
 * close. `AppShell` wires this automatically from its `nav`, so a generated app
 * gets it for free; pass extra `commands` for quick actions like «Создать
 * клиента».
 *
 * Deep, narrow interface (R-01) on the existing Dialog primitive (R-04): give it
 * a flat list of commands and a controlled open state — it owns the filtering,
 * keyboard navigation, grouping and accessibility (an ARIA listbox).
 */

/** One runnable command. A `href` navigates; an `onSelect` runs arbitrary code
 *  (e.g. open a "new" dialog). Provide one or the other. */
export interface CommandItem {
  label: string;
  href?: string;
  onSelect?: () => void;
  /** A lucide icon element, e.g. `<Users />`. */
  icon?: React.ReactNode;
  /** Section header to bucket the command under, e.g. «Страницы», «Действия». */
  group?: string;
  /** Extra words to match on that aren't shown, e.g. synonyms. */
  keywords?: string;
}

export interface CommandPaletteProps {
  commands: CommandItem[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Search box placeholder. Defaults to «Поиск страниц и действий…». */
  placeholder?: string;
  /** Shown when nothing matches. Defaults to «Ничего не найдено». */
  emptyLabel?: React.ReactNode;
}

function matches(cmd: CommandItem, q: string): boolean {
  if (!q) return true;
  const hay = `${cmd.label} ${cmd.keywords ?? ""} ${cmd.group ?? ""}`.toLowerCase();
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((tok) => hay.includes(tok));
}

function SearchIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="size-4 shrink-0 text-muted-foreground"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}

/** A small keyboard-hint chip for the footer (↑↓, ↵, esc). */
function Hint({ keys, label }: { keys: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-sans text-[10px] font-medium leading-none text-muted-foreground">
        {keys}
      </kbd>
      {label}
    </span>
  );
}

/**
 * Keyboard-first command palette. Controlled — the caller owns `open`; the
 * companion `useCommandPalette()` hook wires the global ⌘K toggle.
 */
export function CommandPalette({
  commands,
  open,
  onOpenChange,
  placeholder = "Поиск страниц и действий…",
  emptyLabel = "Ничего не найдено",
}: CommandPaletteProps) {
  const router = useRouter();
  const [query, setQuery] = React.useState("");
  const [active, setActive] = React.useState(0);
  const listRef = React.useRef<HTMLDivElement>(null);

  // Flat list of what's currently visible — the source of truth for ↑↓/↵.
  const filtered = React.useMemo(
    () => commands.filter((c) => matches(c, query)),
    [commands, query],
  );

  // Reset the query + cursor every time the palette is (re)opened, so it never
  // reopens onto a stale search.
  React.useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  // Keep the cursor in range as the filtered list shrinks.
  React.useEffect(() => {
    setActive((a) => (a >= filtered.length ? 0 : a));
  }, [filtered.length]);

  // Scroll the active row into view as the cursor moves.
  React.useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function run(cmd: CommandItem | undefined) {
    if (!cmd) return;
    onOpenChange(false);
    if (cmd.onSelect) cmd.onSelect();
    else if (cmd.href) router.push(cmd.href);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (filtered.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % filtered.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + filtered.length) % filtered.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(filtered[active]);
    }
  }

  // Group the visible commands while preserving a stable flat index for ↑↓/↵.
  const groups: { name: string; items: { cmd: CommandItem; index: number }[] }[] = [];
  filtered.forEach((cmd, index) => {
    const name = cmd.group ?? "";
    let g = groups.find((x) => x.name === name);
    if (!g) {
      g = { name, items: [] };
      groups.push(g);
    }
    g.items.push({ cmd, index });
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="top-[15%] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-xl"
        aria-label="Командная палитра"
      >
        <DialogTitle className="sr-only">Командная палитра</DialogTitle>

        {/* Search box */}
        <div className="flex items-center gap-3 border-b border-border px-4">
          <SearchIcon />
          {/* eslint-disable-next-line jsx-a11y/no-autofocus -- a command palette
              that doesn't focus its input on open is broken. */}
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            aria-label={placeholder}
            aria-controls="omnia-cmd-list"
            aria-activedescendant={
              filtered.length > 0 ? `omnia-cmd-${active}` : undefined
            }
            role="combobox"
            aria-expanded
            className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>

        {/* Results */}
        <div
          ref={listRef}
          id="omnia-cmd-list"
          role="listbox"
          aria-label="Команды"
          className="max-h-80 overflow-y-auto p-2"
        >
          {filtered.length === 0 ? (
            <p className="px-3 py-8 text-center text-sm text-muted-foreground">
              {emptyLabel}
            </p>
          ) : (
            groups.map((g) => (
              <div key={g.name || "_"} className="mb-1 last:mb-0">
                {g.name ? (
                  <p className="px-2 pb-1 pt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {g.name}
                  </p>
                ) : null}
                {g.items.map(({ cmd, index }) => {
                  const isActive = index === active;
                  return (
                    <button
                      key={index}
                      type="button"
                      id={`omnia-cmd-${index}`}
                      data-index={index}
                      role="option"
                      aria-selected={isActive}
                      onClick={() => run(cmd)}
                      onMouseMove={() => setActive(index)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors [&_svg]:size-4 [&_svg]:shrink-0",
                        isActive
                          ? "bg-accent text-accent-foreground [&_svg]:text-primary"
                          : "text-foreground [&_svg]:text-muted-foreground",
                      )}
                    >
                      {cmd.icon}
                      <span className="truncate">{cmd.label}</span>
                      {cmd.href ? (
                        <span aria-hidden="true" className="ml-auto text-muted-foreground/60">
                          ↵
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        {/* Footer hint bar */}
        <div className="flex items-center gap-4 border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
          <Hint keys="↑↓" label="выбрать" />
          <Hint keys="↵" label="перейти" />
          <Hint keys="esc" label="закрыть" />
          <span className="ml-auto tabular-nums">
            {filtered.length} {filtered.length === 1 ? "результат" : "результатов"}
          </span>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Wire the global ⌘K / ⌃K shortcut. Returns `[open, setOpen]` so the host (the
 * `AppShell`) can also open the palette from a topbar button.
 *
 *   const [open, setOpen] = useCommandPalette();
 *   <CommandPalette open={open} onOpenChange={setOpen} commands={...} />
 */
export function useCommandPalette(): [boolean, React.Dispatch<React.SetStateAction<boolean>>] {
  const [open, setOpen] = React.useState(false);
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return [open, setOpen];
}
