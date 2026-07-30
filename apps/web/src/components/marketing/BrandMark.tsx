import Link from "next/link";

export function BrandMark({
  inverse = false,
  href = "/",
}: {
  inverse?: boolean;
  href?: string;
}) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-2.5 font-semibold tracking-[-0.025em]"
      aria-label="Omnia — главная"
    >
      <span
        className={`grid h-7 w-7 grid-cols-2 gap-[3px] border p-[5px] ${
          inverse ? "border-white/70" : "border-[#171815]"
        }`}
        aria-hidden
      >
        <span className={inverse ? "bg-white" : "bg-[#171815]"} />
        <span className={inverse ? "bg-white/35" : "bg-[#315bd7]"} />
        <span className={inverse ? "bg-white/35" : "bg-[#315bd7]"} />
        <span className={inverse ? "bg-white" : "bg-[#171815]"} />
      </span>
      <span className="text-[17px]">Omnia</span>
    </Link>
  );
}
