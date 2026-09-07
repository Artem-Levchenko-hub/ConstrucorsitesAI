import type { ReactNode } from "react";
import { Check, CircleAlert, Clock3, Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function AdminStatus({ tone, children }: {
  tone: "success" | "warning" | "danger" | "info" | "neutral"; children: ReactNode;
}) {
  const Icon = tone === "success" ? Check : tone === "warning" ? Clock3 : CircleAlert;
  return <span className={`admin-status admin-status--${tone}`}><Icon aria-hidden="true" />{children}</span>;
}
export function AdminSearch({ label, placeholder, value, onChange }: {
  label: string; placeholder: string; value: string; onChange: (value: string) => void;
}) {
  return <label className="admin-search"><Search aria-hidden="true" />
    <Input aria-label={label} placeholder={placeholder} value={value} onChange={event => onChange(event.target.value)} />
  </label>;
}
export function AdminState({ title, description, retry, loading, error }: {
  title: string; description?: string; retry?: () => void; loading?: boolean; error?: boolean;
}) {
  return <div className={`admin-state${error ? " admin-state--error" : ""}`} role={error ? "alert" : loading ? "status" : undefined}>
    {loading ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : error ? <CircleAlert aria-hidden="true" /> : null}
    <h2>{title}</h2>{description && <p>{description}</p>}
    {retry && <Button variant="outline" onClick={retry}>Повторить</Button>}
  </div>;
}
export function AdminCellLabel({ children }: { children: ReactNode }) {
  return <span className="admin-cell-label" aria-hidden="true">{children}</span>;
}
export function adminDate(value: string | null, includeTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  return includeTime ? date.toLocaleString("ru-RU") : date.toLocaleDateString("ru-RU");
}
