"use client";

import { useEffect, useState } from "react";

const NATIVE_LEGAL_NAV_SELECTOR = '[data-omnia-native-legal-nav="true"]';
const REQUIRED_LINKS = ["/support", "/legal/privacy", "/legal/terms"] as const;

function hasNativeLegalNavigation() {
  if (document.querySelector(NATIVE_LEGAL_NAV_SELECTOR)) return true;
  return REQUIRED_LINKS.every((href) => document.querySelector(`a[href="${href}"]`));
}

/**
 * Compatibility fallback for products created before native legal navigation
 * became part of the product contract. New products own link placement and
 * suppress this disclosure with data-omnia-native-legal-nav="true".
 *
 * The optional prop keeps older direct <OmniaCompliance /> imports compiling
 * without reintroducing the former persistent footer.
 */
export function OmniaCompliance({ fallback = false }: { fallback?: boolean } = {}) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!fallback) return;
    const refresh = () => setVisible(!hasNativeLegalNavigation());
    refresh();
    const observer = new MutationObserver(refresh);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [fallback]);

  if (!fallback || !visible) return null;

  return (
    <details aria-label="Служебная информация">
      <summary>О приложении</summary>
      <nav aria-label="Поддержка и правовая информация">
        <a href="/support">Поддержка</a>{" · "}
        <a href="/legal/privacy">Конфиденциальность</a>{" · "}
        <a href="/legal/terms">Условия</a>
      </nav>
    </details>
  );
}
