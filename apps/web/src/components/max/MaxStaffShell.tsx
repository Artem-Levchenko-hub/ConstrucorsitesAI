import Link from "next/link";
import { ArrowLeft, BookOpen, LayoutGrid, LogOut } from "lucide-react";
import { logoutAction } from "@/app/(auth)/actions";
import { YleumMark } from "@/components/brand/YleumMark";
import { Button } from "@/components/ui/button";
import { getMaxAccessServer } from "@/lib/auth-mock";
import { guideChapterHref } from "@/lib/guide-lessons";
import { MAX_STAFF_PATH } from "@/lib/max-staff-path";
import "./max-studio.css";
import "./max-staff.css";

const iconOnMobile = "max-sm:w-11 max-sm:px-0";

export async function MaxStaffShell({ children, project }: { children: React.ReactNode; project?: boolean }) {
  const studioAccess = await getMaxAccessServer();
  return <div data-max-studio data-product-shell className="max-staff-shell">
    <header className="max-topbar max-staff-header">
      <span className="max-staff-brand"><YleumMark className="size-6" /> Yleum</span>
      <div className="max-topbar-actions max-staff-header-actions">
        {project && <Button asChild variant="secondary" size="nav" className={iconOnMobile}>
          <Link href={MAX_STAFF_PATH}><ArrowLeft aria-hidden="true" /><span className="max-sm:sr-only">Рабочие приложения</span></Link>
        </Button>}
        {studioAccess?.can_create_project && <Button asChild variant="secondary" size="nav">
          <Link href="/max"><LayoutGrid aria-hidden="true" />Мои приложения</Link>
        </Button>}
        <Button asChild variant="secondary" size="nav" className={iconOnMobile}><a href={guideChapterHref("staff")} target="_blank" rel="noopener noreferrer">
          <BookOpen aria-hidden="true" /><span className="max-sm:sr-only">Руководство<span className="sr-only"> в новой вкладке</span></span>
        </a></Button>
        <form action={logoutAction}>
          <Button type="submit" variant="secondary" size="nav" className={iconOnMobile}>
            <LogOut aria-hidden="true" /><span className="max-sm:sr-only">Выйти</span>
          </Button>
        </form>
      </div>
    </header>
    <main className="max-staff-main">{children}</main>
  </div>;
}
