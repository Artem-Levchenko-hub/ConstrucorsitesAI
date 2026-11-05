import { useEffect } from "react";
import { toast } from "sonner";

export function useMaxWorkspaceDraftGuard(dirty: boolean, saving = false, scope: "project" | "chat" = "project") {
  useEffect(() => {
    if (!dirty && !saving) return;
    const chat = scope === "chat";
    // Project navigation is portaled outside the editor header.
    const linkScope = chat ? ".max-editor-header, .max-editor-modal-navigation" : ".max-project-workspace";

    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const guardLink = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[href]") : null;
      if (!link?.closest(linkScope) || (link.target && link.target !== "_self") || link.hasAttribute("download")) return;

      // Editor query links select a dialog without leaving the chat.
      if (link.origin !== location.origin || (link.pathname === location.pathname && (chat || link.search === location.search))) return;
      if (saving) {
        event.preventDefault();
        toast.info(chat ? "Дождитесь подтверждения отправки" : "Дождитесь завершения сохранения");
      } else if (!window.confirm(chat ? "Есть черновик сообщения. Перейти без отправки?" : "Есть несохранённые правки. Перейти без сохранения?")) {
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
  }, [dirty, saving, scope]);
}
