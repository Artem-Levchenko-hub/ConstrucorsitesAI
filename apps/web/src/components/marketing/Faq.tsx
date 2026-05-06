import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";

const QUESTIONS = [
  {
    q: "А чем вы лучше Wix или Tilda?",
    a: "У них нет AI-генерации с откатом. Ты пишешь промпт, получаешь готовый код, и любой шаг можно отменить как в Photoshop. Плюс мы храним сайт как git-репозиторий — захочешь, скачаешь и хостишь сам.",
  },
  {
    q: "Где хостится результат?",
    a: "На наших серверах в РФ (VPS Serverum.ru). Поддомен *.omnia.ai — сразу. Свой домен можно подключить за 5 минут.",
  },
  {
    q: "Какие модели доступны?",
    a: "Claude Sonnet 4.6 (Anthropic), GPT-4.1 (OpenAI), YandexGPT 5, Qwen-3-Coder. Можно переключать прямо в редакторе — каждый промпт независимо.",
  },
  {
    q: "Что с оплатой LLM-токенов?",
    a: "У тебя кошелёк в рублях. Каждый промпт списывает ровно столько, сколько стоят токены, плюс наша наценка 15%. Видно в реальном времени.",
  },
  {
    q: "Можно скачать исходники?",
    a: "Да. Любой проект экспортируется как zip с git-историей. Захочешь съехать — никаких блокировок, всё твоё.",
  },
  {
    q: "Когда полноценный запуск?",
    a: "Бета — сейчас (по приглашениям). Публичный запуск — октябрь 2026.",
  },
];

export function Faq() {
  // Schema.org FAQPage — gives Google/Yandex rich-result eligibility.
  const faqJsonLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: QUESTIONS.map((item) => ({
      "@type": "Question",
      name: item.q,
      acceptedAnswer: { "@type": "Answer", text: item.a },
    })),
  };

  return (
    <section id="faq" className="border-b border-border-subtle">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd) }}
      />
      <div className="mx-auto max-w-3xl px-6 py-24">
        <h2 className="text-3xl font-semibold tracking-tight mb-2">
          Частые вопросы
        </h2>
        <p className="text-fg-secondary mb-10">
          Если что-то не нашли — пишите{" "}
          <a
            href="mailto:hello@omnia.ai"
            className="text-accent hover:text-accent-hover transition"
          >
            hello@omnia.ai
          </a>
          .
        </p>
        <Accordion type="single" collapsible>
          {QUESTIONS.map((item, idx) => (
            <AccordionItem key={idx} value={`q-${idx}`}>
              <AccordionTrigger>{item.q}</AccordionTrigger>
              <AccordionContent className="leading-6">
                {item.a}
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </div>
    </section>
  );
}
