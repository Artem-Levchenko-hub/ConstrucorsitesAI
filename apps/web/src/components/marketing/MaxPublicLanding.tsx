import { ArrowRight, Eye, MessageSquareText, Rocket, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";

import "@/components/max/max-studio.css";
import "./max-public.css";
import { BrandMark } from "./BrandMark";

const benefits = [
  { Icon: MessageSquareText, title: "Начните с задачи", text: "Опишите идею обычными словами. Studio уточнит детали и сохранит ответы в проекте." },
  { Icon: Eye, title: "Проверяйте результат", text: "Первая версия появляется в мобильном предпросмотре, где можно пройти сценарий и попросить правки." },
  { Icon: ShieldCheck, title: "Подключайте по готовности", text: "Данные владельца, MAX и нужные интеграции добавляются в защищённых разделах перед запуском." },
] as const;
const steps = [
  ["Опишите приложение", "После входа короткий пошаговый бриф помогает зафиксировать аудиторию, задачу и главное действие."],
  ["Получите первую сборку", "Studio создаёт проект только после финального подтверждения и показывает ход реальной генерации."],
  ["Проверьте и уточните", "Откройте экраны в предпросмотре и меняйте приложение сообщениями, сохраняя историю версий."],
  ["Подготовьте запуск", "Заполните обязательные сведения, подключите MAX и проверьте готовность перед публикацией."],
] as const;
const faqs = [
  ["Нужно ли уметь программировать?", "Нет. Вы описываете продукт, проверяете результат и принимаете решения в понятном интерфейсе."],
  ["Когда понадобится MAX Partner?", "Для первой сборки достаточно подтверждённого email. MAX Partner понадобится перед запуском приложения внутри MAX."],
  ["Пример на странице — реальный проект?", "Нет. Это подписанная иллюстрация пути «идея → интерфейс», а не опубликованный клиентский проект."],
  ["Где посмотреть стоимость?", "Актуальные тарифы и суммы приходят с сервера и показываются в разделе «Тариф» после входа, до перехода к оплате."],
] as const;

function Example() {
  return <figure className="max-example" aria-label="Пример интерфейса — иллюстрация">
    <figcaption><strong>Пример интерфейса</strong><span>Иллюстрация, не действующий проект</span></figcaption>
    <div className="max-example__request"><span>Идея владельца</span><p>Приложение кофейни: меню, заказ к выдаче и программа лояльности.</p></div>
    <div className="max-example__flow"><Sparkles className="size-4" />MAX Studio</div>
    <div className="max-example__app"><div className="max-example__bar"><strong>Кофе рядом</strong><span>Мобильный предпросмотр</span></div><div className="max-example__body">
      <span className="max-public-kicker">Доброе утро</span><h2>Выберите напиток</h2><p>Пример первого экрана будущего MAX-приложения</p>
      <div className="max-example__row"><span><strong>Капучино</strong><small>Классический</small></span><strong>от 220 ₽</strong></div>
      <div className="max-example__row"><span><strong>Флэт уайт</strong><small>Двойной эспрессо</small></span><strong>от 260 ₽</strong></div><div className="max-example__cta">Продолжить заказ</div>
    </div></div>
  </figure>;
}

function Header() {
  return <header className="max-public-header"><div className="max-public-header__inner">
    <div className="max-public-header__brand"><BrandMark /><span>MAX Studio</span></div>
    <nav aria-label="Разделы страницы"><a href="#benefits">Возможности</a><a href="#process">Как работает</a><a href="#faq">Вопросы</a><Link href="/max/guide">Руководство</Link></nav>
    <div className="max-public-header__actions"><Link className="max-public-link" href="/login?next=/max">Войти</Link><Link className="max-public-button max-public-button--primary" href="/max/register">Создать приложение</Link></div>
  </div></header>;
}

export function MaxPublicLanding() {
  return <main data-max-studio className="max-public">
    <Header />
    <section className="max-public-wrap max-landing-hero"><div><p className="max-public-kicker">Приложения для MAX</p><h1>От идеи до <em>MAX-приложения</em></h1><p className="max-landing-lead">Опишите задачу, проверьте рабочую версию в мобильном предпросмотре и подготовьте запуск — в одном последовательном сценарии.</p><div className="max-public-actions"><Link className="max-public-button max-public-button--primary" href="/max/register">Создать MAX-приложение <ArrowRight className="size-4" /></Link><Link className="max-public-button" href="/login?next=/max">У меня уже есть аккаунт</Link></div><p className="max-landing-note">Для начала нужен подтверждённый email. Проект создаётся только после вашего финального подтверждения.</p></div><Example /></section>
    <section id="benefits" className="max-public-wrap max-public-section"><div className="max-public-section__heading"><div><p className="max-public-kicker">Понятный путь</p><h2>Решения остаются за вами</h2></div><p>Studio ведёт от идеи к проверяемому результату, а важные действия — создание проекта, подключение секретов и публикация — требуют явного подтверждения.</p></div><div className="max-benefit-grid">{benefits.map(({ Icon, title, text }) => <article key={title}><Icon className="size-5" /><h3>{title}</h3><p>{text}</p></article>)}</div></section>
    <section id="process" className="max-public-wrap max-public-section"><div className="max-public-section__heading"><div><p className="max-public-kicker">Как это работает</p><h2>Четыре этапа до запуска</h2></div><p>Каждый следующий шаг виден в интерфейсе. Серверное состояние определяет, что уже готово и какое действие доступно сейчас.</p></div><ol className="max-process">{steps.map(([title, text]) => <li key={title}><h3>{title}</h3><p>{text}</p></li>)}</ol><div className="max-public-actions"><Link className="max-public-button" href="/max/start">Открыть быстрый старт <ArrowRight className="size-4" /></Link><Link className="max-public-link" href="/max/guide">Полное руководство</Link></div></section>
    <section id="faq" className="max-public-wrap max-public-section"><p className="max-public-kicker">FAQ</p><h2>Перед началом</h2><div className="max-faq">{faqs.map(([question, answer]) => <details key={question}><summary>{question}</summary><p>{answer}</p></details>)}</div></section>
    <section className="max-final"><div className="max-public-wrap"><Rocket className="mx-auto size-6 text-accent" /><h2>Начните с идеи приложения</h2><p>Создайте аккаунт, подтвердите email и перейдите к короткому брифу.</p><div className="max-public-actions"><Link className="max-public-button max-public-button--primary" href="/max/register">Создать MAX-приложение <ArrowRight className="size-4" /></Link></div></div></section>
    <footer className="max-public-footer"><div><span>© 2026 Omnia · MAX Studio</span><nav><Link href="/about">О продукте</Link><Link href="/security">Безопасность</Link><Link href="/legal/terms">Условия</Link><Link href="/legal/privacy">Конфиденциальность</Link><Link href="/requisites">Реквизиты</Link></nav></div></footer>
  </main>;
}
