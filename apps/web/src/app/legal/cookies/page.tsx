import { LegalPage, LegalSection } from "@/components/legal/LegalPage";

export default function CookiesPage() {
  return (
    <LegalPage title="Использование cookie">
      <LegalSection title="Обязательные cookie">
        <p>
          Сессионная cookie нужна для входа, защиты проектов и определения
          владельца действий. Она недоступна JavaScript, передаётся по HTTPS и
          удаляется при выходе или отзыве сессии.
        </p>
      </LegalSection>
      <LegalSection title="Настройки интерфейса">
        <p>
          Локальное хранилище браузера может сохранять несекретные настройки
          интерфейса и безопасную передачу стартового описания в созданный проект.
          Эти данные можно удалить средствами браузера.
        </p>
      </LegalSection>
      <LegalSection title="Аналитика">
        <p>
          Необязательная аналитика и рекламные cookie не включаются без отдельного
          основания и понятного выбора пользователя. Отказ от них не должен
          блокировать создание и редактирование приложения.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
