"use client";

import { useEffect, useState } from "react";

const NATIVE_LEGAL_NAV_SELECTOR = '[data-omnia-native-legal-nav="true"]';
const COMPLIANCE_FALLBACK_SELECTOR = '[data-omnia-compliance-fallback="true"]';
const REQUIRED_LINKS = ["/support", "/legal/privacy", "/legal/terms"] as const;

function hasNativeLegalNavigation() {
  const isNative = (element: Element) =>
    element.closest(COMPLIANCE_FALLBACK_SELECTOR) === null;
  if ([...document.querySelectorAll(NATIVE_LEGAL_NAV_SELECTOR)].some(isNative)) {
    return true;
  }
  return REQUIRED_LINKS.every((href) =>
    [...document.querySelectorAll(`a[href="${href}"]`)].some(isNative),
  );
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
    <details
      aria-label="Служебная информация"
      data-omnia-compliance-fallback="true"
    >
      <summary>О приложении</summary>
      <nav aria-label="Поддержка и правовая информация">
        <a href="/support">Поддержка</a>{" · "}
        <a href="/legal/privacy">Конфиденциальность</a>{" · "}
        <a href="/legal/terms">Условия</a>
      </nav>
    </details>
  );
}
