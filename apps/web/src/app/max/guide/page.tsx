import type { Metadata } from "next";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookOpenText,
  Bot,
  Check,
  CheckCircle2,
  CircleHelp,
  CreditCard,
  ExternalLink,
  KeyRound,
  LifeBuoy,
  Link2,
  LockKeyhole,
  MailCheck,
  MessageSquareText,
  RefreshCw,
  Rocket,
  ShieldCheck,
  Smartphone,
  Sparkles,
  UserRoundCheck,
  Users,
  Webhook,
} from "lucide-react";
import type { ReactNode } from "react";

import { BrandMark } from "@/components/marketing/BrandMark";
import {
  BuilderVisual,
  DashboardVisual,
  GoldenPathVisual,
  IntegrationVisual,
  LaunchVisual,
  PartnerVisual,
  ProjectCreationVisual,
} from "@/components/max/guide/GuideVisuals";

export const metadata: Metadata = {
  title: "Полное руководство MAX Studio — Omnia",
  description:
    "Пошаговые сценарии MAX Studio: аккаунт, проект, сборка, интеграции, MAX-бот, публикация, запуск и сопровождение.",
};

const chapters = [
  ["start", "Перед началом", "00"],
  ["account", "Аккаунт и проверка", "01"],
  ["project", "Создание проекта", "02"],
  ["builder", "Работа с агентом", "03"],
  ["settings", "Данные приложения", "04"],
  ["integrations", "Интеграции", "05"],
  ["max-bot", "MAX-бот", "06"],
  ["publish", "Публикация", "07"],
  ["partner", "URL в MAX Partner", "08"],
  ["acceptance", "Приёмка в MAX", "09"],
  ["operations", "После запуска", "10"],
  ["billing", "Подписка и биллинг", "11"],
  ["errors", "Ошибки и решения", "12"],
  ["checklists", "Контрольные списки", "13"],
] as const;

function Kicker({ children }: { children: ReactNode }) {
  return <p className="omnia-kicker text-[#4f81f7]">{children}</p>;
}

function GuideSection({
  id,
  number,
  eyebrow,
  title,
  lead,
  children,
}: {
  id: string;
  number: string;
  eyebrow: string;
  title: string;
  lead: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-24 border-t border-[#2b2d32] py-16 sm:py-20 lg:py-24">
      <div className="grid gap-6 lg:grid-cols-[120px_minmax(0,1fr)]">
        <p className="font-mono text-[12px] text-[#828491]">{number}</p>
        <div className="min-w-0">
          <Kicker>{eyebrow}</Kicker>
          <h2 className="mt-3 max-w-[850px] text-[34px] font-semibold leading-[1.04] tracking-[-.045em] sm:text-[48px]">{title}</h2>
          <p className="mt-5 max-w-[780px] text-[15px] leading-7 text-[#9fa1b1]">{lead}</p>
          <div className="mt-10">{children}</div>
        </div>
      </div>
    </section>
  );
}

function Route({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-md border border-[#2b2d32] bg-[#191b20] px-2 py-1 font-mono text-[11px] text-[#9fa1b1]">
      {children}
    </span>
  );
}

function StepList({
  steps,
}: {
  steps: Array<{ title: string; text: ReactNode; result?: string }>;
}) {
  return (
    <ol className="divide-y divide-[#25272b] overflow-hidden rounded-[12px] border border-[#2b2d32] bg-[#191b20]">
      {steps.map((step, index) => (
        <li key={step.title} className="grid gap-4 p-5 sm:grid-cols-[44px_minmax(0,1fr)] sm:p-6">
          <span className="grid size-8 place-items-center rounded-full border border-[#4f81f7]/40 bg-[#4f81f7]/[.06] font-mono text-[10px] font-semibold text-[#6a95fa]">
            {String(index + 1).padStart(2, "0")}
          </span>
          <div>
            <h3 className="text-sm font-semibold">{step.title}</h3>
            <div className="mt-2 text-sm leading-6 text-[#9fa1b1]">{step.text}</div>
            {step.result && (
              <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-success-fg">
                <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" />
                <span><strong>Результат:</strong> {step.result}</span>
              </p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Note({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "warning" | "success";
  title: string;
  children: ReactNode;
}) {
  const styles = {
    info: "border-[#2b2d32] bg-[#191b20] text-white",
    warning: "border-[#e8c547]/50 bg-[#e8c547]/10 text-[#e8c547]",
    success: "border-[#248a4b]/35 bg-[#248a4b]/[.06] text-success-fg",
  }[tone];
  const Icon = tone === "warning" ? AlertTriangle : tone === "success" ? CheckCircle2 : CircleHelp;
  return (
    <aside className={`rounded-[12px] border p-5 ${styles}`}>
      <div className="flex gap-3">
        <Icon className="mt-0.5 size-5 shrink-0" />
        <div><p className="text-sm font-semibold">{title}</p><div className="mt-2 text-sm leading-6 opacity-80">{children}</div></div>
      </div>
    </aside>
  );
}

function NumberedLegend({ items }: { items: string[] }) {
  return (
    <div className="mt-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {items.map((item, index) => (
        <div key={item} className="flex gap-3 rounded-[10px] border border-[#2b2d32] bg-[#191b20] p-4 text-xs leading-5 text-[#9fa1b1]">
          <span className="grid size-6 shrink-0 place-items-center rounded-full bg-[#4f81f7] text-[10px] font-semibold text-[#121519]">{index + 1}</span>
          {item}
        </div>
      ))}
    </div>
  );
}

function StatusTable({ rows }: { rows: Array<[string, string, string]> }) {
  return (
    <div className="overflow-x-auto rounded-[12px] border border-[#2b2d32] bg-[#191b20]">
      <table className="w-full min-w-[680px] border-collapse text-left text-sm">
        <thead className="bg-[#2b2d32] text-[10px] uppercase tracking-[.12em] text-[#9fa1b1]">
          <tr><th className="p-4 font-medium">Статус</th><th className="p-4 font-medium">Что означает</th><th className="p-4 font-medium">Что делать</th></tr>
        </thead>
        <tbody className="divide-y divide-[#25272b]">
          {rows.map(([status, meaning, action]) => (
            <tr key={status} className="align-top"><td className="p-4 font-medium text-white">{status}</td><td className="p-4 leading-6 text-[#9fa1b1]">{meaning}</td><td className="p-4 leading-6 text-[#9fa1b1]">{action}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Checklist({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-5 sm:p-6">
      <h3 className="text-base font-semibold">{title}</h3>
      <ul className="mt-5 space-y-3">
        {items.map((item) => (
          <li key={item} className="flex gap-3 text-sm leading-6 text-[#9fa1b1]"><span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded border border-[#2b2d32] bg-[#191b20]"><Check className="size-3 text-success-fg" /></span>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export default function MaxGuidePage() {
  return (
    <main data-product-shell className="min-h-screen bg-[#121519] text-white">
      <header className="sticky top-0 z-50 border-b border-[#2b2d32] bg-[#191b20]/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-[1440px] items-center justify-between gap-4 px-4 sm:px-7">
          <div className="flex min-w-0 items-center gap-3"><BrandMark href="/" /><span className="hidden h-5 w-px bg-[#2b2d32] sm:block" /><span className="hidden truncate text-xs font-medium text-[#9fa1b1] sm:block">Руководство MAX Studio</span></div>
          <div className="flex items-center gap-2">
            <Link href="/max/product" className="hidden min-h-10 items-center rounded-md px-3 text-xs text-[#9fa1b1] hover:bg-[#121519] sm:inline-flex"><ArrowLeft className="mr-2 size-3.5" />О продукте</Link>
            <Link href="/login?next=/max" className="omnia-button omnia-button-secondary min-h-10 px-4 text-xs">Войти</Link>
            <Link href="/max/register" className="omnia-button omnia-button-primary min-h-10 px-4 text-xs">Начать</Link>
          </div>
        </div>
      </header>

      <section data-graphite-shell className="bg-[#121519] px-4 py-16 sm:px-7 sm:py-24 lg:py-28">
        <div className="mx-auto grid max-w-[1320px] gap-12 lg:grid-cols-[1fr_340px] lg:items-end">
          <div>
            <p className="omnia-kicker text-[#4f81f7]">Omnia / MAX Studio / Docs</p>
            <h1 className="mt-6 max-w-[980px] text-[46px] font-semibold leading-[.96] tracking-[-.055em] sm:text-[68px] lg:text-[84px]">От идеи до запуска в MAX — без пропущенных шагов</h1>
            <p className="mt-7 max-w-[760px] text-base leading-7 text-white/55 sm:text-lg">Полное руководство для владельца бизнеса: что нажимать, какие данные подготовить, как проверить сборку, подключить бота, опубликовать приложение и принять его двумя реальными пользователями.</p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/max/start" className="omnia-button omnia-button-primary min-h-11 px-5">Короткий путь: 6 этапов <ArrowRight className="size-4" /></Link>
              <a href="#errors" className="omnia-button min-h-11 border border-white/20 px-5 text-white hover:bg-white/5">У меня ошибка <LifeBuoy className="size-4" /></a>
            </div>
          </div>
          <div className="rounded-[14px] border border-white/14 bg-white/[.04] p-6">
            <div className="flex items-center gap-3"><BookOpenText className="size-5 text-[#4f81f7]" /><p className="text-sm font-semibold">Как читать руководство</p></div>
            <ul className="mt-5 space-y-3 text-xs leading-5 text-white/48">
              <li>① Идите по главам 00–10 для первого запуска.</li>
              <li>② Цифры на снимках показывают точные места клика.</li>
              <li>③ Блок «Результат» — критерий завершения шага.</li>
              <li>④ Не передавайте токены и пароли в чат или код проекта.</li>
            </ul>
            <p className="mt-5 border-t border-white/10 pt-4 font-mono text-[9px] uppercase tracking-[.15em] text-white/28">Обновлено 31 июля 2026</p>
          </div>
        </div>
      </section>

      <div className="mx-auto grid max-w-[1440px] gap-10 px-4 sm:px-7 lg:grid-cols-[240px_minmax(0,1fr)] lg:gap-14">
        <aside className="hidden lg:block">
          <nav aria-label="Оглавление" className="sticky top-24 py-10">
            <p className="omnia-kicker px-3 text-[#828491]">Оглавление</p>
            <div className="mt-4 max-h-[calc(100dvh-150px)] space-y-0.5 overflow-y-auto pr-2">
              {chapters.map(([id, label, number]) => (
                <a key={id} href={`#${id}`} className="flex items-center gap-3 rounded-md px-3 py-2 text-[11px] text-[#9fa1b1] hover:bg-[#191b20] hover:text-white"><span className="font-mono text-[8px] text-[#828491]">{number}</span>{label}</a>
              ))}
            </div>
          </nav>
        </aside>

        <div className="min-w-0">
          <GuideSection id="start" number="00" eyebrow="Перед началом" title="Что подготовить до первого клика" lead="MAX Studio выполняет сборку, backend, безопасное превью, публикацию и webhook. Внешние кабинеты остаются под вашим контролем: почта, платёжный магазин и MAX для партнёров.">
            <GoldenPathVisual />
            <div className="mt-8 grid gap-4 md:grid-cols-2">
              {[
                [MailCheck, "Рабочий email", "На него придёт одноразовая ссылка подтверждения и уведомления безопасности."],
                [UserRoundCheck, "Реквизиты владельца", "ФИО/название, ИНН и ОГРН/ОГРНИП должны совпадать с профилем MAX."],
                [Bot, "Профиль MAX для партнёров", "Верифицированный профиль и промодерированный чат-бот. Токен появится после модерации."],
                [CreditCard, "Платёжные реквизиты", "Для платного запуска: активный магазин ЮKassa и утверждённая схема чеков."],
              ].map(([Icon, title, text]) => {
                const ItemIcon = Icon as typeof MailCheck;
                return <article key={String(title)} className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-5"><ItemIcon className="size-5 text-[#4f81f7]" /><h3 className="mt-6 text-sm font-semibold">{String(title)}</h3><p className="mt-2 text-sm leading-6 text-[#9fa1b1]">{String(text)}</p></article>;
              })}
            </div>
            <Note tone="warning" title="Не начинайте с токена и URL">Сначала создайте и проверьте приложение. Токен подключается до публикации, а production URL появляется только после успешного deploy. URL из безопасного превью в MAX Partner вставлять нельзя.</Note>
          </GuideSection>

          <GuideSection id="account" number="01" eyebrow="Вход в Studio" title="Регистрация, email и проверка владельца" lead="Аккаунт MAX Studio привязывается к реальному владельцу бизнеса. Пока email и реквизиты не подтверждены, создание и оплата production-проекта ограничены.">
            <StepList steps={[
              { title: "Откройте регистрацию", text: <>Перейдите на <Route>/max/register</Route>. Укажите рабочий email, придумайте пароль не короче 10 символов, примите условия и нажмите <strong>«Создать аккаунт»</strong>.</>, result: "Аккаунт создан, на экране показан адрес для подтверждения." },
              { title: "Подтвердите email", text: <>Откройте письмо «Подтвердите email для MAX Studio» и нажмите ссылку. Ссылка одноразовая и имеет срок действия. Если письмо не пришло, проверьте «Спам» и нажмите <strong>«Отправить ещё раз»</strong> только один раз.</>, result: "В MAX Studio появился экран выбора владельца бизнеса." },
              { title: "Выберите тип владельца", text: <>На <Route>/max/onboarding</Route> выберите <strong>Самозанятый</strong>, <strong>ИП</strong> или <strong>Организация</strong>. Тип должен совпадать с владельцем будущего MAX-бота.</>, result: "Форма показывает правильный набор реквизитов." },
              { title: "Введите реквизиты без сокращений", text: <>Укажите юридическое имя и ИНН. Для ИП добавьте ОГРНИП, для организации — ОГРН. Нажмите <strong>«Проверить реквизиты»</strong>. Не используйте псевдоним бренда вместо юридического имени.</>, result: "Статус профиля — «Проверен» или «Ожидает проверки»." },
              { title: "Войдите в рабочее пространство", text: <>После подтверждения откройте <Route>/max</Route>. Если вас вернуло на вход, авторизуйтесь тем же email и паролем.</>, result: "Открыта страница «Мои приложения»." },
            ]} />
            <div className="mt-6 grid gap-4 md:grid-cols-2"><Note title="Письмо не пришло">Проверьте правильность адреса, папки «Спам» и «Промоакции». Повторная отправка ограничена, поэтому не нажимайте кнопку много раз подряд.</Note><Note tone="warning" title="Тип владельца выбран неверно">Не продолжайте к MAX-боту. Исправьте профиль через поддержку: ИНН и тип владельца должны совпасть с MAX Partner.</Note></div>
          </GuideSection>

          <GuideSection id="project" number="02" eyebrow="Первый проект" title="Создание MAX Mini App из короткого описания" lead="На этом шаге вы задаёте продуктовую рамку. Чем точнее главное действие и аудитория, тем меньше уточнений потребуется агенту во время первой сборки.">
            <ProjectCreationVisual />
            <NumberedLegend items={["Нажмите «Новый проект» в правом верхнем углу или на пустой карточке.", "Заполните название, главное действие и выберите ближайший тип приложения.", "Проверьте данные и нажмите «Создать проект». Первая генерация стартует после открытия редактора."]} />
            <div className="mt-8 grid gap-6 lg:grid-cols-2">
              <Checklist title="Хорошее описание" items={["Одна понятная аудитория: «постоянные гости кофейни».", "Одно главное действие: «оформить заказ к выдаче».", "3–7 функций первого релиза, без списка на год вперёд.", "Конкретные брендовые цвета или разрешение подобрать стиль.", "Что должно храниться: заказы, баллы, записи, профиль."]} />
              <Checklist title="Чего избегать" items={["«Сделай приложение как у всех» без сценария.", "Несколько несвязанных бизнесов в одном Mini App.", "Требование Telegram/VK внутри проекта MAX.", "Секретные ключи, пароли и паспортные данные в описании.", "Сразу 30 экранов и интеграции, которых нет в MVP."]} />
            </div>
            <Note tone="success" title="Пример брифа">«Приложение лояльности для гостей кофейни. Пользователь видит баланс баллов, выбирает награду, оформляет заказ к выдаче и смотрит историю. Главное действие — заказать кофе. Стиль — молочный фон, графит и оранжевый акцент».</Note>
          </GuideSection>

          <GuideSection id="builder" number="03" eyebrow="Работа с агентом" title="Как получить рабочую сборку и не потерять контекст" lead="Редактор состоит из трёх зон: диалог с агентом, живое мобильное превью и мастер готовности к запуску. Все изменения сохраняются на сервере и попадают в историю проекта.">
            <BuilderVisual />
            <NumberedLegend items={["Введите одно изменение в нижнее поле и отправьте. Для новой функции опишите ожидаемый результат и граничные случаи.", "Проверьте живое превью: навигацию, пустые состояния, ошибки формы, повторное открытие и мобильную клавиатуру.", "К публикации переходите только после чистой проверки. Кнопка откроет мастер готовности и production deploy."]} />
            <div className="mt-8 grid gap-5 md:grid-cols-3">
              {[
                [MessageSquareText, "Одна задача за сообщение", "Так проще проверить результат и при необходимости откатить только одну правку."],
                [Smartphone, "Проверяйте как пользователь", "Нажимайте кнопки, создавайте тестовые данные, обновляйте страницу и повторяйте сценарий."],
                [RefreshCw, "Не перезапускайте генерацию", "При зависшем статусе сначала обновите страницу: серверная задача продолжает работать в фоне."],
              ].map(([Icon, title, text]) => { const ItemIcon = Icon as typeof MessageSquareText; return <article key={String(title)} className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-5"><ItemIcon className="size-5 text-[#4f81f7]" /><h3 className="mt-6 text-sm font-semibold">{String(title)}</h3><p className="mt-2 text-sm leading-6 text-[#9fa1b1]">{String(text)}</p></article>; })}
            </div>
            <h3 className="mt-10 text-lg font-semibold">Шаблоны сообщений агенту</h3>
            <div className="mt-4 grid gap-3">
              {[
                ["Новая функция", "Добавь запись на услугу: выбор даты, свободного времени, подтверждение и экран «Мои записи». Не допускай двойного бронирования."],
                ["Исправление", "На экране заказа кнопка перекрывается клавиатурой. Сохрани текущий дизайн и исправь только mobile layout и safe area."],
                ["Проверка данных", "Проверь, что пользователь MAX видит только свои заказы. Добавь пустое состояние и понятную ошибку при недоступном сервере."],
                ["Контент", "Замени тексты каталога на приложенный список. Не меняй backend, навигацию и визуальный стиль."],
              ].map(([label, prompt]) => <div key={label} className="rounded-[10px] border border-[#2b2d32] bg-[#191b20] p-4"><p className="font-mono text-[9px] uppercase tracking-[.14em] text-[#4f81f7]">{label}</p><p className="mt-2 text-sm leading-6 text-[#9fa1b1]">{prompt}</p></div>)}
            </div>
          </GuideSection>

          <GuideSection id="settings" number="04" eyebrow="Управляемая конфигурация" title="Данные приложения, каталог и юридические экраны" lead="Часть информации меняется без новой генерации и без расхода баланса. Это название, описание, контент, поддержка, данные оператора, юридические признаки и подтверждение URL в MAX.">
            <StepList steps={[
              { title: "Откройте настройки приложения", text: <>В редакторе справа откройте панель <strong>«Публикация»</strong> и нажмите <strong>«Приложение»</strong>. На отдельных страницах используйте меню <strong>«MAX и приложение»</strong>.</>, result: "Открыто окно «Готовое приложение без разработчика»." },
              { title: "Заполните продукт", text: <>Проверьте название, главное действие, описание, аудиторию, тип приложения, стиль и функции. Эти поля объясняют пользователю, что он может сделать.</>, result: "У приложения нет временных текстов и заглушек." },
              { title: "Добавьте каталог или услуги", text: <>В блоке «Каталог и контент» нажмите <strong>«Добавить»</strong>. Для каждого элемента задайте название, описание, цену, подпись кнопки и активность.</>, result: "Все позиции имеют понятную цену и действие." },
              { title: "Заполните оператора и поддержку", text: <>Юридическое имя, ИНН, ОГРН/ОГРНИП и адрес должны совпадать с офертой и платёжным кабинетом. Добавьте рабочий email и телефон поддержки.</>, result: "Пользователь может определить продавца и связаться с ним." },
              { title: "Отметьте юридические признаки", text: <>Укажите наличие продаж, пользовательского контента и маркетинговых уведомлений. Примите условия только после проверки сгенерированных документов.</>, result: "Readiness отмечает юридические данные как готовые." },
              { title: "Сохраните", text: <>Нажмите <strong>«Сохранить»</strong>. Studio создаст новую управляемую версию без вызова модели.</>, result: "Появилось уведомление «Настройки применены без генерации»." },
            ]} />
            <Note tone="warning" title="Не отмечайте URL заранее">Флажок «URL добавлен в MAX» подтверждает реальное действие во внешнем кабинете. Ставьте его только после сохранения production URL в MAX Partner и проверки кнопки «Открыть».</Note>
          </GuideSection>

          <GuideSection id="integrations" number="05" eyebrow="Внешние сервисы" title="Подключение интеграций без передачи секретов агенту" lead="Интеграции подключаются на уровне бизнеса или проекта. Секреты шифруются и подставляются сервером; агент и код Mini App их не видят.">
            <IntegrationVisual />
            <NumberedLegend items={["Выберите MAX Bot API или другой сервис и нажмите «Подключить».", "Для MAX вставьте токен в защищённое поле, затем нажмите «Проверить и сохранить»."]} />
            <StepList steps={[
              { title: "Выберите сервис", text: <>Откройте <Route>/max/ID/integrations</Route>. Карточка показывает возможности, способ авторизации и текущий статус.</> },
              { title: "Используйте OAuth, если доступен", text: <>Нажмите «Авторизовать» и подтвердите доступ в кабинете провайдера. Не копируйте пароль от самого кабинета.</>, result: "Статус подключения — «Активно»." },
              { title: "Для ключей используйте только защищённую форму", text: <>Вставляйте API-ключ или токен в поле интеграции. Никогда не отправляйте секрет в чат агенту и не добавляйте его в файлы проекта.</>, result: "Ключ проверен у провайдера и зашифрован." },
              { title: "Привяжите интеграцию к проекту", text: <>Если сервис подключён на уровне бизнеса, выберите нужное соединение в проекте. Один бизнес-доступ можно безопасно использовать в нескольких Mini Apps.</>, result: "Карточка проекта показывает выбранное соединение." },
              { title: "Проверьте реальное действие", text: <>Создайте тестовую запись, лид, заказ или платёж в Mini App и убедитесь, что объект появился у провайдера.</>, result: "Интеграция подтверждена не только кнопкой, но и фактическим событием." },
            ]} />
          </GuideSection>

          <GuideSection id="max-bot" number="06" eyebrow="Точка входа" title="Создание, модерация и подключение MAX-бота" lead="Бот является владельцем кнопки запуска Mini App и источником событий Bot API. Создать его может только верифицированный профиль организации, ИП или самозанятого в MAX для партнёров.">
            <div className="grid gap-5 lg:grid-cols-2">
              <Checklist title="В MAX для партнёров" items={["Профиль организации/ИП/самозанятого верифицирован.", "Создан чат-бот с понятным именем, описанием и аватаром.", "Бот прошёл модерацию и имеет статус «создан».", "В расширенных настройках появился токен.", "Владелец профиля совпадает с владельцем MAX Studio."]} />
              <Checklist title="В Omnia" items={["Проект создан как MAX Mini App.", "Открыта интеграция MAX Bot API.", "Токен вставлен только в защищённое поле.", "Проверка вернула имя и username нужного бота.", "После публикации webhook имеет статус «активен»."]} />
            </div>
            <StepList steps={[
              { title: "Создайте бота", text: <>Откройте <a className="font-medium text-[#6a95fa] underline" href="https://business.max.ru/" target="_blank" rel="noreferrer">MAX для партнёров</a>, выберите профиль, затем <strong>«Чат-боты» → «Создать»</strong>. Заполните карточку и отправьте на модерацию.</>, result: "Статус бота — «на модерации»." },
              { title: "Дождитесь статуса «создан»", text: <>Пока бот модерируется, его настройки и токен могут быть недоступны. При статусе «требует исправлений» откройте причину, исправьте карточку и отправьте повторно.</>, result: "Бот находится поиском в настоящем клиенте MAX." },
              { title: "Скопируйте токен", text: <><strong>«Чат-боты» → «Перейти» → «Расширенные настройки» → «Настроить»</strong>. Нажмите копирование рядом с токеном.</>, result: "Токен доступен в буфере обмена, но не сохранён в заметках или чате." },
              { title: "Подключите в Studio", text: <>В проекте откройте <strong>«Интеграции» → «MAX Bot API» → «Подключить»</strong>. Вставьте токен и нажмите <strong>«Проверить и сохранить»</strong>.</>, result: "Studio показывает правильное имя и username бота." },
              { title: "Не меняйте токен без причины", text: <>Обновление токена в MAX немедленно делает старый недействительным. Если ротация нужна, сначала выпустите новый токен, затем сразу обновите интеграцию в Studio и проверьте webhook.</>, result: "Интеграция снова имеет статус «активно»." },
            ]} />
            <Note title="Срок модерации и первоисточник">MAX указывает ориентир до 48 рабочих часов. Актуальные статусы, требования к карточке и порядок получения токена сверяйте в <a className="font-medium text-[#6a95fa] underline" href="https://dev.max.ru/docs/chatbots/bots-create/create" target="_blank" rel="noreferrer">официальной инструкции по созданию и модерации бота</a>.</Note>
            <Note tone="warning" title="Токен равен паролю бота">Не публикуйте его в сообщениях, документах, GitHub, скриншотах и исходном коде. Если токен попал наружу — обновите его в MAX и переподключите бота в Studio.</Note>
          </GuideSection>

          <GuideSection id="publish" number="07" eyebrow="Production" title="Публикация на постоянный HTTPS-адрес" lead="Публикация собирает неизменяемую версию, разворачивает контейнер, проверяет health endpoint и только затем переключает production. Неудачная версия не должна заменять работающую.">
            <LaunchVisual />
            <NumberedLegend items={["Нажмите «Опубликовать», когда сборка и данные приложения проверены.", "Следуйте текущему шагу мастера. Он показывает первую незавершённую причину, а не общий список ошибок."]} />
            <StepList steps={[
              { title: "Откройте мастер", text: <>В редакторе нажмите <strong>«Опубликовать»</strong> или перейдите на <Route>/max/ID/publish</Route>.</>, result: "Видны прогресс и текущий незавершённый шаг." },
              { title: "Закройте readiness", text: <>Последовательно заполните приложение, юридические данные и подключите MAX-бота. Не обходите пункты вручную — каждый статус проверяется сервером.</>, result: "Следующий шаг мастера — «Опубликуйте приложение»." },
              { title: "Запустите deploy один раз", text: <>Нажмите <strong>«Опубликовать»</strong>. Во время фаз building, pushing и swapping не нажимайте кнопку повторно и не закрывайте проект принудительно.</>, result: "Фаза публикации — done, отображается production URL." },
              { title: "Проверьте production", text: <>Откройте полученный URL отдельно. Проверьте первый экран, основные API-действия и обновление страницы.</>, result: "HTTPS открывается без предупреждений, приложение отвечает после reload." },
              { title: "Активируйте webhook", text: <>После успешного deploy Studio формирует <code>/api/max/webhook</code>, подписывает бота через MAX API и проверяет подписку.</>, result: "Интеграция MAX показывает «Webhook активен»." },
            ]} />
            <StatusTable rows={[
              ["building", "Собирается production-образ.", "Ждать. Не запускайте второй deploy."],
              ["pushing", "Образ передаётся в production runtime.", "Не закрывать публикацию принудительно."],
              ["swapping", "Новая версия проходит health-check и готовится принять трафик.", "Проверить детали, если фаза длится необычно долго."],
              ["done", "Production успешно переключён.", "Открыть URL, затем перейти к MAX Partner."],
              ["failed", "Сборка или health-check не прошли.", "Открыть детали, исправить первопричину в редакторе и повторить."],
            ]} />
          </GuideSection>

          <GuideSection id="partner" number="08" eyebrow="Ручной внешний шаг" title="Как вставить production URL в MAX Partner" lead="MAX пока требует сохранить ссылку мини-приложения в кабинете владельца бота. Studio копирует точный URL и ведёт к нужному кабинету; вводить адрес вручную по памяти не нужно.">
            <PartnerVisual />
            <NumberedLegend items={["Скопируйте токен только при первичном подключении бота. После этого он остаётся в Studio зашифрованным.", "Вставьте именно production URL из Studio в поле «Ссылка на мини-приложение» и выберите кнопку «Открыть».", "Нажмите «Сохранить», дождитесь подтверждения и только потом отметьте URL в мастере Studio."]} />
            <StepList steps={[
              { title: "Скопируйте URL в Studio", text: <>В панели запуска, когда webhook активен, нажмите ссылку production или <strong>«Открыть кабинет MAX»</strong>. Studio предварительно копирует адрес в буфер.</>, result: "В буфере адрес вида https://… без /preview и временного токена." },
              { title: "Откройте настройки бота", text: <>В MAX Partner выберите правильную организацию и бота: <strong>«Чат-боты» → «Перейти» → «Расширенные настройки» → «Настроить»</strong>.</> },
              { title: "Вставьте ссылку", text: <>В поле мини-приложения вставьте URL. Не добавляйте <code>/api/max/webhook</code>, параметры preview или ссылку на редактор.</>, result: "Поле содержит корень production Mini App." },
              { title: "Выберите кнопку", text: <>Выберите подпись <strong>«Открыть»</strong>. Это самая понятная точка входа для универсального бизнес-приложения.</> },
              { title: "Сохраните и дождитесь статуса", text: <>Нажмите <strong>«Сохранить»</strong>. Если MAX отправил изменения на повторную модерацию, дождитесь возвращения рабочего статуса.</>, result: "В настоящем чате бота появилась кнопка запуска." },
              { title: "Подтвердите в Studio", text: <>Вернитесь в настройки приложения и включите <strong>«URL добавлен в MAX»</strong>.</>, result: "Readiness равен 100%, приложение готово к приёмке." },
            ]} />
            <Note title="Официальная схема MAX">Путь к полю ссылки, варианты кнопки и формат внутреннего запуска описаны в <a className="font-medium text-[#6a95fa] underline" href="https://dev.max.ru/help/miniapps" target="_blank" rel="noreferrer">официальной справке по мини-приложениям</a>.</Note>
            <Note tone="warning" title="Как отличить production URL">Безопасное превью открывается только внутри защищённой preview-сессии и не подходит для MAX. Production URL постоянный, начинается с HTTPS, открывается отдельно и отображается после deploy со статусом done.</Note>
          </GuideSection>

          <GuideSection id="acceptance" number="09" eyebrow="Настоящий клиент MAX" title="Приёмка двумя разными пользователями" lead="Браузерное превью проверяет интерфейс, но не доказывает интеграцию с MAX. Финальная приёмка проводится в мобильном клиенте через кнопку настоящего промодерированного бота.">
            <div className="grid gap-4 md:grid-cols-2">
              <Checklist title="Пользователь A — новый" items={["Открывает бота по ссылке или через поиск MAX.", "Нажимает «Открыть» и видит Mini App внутри MAX.", "Создаёт тестовую запись/заказ/действие.", "Закрывает шторку и открывает повторно.", "Видит только собственные данные и сохранённый результат."]} />
              <Checklist title="Пользователь B — изоляция" items={["Использует другой реальный MAX-аккаунт и устройство.", "Первый экран не содержит данных пользователя A.", "Создаёт свой отдельный объект.", "После повторного запуска видит только свой объект.", "Получает сервисное сообщение, если оно входит в сценарий."]} />
            </div>
            <div className="mt-6 rounded-[12px] border border-[#2b2d32] bg-[#121519] p-6 text-white">
              <p className="omnia-kicker text-[#4f81f7]">Протокол приёмки</p>
              <div className="mt-6 grid gap-px overflow-hidden rounded-[10px] border border-white/12 bg-white/12 sm:grid-cols-3">
                {[
                  [Users, "2 разных MAX ID", "Сервер получил две независимые личности из валидированного initData."],
                  [ShieldCheck, "Изоляция данных", "Каждый select/update/delete ограничен владельцем объекта."],
                  [Webhook, "Webhook 200", "События MAX приняты, секрет проверен, повторы не создают дубликаты."],
                ].map(([Icon, title, copy]) => { const ItemIcon = Icon as typeof Users; return <div key={String(title)} className="bg-[#121519] p-5"><ItemIcon className="size-5 text-[#4f81f7]" /><p className="mt-7 text-sm font-semibold">{String(title)}</p><p className="mt-2 text-xs leading-5 text-white/45">{String(copy)}</p></div>; })}
              </div>
            </div>
            <StepList steps={[
              { title: "Назначьте окно проверки", text: <>Оба пользователя должны быть доступны одновременно 15–20 минут. Используйте тестовые имена и данные, не реальные ПДн клиентов.</> },
              { title: "Запишите начальное состояние", text: <>Production имеет done, health зелёный, webhook активен, URL подтверждён. Не вносите изменения во время прогона.</> },
              { title: "Пройдите один и тот же сценарий", text: <>A проходит путь полностью, затем B повторяет его. Фиксируйте время каждого действия — так события легко найти в журнале.</> },
              { title: "Проверьте повторный вход", text: <>Оба закрывают и повторно открывают Mini App. Сессия восстанавливается через новый валидный initData, данные не смешиваются.</> },
              { title: "Зафиксируйте результат", text: <>Сохраните версию, production URL, username бота и итог каждого пункта. Не сохраняйте токен, initData или персональные идентификаторы в публичном отчёте.</>, result: "Два пользователя завершили путь, изоляция и webhook подтверждены." },
            ]} />
          </GuideSection>

          <GuideSection id="operations" number="10" eyebrow="Эксплуатация" title="Что делать после запуска и как выпускать изменения" lead="После запуска проект остаётся управляемым: видны runtime, health, бот, webhook и история публикаций. Новое изменение проходит тот же цикл — правка, превью, новая версия, deploy и проверка.">
            <DashboardVisual />
            <NumberedLegend items={["Кнопка «Открыть приложение» ведёт на текущий production и используется для быстрой проверки.", "История публикаций показывает версии и результат deploy. При проблеме сравните последнюю успешную и текущую."]} />
            <div className="mt-8 grid gap-4 md:grid-cols-2">
              <Checklist title="Регулярная проверка" items={["Контейнер — running.", "Health-check — отвечает.", "MAX-бот — подключён.", "Webhook — активен.", "Ключевые API-действия проходят.", "Последняя успешная версия известна."]} />
              <Checklist title="Безопасный релиз изменения" items={["Одна ограниченная задача агенту.", "Проверка в безопасном превью.", "Тест существующих сценариев, не только новой функции.", "Новая публикация и зелёный health.", "Повторный запуск в настоящем MAX.", "Запись версии и результата проверки."]} />
            </div>
            <Note tone="warning" title="Не исправляйте production напрямую">Ручное редактирование контейнера, базы или файлов на сервере создаёт версию, которой нет в истории Studio. Любая постоянная правка должна начинаться в проекте и завершаться новой публикацией.</Note>
          </GuideSection>

          <GuideSection id="billing" number="11" eyebrow="Тариф и баланс" title="Подписка, зачисление кредита и автопродление" lead="Подписка определяет лимиты проектов и ежемесячный кредит на генерации. Платёж проходит у провайдера, а ledger фиксирует каждое зачисление отдельной неизменяемой операцией.">
            <StepList steps={[
              { title: "Откройте тарифы", text: <>В аккаунте выберите <strong>«Биллинг» → «Тариф»</strong> или откройте <Route>/billing/plan</Route>.</> },
              { title: "Выберите план", text: <>Сравните число проектов, включённый кредит и функции. Для первого production-пути используйте минимальный план, который разрешает публикацию нужного проекта.</> },
              { title: "Подтвердите условия", text: <>Перед оплатой проверьте цену, период, автопродление, оферту и email для чека. Автопродление включается только с явным согласием.</> },
              { title: "Завершите оплату у ЮKassa", text: <>Studio перенаправит на защищённую форму. Не обновляйте страницу оплаты многократно. После подтверждения вернитесь в Studio.</>, result: "Платёж имеет статус succeeded." },
              { title: "Проверьте ledger", text: <>В <strong>«Биллинг» → «Транзакции»</strong> должна появиться одна операция <code>subscription_credit</code>. Повторное открытие callback или webhook не должно создавать вторую.</>, result: "Баланс увеличен ровно на кредит тарифа." },
              { title: "Управляйте продлением", text: <>Отключение автопродления не отнимает уже оплаченный период. Включить его обратно можно до конца периода, если сохранённый способ оплаты активен.</> },
            ]} />
            <div className="mt-6 grid gap-4 md:grid-cols-3">
              {[
                ["pending_payment", "Платёж создан, пользователь ещё не завершил форму."],
                ["active", "Период оплачен, лимиты и кредит применены."],
                ["cancel_at_period_end", "Продление выключено, доступ действует до даты окончания."],
                ["past_due", "Продление не прошло, действует льготный период."],
                ["canceled", "Подписка прекращена и больше не продлевается."],
                ["refunded", "Возврат подтверждён провайдером и отражён в учёте."],
              ].map(([status, copy]) => <div key={status} className="rounded-[10px] border border-[#2b2d32] bg-[#191b20] p-4"><code className="text-[11px] font-semibold text-[#6a95fa]">{status}</code><p className="mt-2 text-xs leading-5 text-[#9fa1b1]">{copy}</p></div>)}
            </div>
          </GuideSection>

          <GuideSection id="errors" number="12" eyebrow="Диагностика" title="Что означает ошибка и куда идти" lead="Сначала зафиксируйте экран, время и действие. Затем определите слой: аккаунт, сборка, безопасное превью, production, интеграция или внешний кабинет MAX.">
            <div className="grid gap-4 md:grid-cols-2">
              {[
                [MailCheck, "Не приходит email", "Проверьте адрес и спам → повторите один раз → если канал недоступен, не создавайте второй аккаунт."],
                [UserRoundCheck, "Реквизиты ожидают проверку", "Сверьте тип владельца и цифры → дождитесь проверки → при отказе исправьте ровно указанное поле."],
                [Sparkles, "Генерация долго идёт", "Обновите страницу → проверьте, восстановился ли серверный статус → не отправляйте тот же запрос повторно."],
                [LockKeyhole, "Безопасное превью не открылось", "Нажмите «Повторить» → обновите Studio → не открывайте внутренний preview URL отдельно без сессии."],
                [KeyRound, "Токен MAX отклонён", "Убедитесь, что бот промодерирован → скопируйте токен заново → проверьте, не был ли он отозван."],
                [Rocket, "Deploy failed", "Откройте детали фазы → исправьте первую ошибку сборки/health → опубликуйте новую версию."],
                [Webhook, "Webhook не активен", "Проверьте done и HTTPS → переподключите текущий токен → повторите активацию без ручной подписки."],
                [Link2, "Кнопка в MAX открывает не то", "Сверьте бота и production URL → удалите preview-параметры → сохраните URL и кнопку «Открыть»."],
                [CreditCard, "Оплата есть, баланс не изменился", "Не платите повторно → откройте транзакции → дождитесь reconcile/webhook → передайте ID платежа поддержке."],
                [Users, "Пользователи видят чужие данные", "Немедленно остановите приёмку → не продолжайте production → зафиксируйте сценарий и версию для исправления изоляции."],
              ].map(([Icon, title, copy]) => { const ItemIcon = Icon as typeof MailCheck; return <article key={String(title)} className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-5"><div className="flex gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-[8px] bg-[#2b2d32]"><ItemIcon className="size-4 text-[#4f81f7]" /></span><div><h3 className="text-sm font-semibold">{String(title)}</h3><p className="mt-2 text-sm leading-6 text-[#9fa1b1]">{String(copy)}</p></div></div></article>; })}
            </div>
            <div className="mt-8 rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-6">
              <h3 className="text-base font-semibold">Что приложить к обращению в поддержку</h3>
              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                {["Email аккаунта без пароля", "Название и ID проекта", "Точное время с часовым поясом", "Какую кнопку нажали", "Полный текст ошибки", "Скриншот без токенов и ПДн", "Production URL, если ошибка после deploy", "Ожидаемый и фактический результат"].map((item) => <p key={item} className="flex gap-2 text-sm text-[#9fa1b1]"><Check className="mt-0.5 size-4 shrink-0 text-success-fg" />{item}</p>)}
              </div>
            </div>
          </GuideSection>

          <GuideSection id="checklists" number="13" eyebrow="Контроль качества" title="Два финальных списка перед отметкой «MVP готов»" lead="Первый список проверяет, что техническая и внешняя части готовы. Второй фиксирует чистый golden path без ручного редактирования базы, подмены статусов и повторных зачислений.">
            <div className="grid gap-5 lg:grid-cols-2">
              <Checklist title="Перед реальной приёмкой" items={["SMTP доставляет подтверждение и восстановление пароля.", "ЮKassa использует настоящий shopId и утверждённую схему чеков.", "Оператор, ИНН, адрес и поддержка совпадают во всех документах.", "MAX-профиль верифицирован, бот промодерирован.", "Токен бота проверен и хранится только в защищённой интеграции.", "Сборка работает в безопасном превью.", "Production deploy имеет статус done и зелёный health.", "Webhook активен, production URL сохранён в MAX Partner.", "Кнопка «Открыть» видна в реальном клиенте MAX.", "Назначены два разных MAX-пользователя."]} />
              <Checklist title="Чистый golden path" items={["Новый пользователь зарегистрировался через UI.", "Email подтверждён по настоящему письму.", "Бизнес-профиль проверен штатным процессом.", "Реальный платёж завершён у провайдера.", "В ledger ровно одно зачисление.", "Проект создан и собран через Studio.", "Безопасное превью открыто через подписанную сессию.", "Публикация выполнена штатным deploy.", "URL добавлен в правильного MAX-бота.", "Два пользователя прошли сценарий и не увидели чужие данные.", "Автопродление управляется из кабинета.", "Во время пути не выполнялись ручные SQL/SSH-правки."]} />
            </div>
            <div className="mt-8 rounded-[14px] bg-[#121519] p-7 text-white sm:p-9">
              <div className="grid gap-7 lg:grid-cols-[1fr_auto] lg:items-center"><div><p className="omnia-kicker text-[#4f81f7]">Следующий шаг</p><h3 className="mt-3 text-[30px] font-semibold tracking-[-.04em]">Откройте Studio и пройдите путь по главам 01–09</h3><p className="mt-3 max-w-[650px] text-sm leading-6 text-white/48">Если проект уже создан, начинайте с главы, которую показывает мастер готовности. Он всегда ведёт к первой незавершённой причине.</p></div><Link href="/login?next=/max" className="omnia-button omnia-button-primary min-h-12 px-6">Перейти в MAX Studio <ArrowRight className="size-4" /></Link></div>
            </div>
          </GuideSection>
        </div>
      </div>

      <footer className="border-t border-[#2b2d32] bg-[#191b20] px-4 py-10 sm:px-7">
        <div className="mx-auto flex max-w-[1320px] flex-col gap-7 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3"><BrandMark /><span className="h-5 w-px bg-[#2b2d32]" /><span className="text-xs text-[#9fa1b1]">Руководство MAX Studio</span></div>
          <div className="flex flex-wrap gap-5 text-xs text-[#9fa1b1]"><Link href="/max/product">О продукте</Link><Link href="/mvp">Статус продукта</Link><Link href="/legal/offer">Оферта</Link><Link href="/security">Безопасность</Link><a href="https://dev.max.ru/" target="_blank" rel="noreferrer">Документация MAX <ExternalLink className="ml-1 inline size-3" /></a></div>
        </div>
      </footer>
    </main>
  );
}
