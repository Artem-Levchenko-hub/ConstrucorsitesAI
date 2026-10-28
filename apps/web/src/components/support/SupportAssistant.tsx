"use client";

import { useEffect, useId, useRef, useState, type ComponentType } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowUp, BookOpen, Check, ChevronRight, CircleHelp, Copy, CreditCard, Headphones, MessageSquarePlus, Rocket, ShieldCheck, ThumbsDown, ThumbsUp, WandSparkles } from "lucide-react";
import { YleumMark } from "@/components/brand/YleumMark";
import { useSupportAssistant, type SupportAssistantSession, type SupportExchange } from "@/hooks/useSupportAssistant";
import { relatedSupportAnswers, searchSupportAnswers, supportAnswers, supportTopics, type SupportAnswer, type SupportTopic } from "@/lib/support-faq";

type Props = { onContact: (question: string) => void; session?: SupportAssistantSession };
const topicDetails: Record<SupportTopic, { icon: ComponentType<{ size?: number }>; caption: string }> = {
  account: { icon: ShieldCheck, caption: "Доступ и подтверждение" },
  billing: { icon: CreditCard, caption: "Платежи и подписка" },
  generation: { icon: WandSparkles, caption: "Создание и изменения" },
  publication: { icon: Rocket, caption: "От проверки до запуска" },
  operator: { icon: Headphones, caption: "Помощь в вашем случае" },
};

export function SupportAssistant({ session, ...props }: Props) {
  return session ? <AssistantContent {...props} session={session} /> : <StandaloneAssistant {...props} />;
}
function StandaloneAssistant(props: Omit<Props, "session">) {
  const session = useSupportAssistant();
  return <AssistantContent {...props} session={session} />;
}
function AnswerLinks({ answer }: { answer: SupportAnswer }) {
  return answer.links ? <div className="support-bot-links">{answer.links.map(link =>
    <Link key={link.href} href={link.href} prefetch={false}>{link.label}<ChevronRight size={14} aria-hidden="true" /></Link>,
  )}</div> : null;
}

function AssistantContent({ session: s, onContact }: Required<Props>) {
  const body = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const suggestionsRef = useRef<HTMLDivElement>(null);
  const suggestionId = useId();
  const [copied, setCopied] = useState<number | null>(null);
  const [copyFailed, setCopyFailed] = useState<number | null>(null);
  const suggestions = searchSupportAnswers(s.question);
  const currentTopic = supportTopics.find(item => item.id === s.topic);
  const lastExchangeId = s.exchanges.at(-1)?.id;
  useEffect(() => {
    const container = body.current;
    if (!container) return;
    if (s.browsing) { container.scrollTop = 0; return; }
    // Instant long answers should start at their question, not skip to the end.
    const latest = container.querySelector<HTMLElement>(".support-exchange:last-child");
    if (latest) container.scrollTop += latest.getBoundingClientRect().top - container.getBoundingClientRect().top - 12;
  }, [lastExchangeId, s.browsing, s.topic]);
  useEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = Math.max(44, Math.min(input.current.scrollHeight, 104)) + "px";
  }, [s.question]);

  async function copyAnswer(exchange: SupportExchange) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(exchange.answers[0].paragraphs.join("\n\n"));
      setCopied(exchange.id); setCopyFailed(null);
    } catch { setCopyFailed(exchange.id); }
  }
  function choose(answer: SupportAnswer, question = answer.question) {
    s.ask(question, answer); input.current?.focus();
  }
  function contact() { onContact(s.question.trim() || s.exchanges.at(-1)?.question || ""); }

  return <section className="support-assistant" aria-label="Автопомощник Yleum">
    <div ref={body} className="support-assistant-body" tabIndex={0} aria-label="Справка и диалог">
      {s.browsing ? <>
        {currentTopic ? <div className="support-topic-heading">
          <button type="button" className="support-text-button" onClick={() => s.browse()}><ArrowLeft size={15} aria-hidden="true" />Все темы</button>
          <h3>{currentTopic.title}</h3><p>{topicDetails[currentTopic.id].caption}</p>
        </div> : <>
          <div className="support-welcome"><span className="support-welcome-label"><span />ПОМОЩЬ РЯДОМ</span><h3>С чем вам помочь?</h3><p>Ответы на частые вопросы и помощь команды.</p></div>
          <div className="support-topics" aria-label="Темы помощи">{supportTopics.map(topic => {
            const detail = topicDetails[topic.id]; const Icon = detail.icon;
            return <button type="button" key={topic.id} aria-label={topic.title} onClick={() => s.browse(topic.id)}>
              <span className="support-topic-icon"><Icon size={19} /></span><span><strong>{topic.title}</strong><small>{detail.caption}</small></span><ChevronRight size={15} aria-hidden="true" />
            </button>;
          })}</div>
          <div className="support-section-label"><CircleHelp size={14} aria-hidden="true" />Часто спрашивают</div>
        </>}
        <div className="support-faq-questions" aria-label="Частые вопросы">{(currentTopic ? supportAnswers.filter(item => item.topic === currentTopic.id) : supportAnswers.filter(item => ["verification", "generation-error", "publish"].includes(item.id))).map(answer =>
          <button type="button" key={answer.id} onClick={() => choose(answer)}>{answer.question}<ChevronRight size={16} aria-hidden="true" /></button>,
        )}</div>
        {s.exchanges.length > 0 && <button type="button" className="support-return-chat" onClick={() => s.setBrowsing(false)}>Вернуться к диалогу<ChevronRight size={15} aria-hidden="true" /></button>}
      </> : <ol className="support-bot-log" role="log" aria-label="Ответы автопомощника" aria-live="polite" aria-relevant="additions text">
        {s.exchanges[0]?.id > 1 && s.exchanges.length === 20 && <li className="support-meta">Показаны последние 20 вопросов.</li>}
        {s.exchanges.map(exchange => <li className="support-exchange" key={exchange.id}>
          <p className="support-bot-question">{exchange.question}</p>
          <div className="support-answer-heading"><span><YleumMark tone="mono" /></span>Помощник Yleum<small>По справке</small></div>
          <div className="support-bot-answer">
            {exchange.resolution === "answer" ? <>
              {exchange.answers[0].paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
              <AnswerLinks answer={exchange.answers[0]} />
            </> : exchange.resolution === "choices" ? <><p>Нашёл похожие вопросы. Выберите, что ближе к вашему:</p>
              {exchange.answers.map(answer => <button className="support-answer-choice" type="button" key={answer.id} onClick={() => choose(answer, exchange.question)}>{answer.question}<ChevronRight size={14} aria-hidden="true" /></button>)}
            </> : <><strong>Здесь лучше поможет человек</strong><p>В готовых ответах пока нет точного совпадения. Можно уточнить вопрос или передать его оператору — текст не придётся набирать заново.</p></>}
          </div>
          {exchange.resolution === "answer" && <div className="support-answer-tools">
            <span>Это помогло?</span>
            <button type="button" aria-label="Ответ помог" aria-pressed={exchange.feedback === "yes"} onClick={() => s.feedback(exchange.id, "yes")}><ThumbsUp size={14} aria-hidden="true" /></button>
            <button type="button" aria-label="Ответ не помог" aria-pressed={exchange.feedback === "no"} onClick={() => s.feedback(exchange.id, "no")}><ThumbsDown size={14} aria-hidden="true" /></button>
            <button type="button" aria-label="Скопировать ответ" title="Скопировать ответ" onClick={() => void copyAnswer(exchange)}>{copied === exchange.id ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}</button>
          </div>}
          {exchange.feedback === "yes" && <p className="support-feedback" role="status"><Check size={14} aria-hidden="true" />Рады помочь. Можно задать следующий вопрос.</p>}
          {exchange.feedback === "no" && <div className="support-feedback" role="status">Давайте подключим команду поддержки.<button type="button" onClick={() => onContact(exchange.question)}>Написать по этому вопросу<ChevronRight size={14} aria-hidden="true" /></button></div>}
          {copyFailed === exchange.id && <p className="support-meta" role="status">Не удалось скопировать. Можно выделить текст ответа.</p>}
          {copied === exchange.id && <span className="sr-only" role="status">Ответ скопирован</span>}
          {exchange.id === lastExchangeId && exchange.resolution === "answer" && relatedSupportAnswers(exchange.answers[0]).length > 0 && <div className="support-related" aria-label="По этой теме">
            <span>Может пригодиться</span>{relatedSupportAnswers(exchange.answers[0]).map(answer => <button type="button" key={answer.id} onClick={() => choose(answer)}>{answer.question}<ChevronRight size={14} aria-hidden="true" /></button>)}
          </div>}
        </li>)}
      </ol>}
    </div>
    <div className="support-composer">
      {suggestions.length > 0 && <div ref={suggestionsRef} id={suggestionId} className="support-suggestions" aria-label="Подходящие вопросы">
        <span>Возможно, вы ищете</span>{suggestions.map(answer => <button type="button" key={answer.id} onClick={() => choose(answer)} onKeyDown={event => {
          if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); input.current?.focus(); }
        }}><BookOpen size={14} aria-hidden="true" />{answer.question}<ChevronRight size={14} aria-hidden="true" /></button>)}
      </div>}
      <form className="support-bot-form" onSubmit={event => { event.preventDefault(); s.ask(s.question); input.current?.focus(); }}>
        <label htmlFor={suggestionId + "-question"} className="sr-only">Вопрос автопомощнику</label>
        <div><textarea ref={input} id={suggestionId + "-question"} aria-label="Вопрос автопомощнику" aria-controls={suggestions.length ? suggestionId : undefined} rows={1} maxLength={500} value={s.question} onChange={event => s.setQuestion(event.target.value)} placeholder="Напишите, с чем помочь…" onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); s.ask(s.question); }
          if (event.key === "ArrowUp" && suggestions.length > 0 && event.currentTarget.selectionStart === 0 && event.currentTarget.selectionEnd === 0) { event.preventDefault(); suggestionsRef.current?.querySelector("button")?.focus(); }
        }} /><button type="submit" aria-label="Спросить" title="Отправить вопрос" disabled={!s.question.trim()}><ArrowUp size={19} aria-hidden="true" /></button></div>
      </form>
      <div className="support-bot-tools"><button type="button" onClick={() => s.browse()}><BookOpen size={15} aria-hidden="true" />Все темы</button><button type="button" aria-label="Новый диалог" title="Новый диалог" onClick={() => { s.restart(); input.current?.focus(); }}><MessageSquarePlus size={16} aria-hidden="true" /></button><button type="button" aria-label="Не помогло — передать вопрос оператору" onClick={contact}><Headphones size={15} aria-hidden="true" />Позвать оператора</button></div>
      <p className="support-composer-note">Не отправляйте пароли, коды и данные карты.</p>
    </div>
  </section>;
}
