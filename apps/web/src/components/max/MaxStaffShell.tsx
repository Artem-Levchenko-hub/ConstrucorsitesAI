import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { logoutAction } from "@/app/(auth)/actions";
import { YleumMark } from "@/components/brand/YleumMark";
import { Button } from "@/components/ui/button";
import { MAX_STAFF_PATH } from "@/lib/max-staff-path";
import "./max-studio.css";
import "./max-staff.css";

export function MaxStaffShell({ children, project }: { children: React.ReactNode; project?: boolean }) {
  return <div data-max-studio data-product-shell className="max-staff-shell">
    <header className="max-staff-header">
      <span className="max-staff-brand"><YleumMark className="size-6" /> Yleum</span>
      <div className="max-staff-header-actions">
        {project && <Button asChild variant="ghost"><Link href={MAX_STAFF_PATH}><ArrowLeft aria-hidden="true" /> Рабочие приложения</Link></Button>}
        <form action={logoutAction}><Button type="submit" variant="ghost">Выйти</Button></form>
      </div>
    </header>
    <main className="max-staff-main">{children}</main>
  </div>;
}
