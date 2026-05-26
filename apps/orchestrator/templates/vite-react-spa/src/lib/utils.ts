import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Standard shadcn-style `cn` helper — merges class lists with Tailwind
 *  precedence so later utilities override earlier ones cleanly. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
