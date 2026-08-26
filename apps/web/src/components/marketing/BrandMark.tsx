import Link from "next/link";
import { Asterisk } from "lucide-react";

export function BrandMark({
  inverse = false,
  href = "/",
  label = "Omnia",
}: {
  inverse?: boolean;
  href?: string;
  label?: string;
}) {
  return (
    <Link
      href={href}
      className="font-display inline-flex items-center gap-2 font-semibold tracking-[-0.025em]"
      aria-label={`${label} — главная`}
    >
      <span
        className={`flex h-8 w-8 items-center justify-center rounded-[8px] bg-accent text-accent-fg ${
          inverse ? "" : "ring-1 ring-inset ring-white/10"
        }`}
        aria-hidden
      >
        <Asterisk className="h-[18px] w-[18px]" strokeWidth={2.2} />
      </span>
      <span className="text-[18px] text-fg-primary">
        {label}
      </span>
    </Link>
  );
}
