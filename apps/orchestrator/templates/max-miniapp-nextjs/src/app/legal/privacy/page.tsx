import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export const metadata = { title: `Политика конфиденциальности — ${app.app_name}` };

export default function PrivacyPage() {
  const operator = app.operator.legal_name || app.app_name;
  return (
    <main style={{ maxWidth: 760, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
      <h1>Политика конфиденциальности</h1>
      <p><strong>Оператор:</strong> {operator}</p>
      {app.operator.inn && <p><strong>ИНН:</strong> {app.operator.inn}</p>}
      {app.operator.address && <p><strong>Адрес:</strong> {app.operator.address}</p>}
      <h2>Какие данные обрабатываются</h2>
      <p>
        Приложение получает от MAX идентификатор и доступные данные профиля,
        необходимые для входа и работы функций приложения. Действия, заказы,
        записи и обращения сохраняются только в необходимом объёме.
      </p>
      <h2>Цели и срок обработки</h2>
      <p>
        Данные используются для исполнения запросов, поддержки, безопасности
        и улучшения сервиса и хранятся не дольше, чем требуют эти цели и закон.
      </p>
      <h2>Права пользователя</h2>
      <p>
        Пользователь может запросить сведения, исправление или удаление данных
        через страницу поддержки. Согласие на необязательные уведомления можно отозвать.
      </p>
      <p>Возрастная маркировка: {app.legal.age_rating}.</p>
    </main>
  );
}
