import { ArrowRight, Check } from "lucide-react";
import Link from "next/link";

import { YleumMark } from "@/components/brand/YleumMark";
import type { BillingPlan } from "@/lib/api/account";
import "./landing.css";
import "./landing/screens.css";
import { AppScreen, ScreenCollage } from "./landing/AppScreens";
import { Capabilities, Integrations } from "./landing/Capabilities";
import { HeroStage } from "./landing/HeroStage";
import { LandingPricing } from "./landing/LandingPricing";
import { MarketingEvents } from "./MarketingEvents";

/** Что включено в платформу. Формулировки короткие: секция показывает, а не рассказывает. */
const stack = [
  ["База данных", "У каждого приложения своя"],
  ["Вход через MAX", "Без регистрации и паролей"],
  ["Адрес по HTTPS", "Готов для кабинета бота"],
  ["Статистика", "Кто открыл и докуда дошёл"],
  ["Оплата", "Подключение магазина ЮKassa"],
  ["Конфиденциальность", "Другие владельцы приложений не видят данные вашего проекта"],
] as const;

const faqs = [
  ["code", "Я не программист. У меня получится?", "Начните с того, что должен сделать ваш клиент: выбрать товар, записаться или открыть материал. Пошаговый опрос поможет описать задачу. Yleum соберёт приложение, а вы проверите его и попросите изменения обычным сообщением."],
  ["free", "Что значит «начать бесплатно»?", "Регистрация на бесплатном тарифе не требует оплаты. В нём есть ограничения на проекты, генерации и правки. Сравнение актуальных тарифов находится прямо на этой странице; выбранные условия вы увидите в аккаунте до оплаты."],
  ["max", "Бот MAX нужен сразу?", "Нет. Сначала можно описать идею и работать над приложением. Для запуска внутри MAX нужно создать бота в кабинете MAX, пройти необходимые проверки на стороне платформы и подключить его в Yleum. Помощник проведёт по шагам."],
  ["change", "Можно менять приложение после запуска?", "Да. Продолжайте работу в редакторе, проверяйте результат в превью и публикуйте обновление, когда готовы."],
  ["payments", "Как подключить приём оплаты?", "Откройте раздел интеграций в редакторе проекта и подключите свой магазин ЮKassa. Подключение магазина и проверка оплаты выполняются отдельно от создания приложения."],
  ["ready", "Что проверить перед публикацией?", "Пройдите путь своего клиента в предпросмотре, проверьте тексты, формы и нужные функции. Затем выполните обязательные пункты в окне запуска. Созданный ИИ результат требует вашей проверки перед тем, как им начнут пользоваться клиенты."],
  ["how", "Как вообще сделать приложение для MAX?", "Коротко: опишите задачу словами, получите готовое приложение, проверьте его в предпросмотре, создайте бота в кабинете MAX и вставьте туда адрес опубликованного приложения. Подробный разбор каждого шага — в руководстве."],
  ["time", "Сколько времени занимает первая сборка?", "Первый работающий результат вы видите в том же разговоре, в котором описали идею. Доводка до запуска зависит от того, сколько экранов и правил нужно вашему бизнесу."],
  ["transfer", "У меня уже есть сайт. Можно перенести его в MAX?", "Приложение внутри MAX — это не копия сайта: сценарии там короче, а вход уже выполнен. Разумнее перенести одно-два главных действия клиента, а не всю структуру сайта."],
] as const;

function Brand() {
  return (
    <Link href="/" className="yl-brand" aria-label="Yleum — главная">
      <YleumMark className="yl-brand-mark" />Yleum
    </Link>
  );
}

export function YleumLanding({ plans = null, signedIn = false, now }: { plans?: BillingPlan[] | null; signedIn?: boolean; now: number }) {
  return (
    <main data-max-studio className="yl">
      <MarketingEvents page="landing" />
      <a className="yl-skip" href="#main-content">К содержимому</a>

      <header className="yl-header">
        <div className="yl-header__inner">
          <Brand />
          <nav aria-label="Разделы страницы">
            <a href="#examples">Примеры</a>
            <a href="#stack">Что внутри</a>
            <a href="#process">Как работает</a>
            <a href="#pricing">Тарифы</a>
            <a href="#faq">Вопросы</a>
          </nav>
          <div className="yl-header__actions">
            {signedIn ? <Link className="yl-login" href="/max">Мои приложения</Link>
              : <Link className="yl-login" href="/login?next=/max" data-marketing="login_click" data-placement="header">Войти</Link>}
          </div>
        </div>
      </header>

      {/* 1 — обещание и работающий пример */}
      <section id="main-content" className="yl-hero" data-marketing-section="hero">
        <div className="yl-wrap yl-hero-copy">
          <h1>Опишите словами<br /><em className="yl-grad-text">Получите приложение</em></h1>
          <p className="yl-lead">Клиенты открывают ваше приложение в MAX, без отдельной установки</p>
          <ul className="yl-niches" aria-label="Для какого бизнеса">
            {["Кофейня", "Кафе", "Ресторан", "Салон красоты", "Барбершоп", "Магазин", "Студия", "Школа", "Клуб"].map((niche) => (
              <li className="yl-niche" key={niche}>{niche}</li>
            ))}
          </ul>
          <div className="yl-actions">
            <Link href={signedIn ? "/max" : "/max/register"} className="yl-cta yl-cta--lg"
              data-marketing={signedIn ? undefined : "signup_click"} data-placement="hero">
              {signedIn ? "Открыть студию" : "Начать бесплатно"} <ArrowRight size={18} aria-hidden="true" />
            </Link>
          </div>
        </div>
        <div className="yl-wrap"><HeroStage initialNow={now} /></div>
      </section>

      {/* 2 — что отсюда выходит */}
      <section id="examples" className="yl-section" data-marketing-section="examples">
        <div className="yl-wrap yl-head yl-head--center">
          <h2>Что сможет ваше приложение</h2>
          <p className="yl-lead">Попробуйте примеры и расскажите, что нужно вашим клиентам. Yleum соберёт приложение под вашу задачу.</p>
        </div>
        <ScreenCollage
          screens={[
            { kind: "status", description: "Клиент следит за статусом и временем выдачи заказа" },
            { kind: "estimate", description: "Клиент рассчитывает стоимость услуги по своим параметрам" },
            { kind: "lesson", description: "Ученик практикует английский для поездки" },
            { kind: "feedback", description: "Клиент оценивает визит и оставляет отзыв" },
            { kind: "loyalty", description: "Клиент видит свои бонусы и историю покупок" },
          ]}
        />
      </section>

      {/* 3 — возможности показаны, а не перечислены */}
      <section id="stack" className="yl-section yl-section--tint" data-marketing-section="stack">
        <div className="yl-wrap yl-head yl-head--center">
          <h2>Всё, чем бизнес зарабатывает,<br /><em className="yl-grad-text">— внутри MAX.</em></h2>
          <p className="yl-lead">Каталог, запись, оплата и бонусы — на примерах экранов приложения.</p>
        </div>
        <Capabilities />
        <div className="yl-wrap yl-head yl-head--center yl-head--sub">
          <h3 className="yl-subhead">Подключите приём оплаты</h3>
        </div>
        <Integrations />
        <div className="yl-wrap yl-platform">
          <h3 className="yl-subhead">И то, что обычно настраивают неделями</h3>
          <div className="yl-platform-grid">
            {stack.map(([title, text]) => (
              <div key={title}>
                <Check size={15} />
                <strong>{title}</strong>
                <span>{text}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 4 — редактор: правка показана на самом приложении */}
      <section id="process" className="yl-section" data-marketing-section="editor">
        <div className="yl-wrap yl-head yl-head--center">
          <h2>Меняйте приложение<br /><em className="yl-grad-text">простыми словами</em></h2>
        </div>
        <div className="yl-wrap yl-diff">
          <figure>
            <figcaption>Было</figcaption>
            <AppScreen kind="cafe" />
          </figure>
          <div className="yl-diff-ask">
            <p className="ys-stage-label">Вы пишете</p>
            <p className="yl-diff-text">Добавь поиск по меню и сделай кнопку заказа заметнее</p>
            <ArrowRight size={20} />
          </div>
          <figure>
            <figcaption>Стало</figcaption>
            <AppScreen kind="cafe-after" />
          </figure>
        </div>
        <div className="yl-wrap yl-head yl-head--steps">
          <h3 className="yl-subhead">Этапы создания вашего приложения в MAX</h3>
        </div>
        <div className="yl-wrap yl-steps">
          {[
            ["Опишите бизнес", "Укажите товары, услуги, цены, адреса и другие составляющие бизнеса"],
            ["Yleum соберёт приложение", "Экраны и функции на основе вашего описания"],
            ["Проверьте и поправьте", "Правки тоже пишете словами, а не ищете в настройках"],
            ["Опубликуйте в боте MAX", "Клиентам не нужно ничего устанавливать"],
          ].map(([title, text], i) => (
            <article key={title}>
              <b>{String(i + 1).padStart(2, "0")}</b>
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      {/* 7 — тарифы */}
      <LandingPricing plans={plans} signedIn={signedIn} />

      {/* 8 — вопросы */}
      <section id="faq" className="yl-section yl-section--white" data-marketing-section="faq">
        <div className="yl-wrap yl-faq-layout">
          <div>
            <h2>Часто задаваемые вопросы</h2>
            <Link href="/max/guide" className="yl-quiet" data-marketing="guide_click" data-placement="faq">
              Открыть руководство <ArrowRight size={17} />
            </Link>
          </div>
          <div className="yl-faq">
            {faqs.map(([id, question, answer]) => (
              <details key={id} data-faq={id}>
                <summary>{question}<span aria-hidden="true">+</span></summary>
                <p>{answer}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* 9 — финальный экран целиком в цвете бренда */}
      <section className="yl-sendoff" data-marketing-section="final">
        <div className="yl-wrap">
          <h2>Ваша идея —<br />следующая.</h2>
          <a href="#yl-hero-prompt" className="yl-cta yl-cta--lg yl-cta--light">
            Описать идею <ArrowRight size={18} aria-hidden="true" />
          </a>
        </div>
      </section>

      <footer className="yl-footer">
        <div className="yl-wrap">
          <div className="yl-footer-grid">
            <div className="yl-footer-brand">
              <Brand />
              <p>Приложения для бизнеса внутри MAX.</p>
            </div>
            <div>
              <h3>Продукт</h3>
              <nav aria-label="Продукт">
                <Link href="/max/guide">Руководство</Link>
                <Link href="/max/start">Быстрый старт</Link>
                <Link href="/pricing">Тарифы</Link>
                <Link href="/changelog">Что нового</Link>
              </nav>
            </div>
            <div>
              <h3>Компания</h3>
              <nav aria-label="Компания">
                <Link href="/about">О нас</Link>
                <Link href="/contact">Контакты</Link>
                <Link href="/requisites">Реквизиты</Link>
                <Link href="/security">Безопасность</Link>
              </nav>
            </div>
            <div>
              <h3>Документы</h3>
              <nav aria-label="Документы">
                <Link href="/legal/terms">Условия</Link>
                <Link href="/legal/offer">Оферта</Link>
                <Link href="/legal/privacy">Конфиденциальность</Link>
                <Link href="/legal/refunds">Оплата и возвраты</Link>
              </nav>
            </div>
          </div>
          <div className="yl-footer-bottom">
            <span>© {new Date().getFullYear()} Yleum</span>
            <span>Не является официальным продуктом MAX.</span>
          </div>
        </div>
      </footer>
    </main>
  );
}
