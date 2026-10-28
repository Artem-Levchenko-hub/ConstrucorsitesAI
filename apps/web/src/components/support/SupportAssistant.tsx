"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bot, Send } from "lucide-react";
import { findSupportAnswers, supportAnswers, supportTopics, type SupportAnswer, type SupportTopic } from "@/lib/support-faq";

type Exchange = { question: string; answers: readonly SupportAnswer[] };

export function SupportAssistant({ onContact }: { onContact: (question: string) => void }) {
  const [topic, setTopic] = useState<SupportTopic>("account");
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const log = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [exchanges]);

  function answer(value: string, selected?: SupportAnswer) {
    const text = value.trim().slice(0, 500);
    if (!text) return;
    setExchanges(previous => [...previous.slice(-2), { question: text, answers: selected ? [selected] : findSupportAnswers(text) }]);
    setQuestion("");
  }

  return <section className="support-assistant" aria-label="Автопомощник Yleum">
    <div className="support-assistant-intro">
      <span className="support-assistant-avatar" aria-hidden="true"><Bot size={22} /></span>
      <div><h3>Здравствуйте! Я помощник Yleum</h3><p>Подскажу по частым вопросам. Выберите тему или напишите свой вопрос.</p></div>
    </div>
    <div className="support-topics" aria-label="Темы помощи">{supportTopics.map(item =>
      <button type="button" key={item.id} aria-pressed={topic === item.id} onClick={() => setTopic(item.id)}>{item.title}</button>,
    )}</div>
    <div className="support-faq-questions" aria-label="Частые вопросы">{supportAnswers.filter(item => item.topic === topic).map(item =>
      <button type="button" key={item.id} onClick={() => answer(item.question, item)}>{item.question}<span aria-hidden="true">↗</span></button>,
    )}</div>
    {exchanges.length > 0 && <ol ref={log} className="support-bot-log" role="log" aria-label="Ответы автопомощника" aria-live="polite" aria-relevant="additions text">
      {exchanges.map((exchange, index) => <li key={index}>
        <p className="support-bot-question"><span className="support-meta">Вы</span>{exchange.question}</p>
        <div className="support-bot-answer"><strong>Автопомощник</strong>
          {exchange.answers.length === 1 ? <>
            {exchange.answers[0].paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
            {exchange.answers[0].links && <div className="support-bot-links">{exchange.answers[0].links.map(link => <Link key={link.href} href={link.href} prefetch={false}>{link.label}</Link>)}</div>}
          </> : exchange.answers.length > 1 ? <><p>Нашёл несколько подходящих вопросов. Выберите, какой ближе к вашему:</p>
            {exchange.answers.map(item => <button type="button" key={item.id} onClick={() => answer(exchange.question, item)}>{item.question}</button>)}
          </> : <p>В готовых ответах пока нет точного совпадения. Напишите оператору — он сможет разобраться в вашем случае.</p>}
        </div>
      </li>)}
    </ol>}
    <form className="support-bot-form" onSubmit={event => { event.preventDefault(); answer(question); }}>
      <label htmlFor="support-bot-question" className="support-meta">Ваш вопрос</label>
      <div><input id="support-bot-question" aria-label="Вопрос автопомощнику" maxLength={500} value={question} onChange={event => setQuestion(event.target.value)} placeholder="Например, не приходит код" /><button className="support-primary" type="submit" disabled={!question.trim()}><Send size={16} aria-hidden="true" /><span>Спросить</span></button></div>
      <p className="support-meta">Не отправляйте пароли, коды подтверждения и данные карты.</p>
    </form>
    <button type="button" className="support-back" onClick={() => onContact(question.trim() || exchanges.at(-1)?.question || "")}>Не помогло — передать вопрос оператору</button>
  </section>;
}
