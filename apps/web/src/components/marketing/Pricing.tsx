import Link from "next/link";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Plan = {
  name: string;
  price: string;
  cadence: string;
  description: string;
  features: string[];
  cta: string;
  highlight?: boolean;
};

const PLANS: Plan[] = [
  {
    name: "Старт",
    price: "0 ₽",
    cadence: "навсегда",
    description: "Для первого знакомства и личных страниц.",
    features: [
      "100 ₽ на токены при регистрации",
      "До 3 проектов",
      "Поддомен *.omnia.ai",
      "Базовые модели (YandexGPT, GPT-4.1)",
    ],
    cta: "Попробовать",
  },
  {
    name: "Про",
    price: "990 ₽",
    cadence: "/ мес",
    description: "Для регулярной работы — фрилансеры, малый бизнес.",
    features: [
      "1 000 ₽ на токены ежемесячно",
      "Безлимит проектов",
      "Свой домен (привязка)",
      "Все модели, в т.ч. Claude Sonnet 4.6",
      "Приоритетный rendering превью",
    ],
    cta: "Перейти на Про",
    highlight: true,
  },
  {
    name: "Команда",
    price: "от 3 990 ₽",
    cadence: "/ мес",
    description: "Для агентств: несколько мест, общие проекты.",
    features: [
      "5 000 ₽ на токены, пополняемых",
      "До 10 пользователей",
      "Совместный доступ к проектам",
      "Брендинг ваш, не Omnia",
      "Приоритетная поддержка",
    ],
    cta: "Связаться",
  },
];

export function Pricing() {
  return (
    <section id="pricing" className="border-b border-border-subtle">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <div className="max-w-2xl mb-16">
          <h2 className="text-3xl font-semibold tracking-tight mb-4">
            Цены в рублях, без подписной ловушки
          </h2>
          <p className="text-fg-secondary">
            Платишь только за то, что сгенерировал. Старт — бесплатно: 100 ₽
            на счёте сразу.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          {PLANS.map((plan) => (
            <div
              key={plan.name}
              className={cn(
                "rounded-lg border bg-surface-raised p-6 space-y-5 flex flex-col",
                plan.highlight
                  ? "border-accent shadow-md"
                  : "border-border-default",
              )}
            >
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-medium">{plan.name}</h3>
                {plan.highlight && (
                  <Badge variant="accent">Популярный</Badge>
                )}
              </div>
              <div>
                <div className="text-3xl font-semibold tracking-tight">
                  {plan.price}
                  <span className="text-sm font-normal text-fg-secondary ml-1.5">
                    {plan.cadence}
                  </span>
                </div>
                <p className="text-sm text-fg-secondary mt-2">
                  {plan.description}
                </p>
              </div>
              <ul className="space-y-2 flex-1">
                {plan.features.map((f) => (
                  <li
                    key={f}
                    className="flex items-start gap-2 text-sm text-fg-primary"
                  >
                    <Check className="h-4 w-4 text-accent mt-0.5 shrink-0" />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
              <Button
                asChild
                variant={plan.highlight ? "primary" : "secondary"}
                className="w-full"
              >
                <Link href="/register">{plan.cta}</Link>
              </Button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
