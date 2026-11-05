import { useEffect } from "react";
import { toast } from "sonner";

export function useMaxWorkspaceDraftGuard(dirty: boolean, saving = false) {
  useEffect(() => {
    if (!dirty && !saving) return;

    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const guardLink = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[href]") : null;
      if (!link?.closest(".max-project-workspace") || (link.target && link.target !== "_self") || link.hasAttribute("download")) return;

      if (link.origin !== location.origin || (link.pathname === location.pathname && link.search === location.search)) return;
      if (saving) {
        event.preventDefault();
        toast.info("Дождитесь завершения сохранения");
      } else if (!window.confirm("Есть несохранённые правки. Перейти без сохранения?")) {
        event.preventDefault();
      }
    };

    window.addEventListener("beforeunload", warn);
    // Next links can change routes without firing beforeunload.
    document.addEventListener("click", guardLink, true);
    return () => {
      window.removeEventListener("beforeunload", warn);
      document.removeEventListener("click", guardLink, true);
    };
  }, [dirty, saving]);
}
