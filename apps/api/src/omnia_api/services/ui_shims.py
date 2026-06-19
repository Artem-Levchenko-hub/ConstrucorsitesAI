"""Self-contained shims for shadcn ``@/components/ui/*`` the template doesn't ship.

The nextjs-entities template ships a FIXED set of shadcn primitives (avatar,
badge, button, card, checkbox, dialog, dropdown-menu, input, label, select,
separator, sheet, skeleton, sonner, table, tabs, textarea, tooltip). Writer
models routinely reach for a *standard* shadcn component that ISN'T shipped
(``radio-group``, ``switch``, …) — Next.js then fails the build with
``Module not found: Can't resolve '@/components/ui/radio-group'`` and the whole
generated app renders a build-error page (the owner hit exactly this).

The instruction-side fix (``_ENTITIES_STACK`` lists the exact available set) is
the primary prevention; this is the deterministic SAFETY NET: if a generated
.tsx imports a missing ``@/components/ui/X`` for which we have a self-contained
shim, inject ``src/components/ui/X.tsx`` into the generated files so the import
resolves and the app builds. Shims are dependency-free (no new ``@radix-ui/*``
package) — plain React + ``@/lib/utils`` ``cn`` + lucide, all already present —
so they need NO template rebuild / no package.json change.
"""

from __future__ import annotations

import re

# Components the template already ships — never shimmed.
_SHIPPED: frozenset[str] = frozenset(
    {
        "avatar", "badge", "button", "card", "checkbox", "dialog",
        "dropdown-menu", "input", "label", "select", "separator", "sheet",
        "skeleton", "sonner", "table", "tabs", "textarea", "tooltip",
    }
)

_UI_IMPORT_RE = re.compile(r"""['"]@/components/ui/([a-z0-9-]+)['"]""")

# ── Dependency-free shadcn-compatible shims (no @radix-ui) ────────────────────
_RADIO_GROUP = '''\
"use client";

import * as React from "react";
import { Circle } from "lucide-react";
import { cn } from "@/lib/utils";

type Ctx = { value?: string; onValueChange?: (v: string) => void };
const RadioGroupContext = React.createContext<Ctx>({});

const RadioGroup = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & {
    value?: string;
    defaultValue?: string;
    onValueChange?: (v: string) => void;
  }
>(({ className, value, defaultValue, onValueChange, ...props }, ref) => {
  const [internal, setInternal] = React.useState(defaultValue);
  const current = value !== undefined ? value : internal;
  const change = React.useCallback(
    (v: string) => {
      if (value === undefined) setInternal(v);
      onValueChange?.(v);
    },
    [value, onValueChange]
  );
  return (
    <RadioGroupContext.Provider value={{ value: current, onValueChange: change }}>
      <div ref={ref} role="radiogroup" className={cn("grid gap-2", className)} {...props} />
    </RadioGroupContext.Provider>
  );
});
RadioGroup.displayName = "RadioGroup";

const RadioGroupItem = React.forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { value: string }
>(({ className, value, ...props }, ref) => {
  const ctx = React.useContext(RadioGroupContext);
  const checked = ctx.value === value;
  return (
    <button
      ref={ref}
      type="button"
      role="radio"
      aria-checked={checked}
      onClick={() => ctx.onValueChange?.(value)}
      className={cn(
        "aspect-square h-4 w-4 rounded-full border border-primary text-primary shadow",
        "flex items-center justify-center focus:outline-none",
        "focus-visible:ring-1 focus-visible:ring-ring",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    >
      {checked ? <Circle className="h-2.5 w-2.5 fill-current text-current" /> : null}
    </button>
  );
});
RadioGroupItem.displayName = "RadioGroupItem";

export { RadioGroup, RadioGroupItem };
'''

_SWITCH = '''\
"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

const Switch = React.forwardRef<
  HTMLButtonElement,
  Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> & {
    checked?: boolean;
    defaultChecked?: boolean;
    onCheckedChange?: (checked: boolean) => void;
  }
>(({ className, checked, defaultChecked, onCheckedChange, ...props }, ref) => {
  const [internal, setInternal] = React.useState(!!defaultChecked);
  const on = checked !== undefined ? checked : internal;
  return (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => {
        if (checked === undefined) setInternal(!on);
        onCheckedChange?.(!on);
      }}
      className={cn(
        "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center",
        "rounded-full border-2 border-transparent transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "disabled:cursor-not-allowed disabled:opacity-50",
        on ? "bg-primary" : "bg-input",
        className
      )}
      {...props}
    >
      <span
        className={cn(
          "pointer-events-none block h-4 w-4 rounded-full bg-background",
          "shadow-lg ring-0 transition-transform",
          on ? "translate-x-4" : "translate-x-0"
        )}
      />
    </button>
  );
});
Switch.displayName = "Switch";

export { Switch };
'''

_SHIMS: dict[str, str] = {
    "radio-group": _RADIO_GROUP,
    "switch": _SWITCH,
}


def ensure_ui_shims(files: dict[str, str]) -> tuple[dict[str, str], list[str], list[str]]:
    """Inject self-contained shims for missing ``@/components/ui/*`` imports.

    Returns ``(files, injected, missing_unshimmed)``:
    * ``injected`` — components we added a shim for (build saved);
    * ``missing_unshimmed`` — imported, not shipped, and we have NO shim
      (the caller should log these — they will still break the build).
    Side-effect-free except adding shim files; never overwrites an existing file.
    """
    imported: set[str] = set()
    for path, code in files.items():
        if isinstance(code, str) and path.endswith(".tsx"):
            imported.update(_UI_IMPORT_RE.findall(code))

    injected: list[str] = []
    missing: list[str] = []
    for comp in sorted(imported):
        if comp in _SHIPPED:
            continue
        target = f"src/components/ui/{comp}.tsx"
        if target in files:
            continue
        shim = _SHIMS.get(comp)
        if shim is not None:
            files[target] = shim
            injected.append(comp)
        else:
            missing.append(comp)
    return files, injected, missing


__all__ = ["ensure_ui_shims"]
