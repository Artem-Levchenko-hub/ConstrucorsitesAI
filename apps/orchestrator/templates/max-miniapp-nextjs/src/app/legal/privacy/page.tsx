import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export const metadata = { title: `Политика конфиденциальности — ${app.app_name}` };

export default function PrivacyPage() {
  const operator = app.operator.legal_name || app.app_name;
  if (app.legal.policy_url) {
    return (
      <main style={{ maxWidth: 760, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
        <h1>Политика конфиденциальности</h1>
        <p>
          Обработка данных в мини-приложении «{app.app_name}» регулируется политикой владельца:{" "}
          <a href={app.legal.policy_url} rel="noopener noreferrer">{app.legal.policy_url}</a>.
        </p>
        <p>Возрастная маркировка: {app.legal.age_rating}.</p>
      </main>
    );
  }
  return (
    <main style={{ maxWidth: 760, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
      <h1>Политика конфиденциальности</h1>
      <p><strong>Оператор:</strong> {operator}</p>
      {app.support.email && <p><strong>Контакт по вопросам данных:</strong> {app.support.email}</p>}
      <h2>Какие данные обрабатываются</h2>
      <p>
        Приложение получает от MAX только идентификатор пользователя — он нужен,
        чтобы различать пользователей и хранить их действия, заказы, записи и обращения.
        Имя, фамилия, имя пользователя, язык и фотография из MAX не запрашиваются и не
        сохраняются. Другие сведения приложение запрашивает явно и только для конкретной функции.
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
