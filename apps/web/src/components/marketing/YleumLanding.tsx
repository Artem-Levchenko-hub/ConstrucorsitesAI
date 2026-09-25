import {
  ArrowRight,
  Check,
  ChevronRight,
  Database,
  History,
  LineChart,
  MessageSquareText,
  MousePointer2,
  Plug,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import Link from "next/link";

import { YleumMark } from "@/components/brand/YleumMark";
import "./landing.css";
import "./max-landing.css";
import { LandingExample } from "./LandingExample";
import { LandingUseCases } from "./LandingUseCases";
import { MarketingEvents } from "./MarketingEvents";

/** «Всё уже внутри» — только то, что действительно включено в тариф. */
const stack = [
  ["База данных", "У каждого приложения своя база. Заказы, записи и участники хранятся отдельно от чужих данных."],
  ["Вход через MAX", "Приложение узнаёт пользователя мессенджера само. Регистрацию и пароли придумывать не нужно."],
  ["Адрес и сертификат", "Приложение получает постоянный адрес по HTTPS — его и вставляют в настройки бота."],
  ["Статистика посещений", "Видно, сколько людей открыли приложение и какими экранами пользовались."],
  ["Интеграции", "Раздел подключений: оплата, рассылки, аналитика. Платёжного провайдера вы подключаете отдельно — генерация не делает этого за вас."],
  ["Изоляция", "Приложения разных владельцев не видят данные друг друга и работают в отдельных контейнерах."],
] as const;

const afterLaunch = [
  [RefreshCw, "Обновление без простоя", "Продолжайте менять приложение после запуска. Новая версия заменяет прежнюю, когда вы её опубликуете."],
  [History, "Возврат к прошлой версии", "История сохраняется. Если новая версия не понравилась, возвращайтесь к предыдущей."],
  [Plug, "Подключения по мере роста", "Начните с простого сценария и добавляйте сервисы тогда, когда они действительно понадобятся."],
] as const;

/** Честные факты платформы вместо выдуманного отзыва. */
const facts = [
  ["Своя база", "Каждое приложение получает отдельную базу данных, а не общую таблицу с чужими записями."],
  ["Свой адрес", "Приложение публикуется по постоянному HTTPS-адресу, готовому к вставке в кабинет MAX."],
  ["Откат за секунды", "Версии хранятся, и возврат к прошлой — это выбор в списке, а не восстановление из копии."],
] as const;

const faqs = [
  ["code", "Я не программист. У меня получится?", "Начните с того, что должен сделать ваш клиент: выбрать товар, записаться или открыть материал. Пошаговый опрос поможет описать задачу. Yleum соберёт приложение, а вы проверите его и попросите изменения обычным сообщением."],
  ["free", "Что значит «начать бесплатно»?", "Вы регистрируетесь на тарифе Free без оплаты и привязки карты. Генерация расходует доступный баланс; лимиты и условия публикации зависят от тарифа. Актуальные условия вы увидите в аккаунте до оплаты. Free — не обещание безлимитной генерации или бесплатного публичного хостинга."],
  ["max", "Бот MAX нужен сразу?", "Нет. Сначала можно описать идею и работать над приложением. Для запуска внутри MAX нужно создать бота в кабинете MAX, пройти необходимые проверки на стороне платформы и подключить его в Yleum. Помощник проведёт по шагам."],
  ["change", "Можно менять приложение после запуска?", "Да. Продолжайте работу в редакторе, проверяйте новую версию и публикуйте обновление, когда готовы. История версий помогает посмотреть предыдущий результат."],
  ["payments", "Можно добавить оплату и другие сервисы?", "В Yleum есть раздел интеграций. Доступность конкретного сервиса зависит от его поддержки и вашего тарифа. Подключение платёжного провайдера и его условия оформляются отдельно — сама генерация приложения не подключает приём платежей автоматически."],
  ["ready", "Что проверить перед публикацией?", "Пройдите путь своего клиента в предпросмотре, проверьте тексты, формы и нужные функции. Затем выполните обязательные пункты в окне запуска. Созданный ИИ результат требует вашей проверки перед тем, как им начнут пользоваться клиенты."],
  ["how", "Как вообще сделать приложение для MAX?", "Коротко: опишите задачу словами, получите готовое приложение, проверьте его в предпросмотре, создайте бота в кабинете MAX и вставьте туда адрес опубликованного приложения. Подробный разбор каждого шага — в руководстве."],
  ["time", "Сколько времени занимает первая версия?", "Первую работающую версию вы видите в том же разговоре, в котором описали идею. Доводка до запуска зависит от того, сколько экранов и правил нужно вашему бизнесу."],
  ["transfer", "У меня уже есть сайт. Можно перенести его в MAX?", "Приложение внутри MAX — это не копия сайта: сценарии там короче, а вход уже выполнен. Разумнее перенести один-два главных действия клиента, а не всю структуру сайта."],
] as const;

function Signup({ placement, children = "Начать бесплатно", tone = "dark" }: {
  placement: string;
  children?: React.ReactNode;
  tone?: "dark" | "light";
}) {
  return (
    <Link
      href="/max/register"
      className={`yl-cta yl-cta--lg${tone === "light" ? " yl-cta--light" : ""}`}
      data-marketing="signup_click"
      data-placement={placement}
    >
      {children}
      <ArrowRight size={18} />
    </Link>
  );
}

export function YleumLanding() {
  return (
    <main data-max-studio className="yl">
      <MarketingEvents page="landing" />
      <a className="yl-skip" href="#main-content">К содержимому</a>

      <header className="yl-header">
        <div className="yl-header__inner">
          <Link href="/" className="yl-display" aria-label="Yleum — главная" style={{ display: "inline-flex", alignItems: "center", gap: 9, fontSize: 19, letterSpacing: "-0.03em" }}>
            <YleumMark className="size-6" />Yleum
          </Link>
          <nav aria-label="Разделы страницы">
            <a href="#stack">Что внутри</a>
            <a href="#examples">Примеры</a>
            <a href="#process">Как работает</a>
            <a href="#pricing">Тарифы</a>
            <a href="#faq">Вопросы</a>
          </nav>
          <div className="yl-header__actions">
            <Link className="yl-login" href="/login?next=/max" data-marketing="login_click" data-placement="header">Войти</Link>
            <Link href="/max/register" className="yl-cta" data-marketing="signup_click" data-placement="header">Начать бесплатно</Link>
          </div>
        </div>
      </header>

      {/* 1 — обещание и мгновенная демонстрация */}
      <section id="main-content" className="yl-wrap yl-hero" data-marketing-section="hero">
        <div>
          <p className="yl-eyebrow"><span />Yleum · приложения внутри MAX</p>
          <h1>Ваш бизнес.<br />Ваше приложение.<br /><em className="yl-grad-text">Внутри MAX.</em></h1>
          <p className="yl-lead">Меню с заказом, запись на услуги или клуб для своих. Опишите идею словами — ИИ соберёт приложение, которое вы сможете менять без кода.</p>
          <div className="yl-actions">
            <Signup placement="hero" />
            <a className="yl-quiet" href="#examples">Посмотреть примеры <ChevronRight size={16} /></a>
          </div>
          <p className="yl-note">Тариф Free · Без привязки карты</p>
        </div>
        <LandingExample />
      </section>

      <div className="yl-wrap yl-strip">
        <span><Check />Не нужно писать код</span>
        <span><Check />Правки в диалоге</span>
        <span><Check />Предпросмотр до запуска</span>
        <span><Check />Приложение внутри MAX</span>
      </div>

      {/* 2 — что уже включено */}
      <section id="stack" className="yl-section" data-marketing-section="stack">
        <div className="yl-wrap">
          <div className="yl-head">
            <p className="yl-eyebrow"><span />Ничего не настраивать</p>
            <h2>Всё уже внутри.<br />Собирать нечего.</h2>
            <p className="yl-lead">Запустить приложение обычно мешает не идея, а обвязка: база, вход, адрес, сертификат. Здесь она готова заранее.</p>
          </div>
          <ul className="yl-stack-list">
            {stack.map(([title, text], i) => (
              <li key={title}>
                <b>{String(i + 1).padStart(2, "0")}</b>
                <h3>{title}</h3>
                <p>{text}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* 3 — что вы запустите */}
      <LandingUseCases />

      {/* 4 — редактор: вторая глава, фон меняется */}
      <section id="process" className="yl-section yl-section--tint" data-marketing-section="editor">
        <div className="yl-wrap yl-editor">
          <div>
            <p className="yl-eyebrow"><span />Вы управляете результатом</p>
            <h2>Меняется как<br />разговор.<br /><em className="yl-grad-text">Не как настройки.</em></h2>
            <p className="yl-lead" style={{ marginTop: 26 }}>Не нужно разбираться в панелях до первого результата. Сформулируйте задачу, посмотрите приложение и уточните, что изменить.</p>
            <div className="yl-actions"><Signup placement="editor" /></div>
          </div>
          <div className="yl-editor-proof">
            <div className="yl-editor-top"><span><span className="yl-dot" />Редактор</span><span>Чат + предпросмотр</span></div>
            <div className="yl-editor-msg"><MessageSquareText size={18} /><p>Добавь поиск по меню и сделай кнопку заказа заметнее.</p></div>
            <div className="yl-editor-reply"><Check size={17} /><p>Проверьте изменения в предпросмотре.<small>Иллюстрация работы редактора</small></p></div>
            <ul>
              {([
                [MousePointer2, "Проверяйте как клиент", "Открывайте экраны и проходите нужный сценарий."],
                [History, "Сохраняйте историю", "Сравнивайте результат с предыдущими версиями."],
                [SlidersHorizontal, "Уточняйте постепенно", "Меняйте оформление, тексты и функции в ходе работы."],
              ] as const).map(([Icon, title, text]) => (
                <li key={title}>
                  <Icon size={19} />
                  <div><h3>{title}</h3><p>{text}</p></div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {/* 5 — что дальше */}
      <section className="yl-section yl-section--tint" style={{ paddingTop: 0 }} data-marketing-section="after">
        <div className="yl-wrap">
          <div className="yl-head">
            <p className="yl-eyebrow"><span />После запуска</p>
            <h2>Работа не кончается<br />на публикации.</h2>
          </div>
          <div className="yl-cards">
            {afterLaunch.map(([Icon, title, text]) => (
              <article className="yl-card" key={title}>
                <Icon size={22} />
                <h3>{title}</h3>
                <p>{text}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* 6 — честные факты вместо отзыва */}
      <section className="yl-section" data-marketing-section="proof">
        <div className="yl-wrap">
          <div className="yl-head">
            <p className="yl-eyebrow"><span />Что уже работает</p>
            <h2>Не обещания.<br />Устройство платформы.</h2>
            <p className="yl-lead">Мы пока не показываем чужие истории успеха — вместо них то, что можно проверить в своём приложении в первый же день.</p>
          </div>
          <div className="yl-facts">
            {facts.map(([title, text]) => (
              <div className="yl-fact" key={title}>
                <strong>{title}</strong>
                <span>{text}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 7 — тарифы */}
      <section id="pricing" className="yl-section yl-section--tint" data-marketing-section="pricing">
        <div className="yl-wrap">
          <div className="yl-head">
            <p className="yl-eyebrow"><span />Сначала попробуйте</p>
            <h2>Большая идея.<br />Бесплатный первый шаг.</h2>
          </div>
          <div className="yl-plans">
            <div className="yl-plan">
              <div className="yl-plan-top"><span>Free</span><span>Без привязки карты</span></div>
              <p className="yl-price">0 ₽<span>за тариф</span></p>
              <ul>
                <li><Check />Личное рабочее пространство</li>
                <li><Check />Пошаговое описание проекта</li>
                <li><Check />Доступ к редактору в рамках лимитов</li>
              </ul>
              <Signup placement="free">Зарегистрироваться бесплатно</Signup>
              <p className="yl-note">Генерация расходует баланс. Публикация и дополнительные возможности зависят от тарифа.</p>
            </div>
            <div className="yl-plan">
              <div className="yl-plan-top"><span>Платные тарифы</span><span>Когда пойдут клиенты</span></div>
              <p className="yl-price">По тарифу<span>условия в кабинете</span></p>
              <ul>
                <li><Check />Больше генераций и публикаций</li>
                <li><Check />Раздел интеграций</li>
                <li><Check />Обновление приложения после запуска</li>
              </ul>
              <Link href="/pricing" className="yl-cta yl-cta--lg yl-cta--light" style={{ border: "1px solid var(--yl-line)", justifyContent: "center" }}>
                Посмотреть тарифы<ArrowRight size={18} />
              </Link>
              <p className="yl-note">Состав и сумма показываются из действующей конфигурации в личном кабинете до оплаты.</p>
            </div>
          </div>
        </div>
      </section>

      {/* 8 — вопросы */}
      <section id="faq" className="yl-section yl-section--white" data-marketing-section="faq">
        <div className="yl-wrap yl-faq-layout">
          <div>
            <p className="yl-eyebrow"><span />Без неясностей</p>
            <h2 style={{ marginTop: 20 }}>Хорошие вопросы.<br />Прямые ответы.</h2>
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
          <p>Начните с одного полезного сценария. Остальное соберётся вокруг него.</p>
          <Signup placement="final" tone="light">Создать приложение</Signup>
          <small>Регистрация на Free · Без привязки карты</small>
        </div>
      </section>

      <footer className="yl-footer">
        <div className="yl-wrap">
          <div className="yl-footer-grid">
            <div className="yl-footer-brand">
              <Link href="/" className="yl-display" aria-label="Yleum — главная" style={{ display: "inline-flex", alignItems: "center", gap: 9, fontSize: 19, letterSpacing: "-0.03em" }}>
                <YleumMark className="size-6" />Yleum
              </Link>
              <p>Приложения для бизнеса внутри MAX. Произносится «Илиум».</p>
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
