import Link from "next/link";

import { AuthCard } from "@/components/auth/AuthCard";
import { OAuthConsentForm } from "@/components/auth/OAuthConsentForm";
import { Button } from "@/components/ui/button";
import { getOAuthPending } from "@/lib/oauth-login-server";

/**
 * Сюда api отправляет браузер после входа через VK ID / Яндекс ID, когда
 * аккаунта с таким email ещё нет. Билет одноразовый и живёт 15 минут; email
 * берём с api по билету, а не из адресной строки.
 */
export default async function OAuthCompletePage({
  searchParams,
}: {
  searchParams: Promise<{ ticket?: string }>;
}) {
  const { ticket } = await searchParams;
  const pending = ticket ? await getOAuthPending(ticket) : null;

  if (!pending || !ticket) {
    return (
      <AuthCard
        title="Ссылка устарела"
        subtitle="Подтверждение входа действует 15 минут и срабатывает один раз. Нажмите кнопку провайдера ещё раз."
        footer={
          <>
            Или{" "}
            <Link href="/max/register" className="text-accent hover:text-accent-hover transition">
              зарегистрируйтесь по email
            </Link>
          </>
        }
      >
        <Button asChild variant="primary" size="lg" className="w-full rounded-[8px]">
          <Link href="/login">Вернуться ко входу</Link>
        </Button>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Почти готово"
      subtitle={`Создаём аккаунт по ${pending.label}. Осталось принять документы — как при обычной регистрации.`}
      footer={
        <>
          Передумали?{" "}
          <Link href="/login" className="text-accent hover:text-accent-hover transition">
            Войти по email
          </Link>
        </>
      }
    >
      <OAuthConsentForm
        ticket={ticket}
        email={pending.email}
        providerLabel={pending.label}
        next={pending.next}
        documentVersion={pending.legal_document_version}
      />
    </AuthCard>
  );
}
