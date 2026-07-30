import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export const metadata = { title: `Условия использования — ${app.app_name}` };

export default function TermsPage() {
  return (
    <main style={{ maxWidth: 760, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
      <h1>Условия использования</h1>
      <p>
        Эти условия регулируют использование мини-приложения «{app.app_name}».
        Владелец сервиса: {app.operator.legal_name || app.app_name}.
      </p>
      <h2>Возможности сервиса</h2>
      <p>{app.summary}</p>
      <h2>Правила</h2>
      <p>
        Нельзя нарушать закон и права других лиц, получать чужие данные,
        вмешиваться в работу сервиса или использовать его для спама и обмана.
      </p>
      {app.legal.has_sales && (
        <>
          <h2>Заказы и оплата</h2>
          <p>
            Итоговая цена, состав заказа, способ оплаты, отмены и возврата
            показываются до подтверждения.
          </p>
        </>
      )}
      {app.legal.has_user_content && (
        <>
          <h2>Пользовательский контент</h2>
          <p>
            Запрещён незаконный и оскорбительный контент. О нарушении можно
            сообщить через поддержку.
          </p>
        </>
      )}
      <p>Возрастная маркировка: {app.legal.age_rating}.</p>
    </main>
  );
}
