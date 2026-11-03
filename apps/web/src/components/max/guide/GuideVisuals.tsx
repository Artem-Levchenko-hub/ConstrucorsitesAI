import Image from "next/image";
import { ArrowRight, Bot, Eye, FileCheck2, Rocket, Smartphone } from "lucide-react";

import { guideScreenshots, type GuideScreenshotData } from "./guide-screenshots";
import "./GuideVisuals.css";

export const guideColors = {
  ink: "#14141a",
  paper: "#ffffff",
  canvas: "#f9f8f6",
  line: "#e3e0dc",
  accent: "#0062ee",
  muted: "#7d7d87",
  success: "#187443",
};

function ScreenshotAnnotations({ screenshot }: { screenshot: GuideScreenshotData }) {
  const { width, height, callouts } = screenshot;
  const badgeRadius = width * 0.022;
  const strokeWidth = width * 0.0024;
  const arrowheadLength = width * 0.014;

  return (
    <svg
      className="guide-screenshot__overlay"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
      focusable="false"
    >
      {callouts.map((callout, index) => {
        const x = (callout.x / 100) * width;
        const y = (callout.y / 100) * height;
        const targetX = (callout.targetX / 100) * width;
        const targetY = (callout.targetY / 100) * height;
        const deltaX = targetX - x;
        const deltaY = targetY - y;
        const distance = Math.hypot(deltaX, deltaY) || 1;
        const headX = (deltaX / distance) * arrowheadLength;
        const headY = (deltaY / distance) * arrowheadLength;
        const arrow = [
          `M ${x} ${y} L ${targetX} ${targetY}`,
          `M ${targetX - headX + headY * 0.6} ${targetY - headY - headX * 0.6}`,
          `L ${targetX} ${targetY}`,
          `L ${targetX - headX - headY * 0.6} ${targetY - headY + headX * 0.6}`,
        ].join(" ");

        return (
          <g key={index + 1}>
            <path
              d={arrow}
              fill="none"
              stroke="#fff"
              strokeWidth={strokeWidth * 2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d={arrow}
              fill="none"
              stroke={guideColors.accent}
              strokeWidth={strokeWidth}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle
              cx={x}
              cy={y}
              r={badgeRadius}
              fill={guideColors.accent}
              stroke="#fff"
              strokeWidth={strokeWidth}
            />
            <text
              x={x}
              y={y}
              fill="#fff"
              textAnchor="middle"
              dominantBaseline="central"
              fontFamily="system-ui, sans-serif"
              fontSize={width * 0.026}
              fontWeight="700"
            >
              {index + 1}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function GuideScreenshot({ id }: { id: string }) {
  if (!Object.hasOwn(guideScreenshots, id)) {
    throw new Error(`Unknown guide screenshot: ${id}`);
  }

  const screenshot = guideScreenshots[id];
  const capturedDate = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "Europe/Moscow",
  }).format(new Date(screenshot.capturedAt));

  return (
    <figure className="guide-screenshot" data-guide-screenshot={id} aria-labelledby={`guide-${id}-title`} aria-describedby={`guide-${id}-source`}>
      <div className="guide-screenshot__media">
        <div className="guide-screenshot__image">
          <Image
            className="guide-screenshot__photo"
            src={screenshot.src}
            width={screenshot.width}
            height={screenshot.height}
            alt={screenshot.alt}
            unoptimized
          />
          <ScreenshotAnnotations screenshot={screenshot} />
        </div>
      </div>
      <figcaption className="guide-screenshot__caption">
        <div className="guide-screenshot__heading">
          <h3 className="guide-screenshot__title" id={`guide-${id}-title`}>{screenshot.title}</h3>
          <a
            className="guide-screenshot__original"
            href={screenshot.src}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Открыть в полном размере: ${screenshot.title}`}
          >
            Открыть в полном размере <span aria-hidden="true">↗</span>
          </a>
        </div>
        <ol className="guide-screenshot__callouts" aria-label="Действия на снимке" role="list">
          {screenshot.callouts.map((callout, index) => (
            <li key={index + 1}>
              <span className="guide-screenshot__number" aria-hidden="true">{index + 1}</span>
              <span>{callout.text}</span>
            </li>
          ))}
        </ol>
        <p className="guide-screenshot__source" id={`guide-${id}-source`}>
          Источник: <a href={screenshot.sourceUrl} target="_blank" rel="noopener noreferrer">{screenshot.sourceLabel}</a>
          {" · Снято "}<time dateTime={screenshot.capturedAt}>{capturedDate}</time>
          {" · Реальные данные скрыты"}
        </p>
      </figcaption>
    </figure>
  );
}

export function ProjectCreationVisual() {
  return <GuideScreenshot id="create" />;
}

export function BuilderVisual() {
  return <GuideScreenshot id="builder" />;
}

export function IntegrationVisual() {
  return <GuideScreenshot id="integrations" />;
}

export function LaunchVisual() {
  return <GuideScreenshot id="publish" />;
}

export function PartnerVisual() {
  return <GuideScreenshot id="max-settings" />;
}

export function DashboardVisual() {
  return <GuideScreenshot id="management" />;
}

const journeySteps = [
  { title: "Создайте проект", text: "Опишите идею и главное действие пользователя.", href: "#project", icon: Smartphone },
  { title: "Проверьте в редакторе", text: "Пройдите путь клиента в мобильном превью.", href: "#builder", icon: Eye },
  { title: "Подготовьте данные", text: "Заполните каталог и проверьте документы.", href: "#settings", icon: FileCheck2 },
  { title: "Подключите бота", text: "Настройте безопасный вход через MAX.", href: "#max-bot", icon: Bot },
  { title: "Опубликуйте", text: "Получите постоянную ссылку на приложение.", href: "#publish", icon: Rocket },
  { title: "Откройте в MAX", text: "Добавьте ссылку в MAX Partner и проверьте запуск.", href: "#acceptance", icon: Smartphone },
] as const;

export function GoldenPathVisual() {
  return (
    <nav className="guide-journey" aria-label="Путь от проекта до запуска" data-learning="guide-journey">
      <div className="guide-journey__heading">
        <div><p>Карта запуска</p><h2>От проекта до запуска в MAX</h2></div>
        <span>Выберите этап, чтобы перейти к инструкции.</span>
      </div>
      <ol className="guide-path" role="list">
        {journeySteps.map((step, index) => {
          const Icon = step.icon;
          return <li key={step.href}>
            <a href={step.href}>
              <span className="guide-path__top"><Icon size={21} aria-hidden="true" /><span>0{index + 1}</span></span>
              <strong>{step.title}</strong>
              <span className="guide-path__description">{step.text}</span>
              <ArrowRight className="guide-path__arrow" size={17} aria-hidden="true" />
            </a>
          </li>;
        })}
      </ol>
    </nav>
  );
}
