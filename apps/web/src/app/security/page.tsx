import { Database, EyeOff, KeyRound, LockKeyhole, Server, ShieldCheck } from "lucide-react";

import { InfoGrid, PublicPageShell } from "@/components/marketing/PublicPageShell";

export default function SecurityPage() {
  return (
    <PublicPageShell
      eyebrow="Безопасность"
      title="Данные, ключи и публикация защищены по умолчанию"
      lead="Безопасность встроена в пользовательский сценарий: от регистрации владельца до хранения секретов и постоянной production-версии."
    >
      <InfoGrid
        items={[
          { Icon: LockKeyhole, title: "HTTPS", text: "Рабочие и опубликованные версии открываются только через защищённое соединение." },
          { Icon: KeyRound, title: "Секреты интеграций", text: "Токены ботов и API-ключи хранятся зашифрованно и не добавляются в исходный код." },
          { Icon: Database, title: "Изоляция данных", text: "Проекты и бизнес-данные разделены по владельцам и недоступны другим аккаунтам." },
          { Icon: EyeOff, title: "Минимизация доступа", text: "Интерфейс повторно не показывает сохранённые секреты и не пишет их в логи." },
          { Icon: Server, title: "Контроль среды", text: "Контейнеры приложений запускаются отдельно, а состояние публикации контролируется сервером." },
          { Icon: ShieldCheck, title: "Удаление данных", text: "Владелец может запросить экспорт или удаление аккаунта и связанных данных из кабинета." },
        ]}
      />
    </PublicPageShell>
  );
}
