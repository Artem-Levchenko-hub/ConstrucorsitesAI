import { Bot, Building2, Code2, ShieldCheck, Users, Waypoints } from "lucide-react";

import { InfoGrid, PublicPageShell } from "@/components/marketing/PublicPageShell";

export default function AboutPage() {
  return (
    <PublicPageShell
      eyebrow="О продукте"
      title="Продуктовая студия, которая доводит приложение до запуска"
      lead="MaxStudio объединяет разработку, инфраструктуру, интеграции и публикацию в один управляемый сценарий для бизнеса."
    >
      <InfoGrid
        items={[
          { Icon: Waypoints, title: "Разработка через диалог", text: "Владелец описывает бизнес-задачу, а агент проектирует и реализует рабочий продукт." },
          { Icon: Code2, title: "Настоящий код", text: "Проект состоит из полноценного frontend, backend и базы данных с историей версий." },
          { Icon: Bot, title: "Специализация на MAX", text: "MAX Bridge, бот, webhook и мобильные ограничения учитываются с первой сборки." },
          { Icon: ShieldCheck, title: "Защищённая эксплуатация", text: "Секреты интеграций шифруются, а опубликованная версия работает по HTTPS." },
          { Icon: Building2, title: "Для бизнеса любого типа", text: "Поддерживаются ООО, ИП и самозанятые владельцы MAX-ботов." },
          { Icon: Users, title: "Без команды разработчиков", text: "После запуска продукт можно изменять в той же студии обычными сообщениями." },
        ]}
      />
    </PublicPageShell>
  );
}
