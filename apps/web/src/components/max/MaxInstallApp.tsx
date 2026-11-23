"use client";

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Download, Share, Smartphone } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import "./max-mobile.css";

type InstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};
type InstallState = {
  installed: boolean;
  prompt: InstallPrompt | null;
  clearPrompt: () => void;
};
const InstallContext = createContext<InstallState>({ installed: false, prompt: null, clearPrompt: () => {} });

/** Listen before menus open: browsers emit the install event once per page. */
export function PwaInstallProvider({ children }: { children: ReactNode }) {
  const [prompt, setPrompt] = useState<InstallPrompt | null>(null);
  const [installed, setInstalled] = useState(false);
  useEffect(() => {
    const standalone = window.matchMedia?.("(display-mode: standalone)");
    const updateDisplay = () => setInstalled(!!standalone?.matches ||
      (navigator as Navigator & { standalone?: boolean }).standalone === true);
    const capture = (event: Event) => { event.preventDefault(); setPrompt(event as InstallPrompt); };
    const finish = () => { setInstalled(true); setPrompt(null); };
    updateDisplay();
    window.addEventListener("beforeinstallprompt", capture);
    window.addEventListener("appinstalled", finish);
    standalone?.addEventListener("change", updateDisplay);
    return () => {
      window.removeEventListener("beforeinstallprompt", capture);
      window.removeEventListener("appinstalled", finish);
      standalone?.removeEventListener("change", updateDisplay);
    };
  }, []);
  return <InstallContext.Provider value={{ installed, prompt, clearPrompt: () => setPrompt(null) }}>{children}</InstallContext.Provider>;
}

export function MaxInstallButton() {
  const { installed, prompt, clearPrompt } = useContext(InstallContext);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  if (installed) return null;

  async function install() {
    if (!prompt) { setOpen(true); return; }
    setBusy(true);
    try {
      await prompt.prompt();
      await prompt.userChoice;
    } catch {
      setOpen(true);
    } finally {
      clearPrompt();
      setBusy(false);
    }
  }

  return <>
    <button ref={trigger} type="button" className="max-install-button" aria-label="На главный экран" onClick={() => void install()} disabled={busy}>
      <Download aria-hidden="true" /><span>На главный экран</span>
    </button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent data-product-shell data-max-studio className="max-install-dialog" onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus(); }}>
        <DialogHeader>
          <div className="max-install-symbol"><Smartphone aria-hidden="true" /></div>
          <DialogTitle>Yleum всегда под рукой</DialogTitle>
          <DialogDescription>Добавьте на главный экран и открывайте как приложение.</DialogDescription>
        </DialogHeader>
        <section><h3>На iPhone</h3><p>Откройте Yleum в Safari, нажмите <Share aria-hidden="true" /> «Поделиться» → «На экран “Домой”». Оставьте включённым «Открывать как веб-приложение», если этот пункт есть.</p></section>
        <section><h3>На Android</h3><p>Откройте меню браузера ⋮ → «Установить приложение» или «Добавить на главный экран».</p></section>
        <button type="button" className="max-install-done" onClick={() => setOpen(false)}>Понятно</button>
      </DialogContent>
    </Dialog>
  </>;
}
