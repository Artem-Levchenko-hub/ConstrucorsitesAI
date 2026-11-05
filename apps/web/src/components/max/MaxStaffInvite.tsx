"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, UsersRound } from "lucide-react";
import { YleumMark } from "@/components/brand/YleumMark";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { acceptMaxInvitation, staffRequestError } from "@/lib/api/max-staff";
import { MAX_STAFF_JOIN_PATH, maxStaffProjectPath } from "@/lib/max-staff-path";
import { EmailVerification } from "./EmailVerification";
import "./max-studio.css";
import "./max-staff.css";

const inviteKey = "yleum-max-staff-invitation";
const loginHref = `/login?next=${encodeURIComponent(MAX_STAFF_JOIN_PATH)}`;
const registerHref = `/max/register?next=${encodeURIComponent(MAX_STAFF_JOIN_PATH)}`;

function readInvitation(): { token: string | null; stored: boolean } {
  const hash = window.location.hash;
  const received = new URLSearchParams(hash.slice(1)).get("token");
  const token = received && /^[a-zA-Z\d_-]{43}$/.test(received) ? received : null;
  if (hash || window.location.search) window.history.replaceState(window.history.state, "", MAX_STAFF_JOIN_PATH);
  try {
    if (!hash) return { token: sessionStorage.getItem(inviteKey), stored: true };
    if (token) sessionStorage.setItem(inviteKey, token);
    else sessionStorage.removeItem(inviteKey);
    return { token, stored: true };
  } catch {
    return { token, stored: false };
  }
}

function clearInvitation() {
  try { sessionStorage.removeItem(inviteKey); } catch { /* No browser storage available. */ }
}

export function MaxStaffInvite({ authenticated, email, verified, emailDeliveryAvailable }: {
  authenticated: boolean; email: string | null; verified: boolean; emailDeliveryAvailable: boolean;
}) {
  const router = useRouter();
  const [invitation, setInvitation] = useState<ReturnType<typeof readInvitation> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [emailConfirmed, setEmailConfirmed] = useState(false);
  const accepting = useRef(false);
  const initialized = useRef(false);
  useEffect(() => {
    const load = () => { setInvitation(readInvitation()); setError(null); };
    if (!initialized.current) { initialized.current = true; load(); }
    window.addEventListener("hashchange", load);
    return () => window.removeEventListener("hashchange", load);
  }, []);

  async function accept() {
    if (!invitation?.token || accepting.current) return;
    accepting.current = true; setPending(true); setError(null);
    try {
      const result = await acceptMaxInvitation(invitation.token);
      clearInvitation();
      setInvitation({ token: null, stored: true });
      router.replace(maxStaffProjectPath(result.project_id));
    } catch (failure) {
      if (failure instanceof ApiError && String(failure.code) === "staff_invite_invalid") {
        clearInvitation(); setInvitation({ token: null, stored: true }); setError("invalid");
      } else if (failure instanceof ApiError && String(failure.code) === "staff_invite_account_mismatch") {
        setError("mismatch");
      } else if (failure instanceof ApiError && String(failure.code) === "staff_verification_required") {
        setError("verify");
      } else if (failure instanceof ApiError && failure.status === 401) {
        setError("login");
      } else {
        setError(staffRequestError(failure));
      }
    } finally {
      accepting.current = false; setPending(false);
    }
  }

  const needsLogin = !authenticated || !email || error === "login";
  const needsVerification = (!verified && !emailConfirmed) || error === "verify";
  return <main data-max-studio data-product-shell className="max-staff-join">
    <div className="max-staff-join-brand"><YleumMark className="size-6" /> Yleum</div>
    <section className="max-staff-join-card" aria-labelledby="staff-join-title">
      <div className="max-staff-join-symbol"><UsersRound aria-hidden="true" className="size-6" /></div>
      <p className="max-staff-kicker">Рабочий кабинет</p>
      <h1 id="staff-join-title">Вас пригласили в команду</h1>
      {!invitation ? <p role="status">Проверяем ссылку…</p> : !invitation.token ? <p role="status">
        {error === "invalid" ? "Срок действия ссылки истёк или её уже использовали. Попросите владельца прислать новое приглашение." : "Откройте ссылку, которую прислал владелец приложения."}
      </p> : <>
        <p>Примите приглашение, чтобы открыть рабочие разделы приложения в Yleum.</p>
        {email && !needsLogin && <p className="max-staff-join-account">Вы вошли как <strong>{email}</strong></p>}
        {error === "mismatch" && <p role="alert">Приглашение оформлено на другой email. Войдите под аккаунтом, указанным владельцем.</p>}
        {error && !["mismatch", "verify", "login"].includes(error) && <p role="alert">{error}</p>}
        {!invitation.stored && (needsLogin || needsVerification || error === "mismatch") && <p role="status">
          После входа откройте исходную ссылку ещё раз. Этот браузер не сохраняет приглашение между страницами.
        </p>}
        <div className="max-staff-actions">
          {needsLogin || error === "mismatch" ? <>
            <Button asChild><Link href={loginHref}>{needsLogin ? "Войти" : "Войти под другим email"} <ArrowRight aria-hidden="true" /></Link></Button>
            {needsLogin && <Button asChild variant="outline"><Link href={registerHref}>Создать аккаунт</Link></Button>}
          </> : needsVerification && email ? <EmailVerification email={email} available={emailDeliveryAvailable} onVerified={() => {
            setEmailConfirmed(true); setError(null); router.refresh();
          }} />
            : <Button type="button" onClick={() => void accept()} disabled={pending} aria-busy={pending}>
              {pending ? "Принимаем приглашение…" : "Принять приглашение"} <ArrowRight aria-hidden="true" />
            </Button>}
        </div>
      </>}
    </section>
  </main>;
}
