import { ArrowRight, Check } from "lucide-react";
import Link from "next/link";

import { YleumMark } from "@/components/brand/YleumMark";
import "./landing.css";
import "./landing/screens.css";
import { AppScreen, ScreenCollage } from "./landing/AppScreens";
import { HeroStage } from "./landing/HeroStage";
import { MarketingEvents } from "./MarketingEvents";

/** Что включено в платформу. Формулировки короткие: секция показывает, а не рассказывает. */
const stack = [
  ["База данных", "У каждого приложения своя"],
  ["Вход через MAX", "Без регистрации и паролей"],
  ["Адрес по HTTPS", "Готов для кабинета бота"],
  ["Статистика", "Кто открыл и докуда дошёл"],
  ["Интеграции", "Оплата подключается отдельно"],
  ["Изоляция", "Чужие данные недоступны"],
] as const;

const useCases = [
  ["Заказ", "Гость выбирает и забирает к нужному времени."],
  ["Запись", "Клиент сам находит свободное время."],
  ["Сообщество", "Встречи и материалы — только для своих."],
] as const;

const afterLaunch = [
  ["Обновление", "Меняйте приложение после запуска — новая версия заменит прежнюю."],
  ["Возврат назад", "Не понравилось? Вернитесь к предыдущей версии."],
  ["Рост", "Добавляйте сервисы тогда, когда они понадобятся."],
] as const;

const facts = [
  ["Своя база", "Отдельная база данных, а не общая таблица с чужими записями."],
  ["Свой адрес", "Постоянный HTTPS-адрес, готовый к вставке в кабинет MAX."],
  ["Откат за секунды", "Возврат к прошлой версии — выбор в списке, а не восстановление из копии."],
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
  ["transfer", "У меня уже есть сайт. Можно перенести его в MAX?", "Приложение внутри MAX — это не копия сайта: сценарии там короче, а вход уже выполнен. Разумнее перенести одно-два главных действия клиента, а не всю структуру сайта."],
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

function Brand() {
  return (
    <Link href="/" className="yl-brand" aria-label="Yleum — главная">
      <YleumMark className="yl-brand-mark" />Yleum
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
          <Brand />
          <nav aria-label="Разделы страницы">
            <a href="#examples">Примеры</a>
            <a href="#stack">Что внутри</a>
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

      {/* 1 — обещание и работающий пример */}
      <section id="main-content" className="yl-hero" data-marketing-section="hero">
        <div className="yl-wrap yl-hero-copy">
          <h1>Опишите словами.<br /><em className="yl-grad-text">Получите приложение.</em></h1>
          <p className="yl-lead">Внутри MAX, где ваши клиенты уже есть.</p>
          <div className="yl-actions">
            <Signup placement="hero" />
            <span className="yl-note">Тариф Free · Без карты</span>
          </div>
        </div>
        <div className="yl-wrap"><HeroStage /></div>
      </section>

      {/* 2 — что отсюда выходит */}
      <section id="examples" className="yl-section" data-marketing-section="examples">
        <div className="yl-wrap yl-head yl-head--center">
          <h2>Что вы запустите.</h2>
        </div>
        <ScreenCollage
          screens={["status", "cafe", "salon", "club", "loyalty"]}
          caption="Примеры экранов. Содержание и функции вы задаёте для своего бизнеса."
        />
        <div className="yl-wrap yl-usecases">
          {useCases.map(([title, text]) => (
            <article key={title}>
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      {/* 3 — что уже внутри */}
      <section id="stack" className="yl-section yl-section--tint" data-marketing-section="stack">
        <div className="yl-wrap yl-stack">
          <div>
            <h2>Весь стек внутри.<br /><em className="yl-grad-text">Настраивать нечего.</em></h2>
            <p className="yl-lead">База, вход, адрес и сертификат готовы заранее. Вы описываете бизнес, а не сервер.</p>
            <div className="yl-shop-peek"><AppScreen kind="shop" /></div>
          </div>
          <ul className="yl-stack-list">
            {stack.map(([title, text]) => (
              <li key={title}>
                <Check size={17} />
                <div><h3>{title}</h3><p>{text}</p></div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* 4 — редактор: правка показана на самом приложении */}
      <section id="process" className="yl-section" data-marketing-section="editor">
        <div className="yl-wrap yl-head yl-head--center">
          <h2>Меняется как разговор.<br /><em className="yl-grad-text">Не как настройки.</em></h2>
        </div>
        <div className="yl-wrap yl-diff">
          <figure>
            <figcaption>Было</figcaption>
            <AppScreen kind="cafe" />
          </figure>
          <div className="yl-diff-ask">
            <p className="ys-stage-label">Вы пишете</p>
            <p className="yl-diff-text">Добавь поиск по меню и сделай кнопку заказа заметнее.</p>
            <ArrowRight size={20} />
          </div>
          <figure>
            <figcaption>Стало</figcaption>
            <AppScreen kind="cafe-after" />
          </figure>
        </div>
        <div className="yl-wrap yl-steps">
          {[
            ["Опишите", "Обычным сообщением, без настроек и полей"],
            ["Проверьте", "Пройдите путь своего клиента до публикации"],
            ["Опубликуйте", "Адрес готов для кабинета бота MAX"],
          ].map(([title, text], i) => (
            <article key={title}>
              <b>{String(i + 1).padStart(2, "0")}</b>
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      {/* 5 — после запуска */}
      <section className="yl-section yl-section--tint" data-marketing-section="after">
        <div className="yl-wrap yl-after">
          <div className="yl-after-copy">
            <h2>Запуск —<br />не финиш.</h2>
            <ul>
              {afterLaunch.map(([title, text]) => (
                <li key={title}><strong>{title}</strong><span>{text}</span></li>
              ))}
            </ul>
          </div>
          <div className="yl-after-screens">
            <AppScreen kind="status" />
            <AppScreen kind="loyalty" />
          </div>
        </div>
      </section>

      {/* 6 — честные факты вместо отзыва */}
      <section className="yl-section" data-marketing-section="proof">
        <div className="yl-wrap yl-head yl-head--center">
          <h2>Не обещания.<br />Устройство платформы.</h2>
          <p className="yl-lead">Чужих историй успеха пока не показываем — вот то, что проверяется в свой первый день.</p>
        </div>
        <div className="yl-wrap yl-facts">
          {facts.map(([title, text]) => (
            <div className="yl-fact" key={title}><strong>{title}</strong><span>{text}</span></div>
          ))}
        </div>
      </section>

      {/* 7 — тарифы */}
      <section id="pricing" className="yl-section yl-section--tint" data-marketing-section="pricing">
        <div className="yl-wrap yl-head yl-head--center">
          <h2>Большая идея.<br />Бесплатный первый шаг.</h2>
        </div>
        <div className="yl-wrap yl-plans">
          <div className="yl-plan">
            <div className="yl-plan-top"><span>Free</span><span>Без карты</span></div>
            <p className="yl-price">0 ₽<span>за тариф</span></p>
            <ul>
              <li><Check />Личное рабочее пространство</li>
              <li><Check />Пошаговое описание проекта</li>
              <li><Check />Редактор в рамках лимитов</li>
            </ul>
            <Signup placement="free">Зарегистрироваться</Signup>
            <p className="yl-note">Генерация расходует баланс. Публикация зависит от тарифа.</p>
          </div>
          <div className="yl-plan">
            <div className="yl-plan-top"><span>Платные тарифы</span><span>Когда пойдут клиенты</span></div>
            <p className="yl-price">По тарифу<span>условия в кабинете</span></p>
            <ul>
              <li><Check />Больше генераций и публикаций</li>
              <li><Check />Раздел интеграций</li>
              <li><Check />Обновление после запуска</li>
            </ul>
            <Link href="/pricing" className="yl-cta yl-cta--lg yl-cta--outline">Посмотреть тарифы<ArrowRight size={18} /></Link>
            <p className="yl-note">Состав и сумма — из действующей конфигурации в кабинете до оплаты.</p>
          </div>
        </div>
      </section>

      {/* 8 — вопросы */}
      <section id="faq" className="yl-section yl-section--white" data-marketing-section="faq">
        <div className="yl-wrap yl-faq-layout">
          <div>
            <h2>Хорошие вопросы.<br />Прямые ответы.</h2>
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
          <Signup placement="final" tone="light">Создать приложение</Signup>
          <small>Регистрация на Free · Без привязки карты</small>
        </div>
      </section>

      <footer className="yl-footer">
        <div className="yl-wrap">
          <div className="yl-footer-grid">
            <div className="yl-footer-brand">
              <Brand />
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
