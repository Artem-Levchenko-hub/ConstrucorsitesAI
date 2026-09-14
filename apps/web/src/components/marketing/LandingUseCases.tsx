import Image from "next/image";
import { ArrowRight, Check, ChevronLeft, MoreHorizontal } from "lucide-react";

function AppHeader({ name, section }: { name: string; section: string }) {
  return <div className="ml-example-header"><ChevronLeft size={16}/><div><strong>{name}</strong><span>{section}</span></div><MoreHorizontal size={18}/></div>;
}

export function LandingUseCases() {
  return <section id="examples" className="ml-wrap ml-section" data-marketing-section="examples">
    <div className="ml-section-head">
      <p className="ml-eyebrow">ПРИМЕРЫ ПРИЛОЖЕНИЙ</p>
      <h2>Какое приложение<br/>можно создать?</h2>
      <p>Вот три примера того, что ваши клиенты смогут делать прямо внутри MAX.</p>
    </div>
    <div className="ml-scenarios-grid">
      <article className="ml-scenario">
        <div className="ml-scenario-copy"><h3>Кофейня</h3><p>Клиент выбирает напиток и заказывает его к нужному времени.</p></div>
        <figure className="ml-example-screen" role="img" aria-label="Пример приложения кофейни: капучино 250 мл за 240 рублей, самовывоз в 10:30 и кнопка оформления заказа.">
          <div aria-hidden="true" className="ml-example-screen-inner">
            <AppHeader name="Смена" section="Кофейня"/>
            <div className="ml-example-body">
              <span className="ml-example-step">1. Выберите напиток</span>
              <div className="ml-example-product"><Image src="/landing/cappuccino.jpg" width={88} height={88} alt="" sizes="88px"/><div><strong>Капучино</strong><span>250 мл · на обычном молоке</span><b>240 ₽</b></div></div>
              <div className="ml-example-line"><span>В заказе</span><strong>1 капучино</strong></div>
              <span className="ml-example-step">2. Когда заберёте?</span>
              <div className="ml-example-options"><span>Сейчас</span><span className="is-selected">10:30 <Check size={14}/></span><span>11:00</span></div>
              <p className="ml-example-note">Самовывоз · ул. Садовая, 12</p>
            </div>
            <div className="ml-example-bottom"><div className="ml-example-action">Заказать к 10:30 <span>240 ₽ <ArrowRight size={16}/></span></div></div>
          </div>
        </figure>
        <p className="ml-scenario-result"><Check size={18}/><span>Гость оформляет заказ без переписки.</span></p>
      </article>
      <article className="ml-scenario ml-scenario--salon">
        <div className="ml-scenario-copy"><h3>Салон красоты</h3><p>Клиент выбирает услугу, мастера и свободное время для записи.</p></div>
        <figure className="ml-example-screen" role="img" aria-label="Пример приложения салона: стрижка у мастера Анны, 60 минут за 1800 рублей, выбор времени и кнопка записи на 12:30.">
          <div aria-hidden="true" className="ml-example-screen-inner">
            <AppHeader name="Форма" section="Студия красоты"/>
            <div className="ml-example-body">
              <span className="ml-example-step">1. Услуга и мастер</span>
              <div className="ml-example-service"><strong>Стрижка</strong><span>60 минут <b>1 800 ₽</b></span></div>
              <div className="ml-example-line"><span>Ваш мастер</span><strong>Анна <Check size={14}/></strong></div>
              <span className="ml-example-step">2. Свободное время</span>
              <p className="ml-example-date">15 сентября</p>
              <div className="ml-example-options"><span>10:00</span><span className="is-selected">12:30 <Check size={14}/></span><span>14:00</span></div>
              <p className="ml-example-note">Подтверждение записи придёт в MAX</p>
            </div>
            <div className="ml-example-bottom"><div className="ml-example-action">Записаться на 12:30 <ArrowRight size={16}/></div></div>
          </div>
        </figure>
        <p className="ml-scenario-result"><Check size={18}/><span>Мастер получает запись, а не вопросы о времени.</span></p>
      </article>
      <article className="ml-scenario ml-scenario--club">
        <div className="ml-scenario-copy"><h3>Клуб по интересам</h3><p>Участник находит встречу, смотрит программу и записывается.</p></div>
        <figure className="ml-example-screen" role="img" aria-label="Пример приложения книжного клуба: встреча 20 сентября в 19:00, программа обсуждения и кнопка участия.">
          <div aria-hidden="true" className="ml-example-screen-inner">
            <AppHeader name="Между строк" section="Книжный клуб"/>
            <div className="ml-example-body">
              <span className="ml-example-step">Ближайшая встреча</span>
              <div className="ml-example-event"><span>20 СЕНТЯБРЯ · 19:00</span><strong>Обсуждаем<br/>«Маленького принца»</strong><p>Онлайн · 60 минут</p></div>
              <span className="ml-example-step">Что будет на встрече</span>
              <ul className="ml-example-agenda"><li>Обсудим героев и любимые цитаты</li><li>Выберем книгу на следующий месяц</li></ul>
            </div>
            <div className="ml-example-bottom"><div className="ml-example-action">Участвовать во встрече <ArrowRight size={16}/></div></div>
          </div>
        </figure>
        <p className="ml-scenario-result"><Check size={18}/><span>Программа и участие не теряются в общем чате.</span></p>
      </article>
    </div>
    <p className="ml-examples-caption">Примеры экранов, не действующие приложения. Содержание и функции вы задаёте для своего бизнеса.</p>
  </section>;
}
