"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { Check, ChevronDown, Copy, Plus, Search, Settings } from "lucide-react";
import { YleumMark } from "@/components/brand/YleumMark";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button, type ButtonProps } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { MotionLab } from "./MotionLab";

const sections = [["foundations", "Основа"], ["controls", "Компоненты"], ["patterns", "Состояния"], ["motion", "Движение"]] as const;
const colors = [
  ["Фон", "--color-bg-base"], ["Поверхность", "--color-surface"], ["Вложенная поверхность", "--color-surface-3"],
  ["Граница", "--color-border-default"], ["Основной текст", "--color-fg-primary"], ["Вторичный текст", "--color-fg-secondary"],
  ["Акцент", "--color-accent"], ["Успех", "--color-success"], ["Ошибка", "--color-danger"], ["Предупреждение", "--color-warning"],
] as const;
const buttons: [ButtonProps["variant"], string][] = [["primary", "Основная"], ["secondary", "Вторичная"], ["outline", "Контурная"], ["ghost", "Тихая"], ["danger", "Удалить"], ["pill-primary", "Продолжить"], ["pill-secondary", "Подробнее"]];
const badges: [BadgeProps["variant"], string][] = [["default", "Черновик"], ["accent", "В работе"], ["success", "Опубликовано"], ["warning", "Нужно действие"], ["danger", "Ошибка"], ["outline", "MAX"]];

function Section({ id, title, description, source, children }: { id: string; title: string; description: string; source: string; children: ReactNode }) {
  return <section id={id} className="uk-section" aria-labelledby={`${id}-title`}>
    <div className="uk-section-heading"><h2 id={`${id}-title`}>{title}</h2><p>{description}</p><code>{source}</code></div>{children}
  </section>;
}

function FormExamples() {
  const [submitted, setSubmitted] = useState(false);
  return <form className="uk-form" onSubmit={event => { event.preventDefault(); setSubmitted(true); }} onChange={() => setSubmitted(false)}>
    <div><Label htmlFor="uk-name">Название приложения</Label><Input id="uk-name" name="name" placeholder="Например, студия керамики" required maxLength={120} /></div>
    <div><Label htmlFor="uk-description">Описание</Label><Textarea id="uk-description" placeholder="Что смогут делать ваши клиенты?" /></div>
    <div><Label htmlFor="uk-error">Адрес приложения · пример ошибки</Label><Input id="uk-error" defaultValue="моя студия" aria-invalid="true" aria-describedby="uk-error-help" /><p id="uk-error-help" className="uk-error">Используйте латинские буквы, цифры и дефис.</p></div>
    <div><Label htmlFor="uk-disabled">Недоступное поле</Label><Input id="uk-disabled" disabled value="Адрес появится после публикации" readOnly /></div>
    <div className="uk-inline"><Button type="submit">Проверить форму</Button><span className="uk-caption" role="status">{submitted ? "Форма проверена. Данные никуда не отправлены." : "Локальный пример, без отправки данных."}</span></div>
  </form>;
}

export function UiKit() {
  const [dark, setDark] = useState(false);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!root.current) return;
    const styles = getComputedStyle(root.current);
    setValues(Object.fromEntries(colors.map(([, token]) => [token, styles.getPropertyValue(token).trim()])));
  }, [dark]);
  const themeProps = dark ? { "data-product-shell": "" } : { "data-max-studio": "" };

  return <div ref={root} className="uk" {...themeProps}>
    <a className="uk-skip" href="#uk-content">К содержимому</a>
    <header className="uk-header"><Link href="/" className="uk-brand" aria-label="Yleum — главная"><YleumMark /><span>Yleum</span></Link><span className="uk-header-title">UI kit</span>
      <button type="button" className="uk-theme" aria-pressed={dark} onClick={() => setDark(value => !value)}>{dark ? "Тёмная тема" : "Светлая тема"}<span className="uk-theme-dot" /></button>
    </header>
    <div className="uk-layout">
      <aside className="uk-sidebar"><nav aria-label="Разделы UI kit">{sections.map(([id, title]) => <a key={id} href={`#${id}`}>{title}</a>)}</nav></aside>
      <main id="uk-content" className="uk-main">
        <div className="uk-intro"><h1>Знакомые детали.<br />Единый характер.</h1><p>Цвета, типографика и компоненты, которые уже живут в Yleum. Рядом — лаборатория спокойного движения для будущих интерфейсов.</p><a className="uk-text-link" href="#motion">Попробовать анимации <ChevronDown size={16} /></a></div>
        <Section id="foundations" title="Основа" description="Текущие цвета читаются из переменных темы. Переключите тему, чтобы сравнить обе палитры." source="app/globals.css · components/max/max-studio.css">
          <div className="uk-palette">{colors.map(([name, token]) => <div className="uk-swatch" key={token}><span className="uk-color" style={{ background: `var(${token})` }} /><strong>{name}</strong><code>{values[token] || token}</code><small>{token}</small></div>)}</div>
          <div className="uk-foundation-grid"><div><h3>Типографика</h3><p className="uk-type-display">Создайте своё приложение</p><p className="uk-type-section">Всё готово к следующему шагу</p><p>Inter — интерфейс и основной текст.</p><p className="uk-type-onest">Onest — заголовки витрины.</p><code>JetBrains Mono — код и значения</code><p className="uk-caption">Подпись 12 / метка 14 / текст 16 / раздел 20</p></div><div><h3>Форма и ритм</h3><div className="uk-radii">{["xs", "sm", "md", "lg", "xl"].map(size => <div key={size}><span data-radius={size} /><code>{size}</code></div>)}</div><p className="uk-caption">Радиусы из globals.css: 6, 8, 12, 14, 18 px.</p><div className="uk-spacing">{[4, 8, 12, 16, 24, 32].map(size => <div key={size}><span style={{ width: size }} /><code>{size}</code></div>)}</div><p className="uk-caption">Отступы кратны 4 px. Больше воздуха между смысловыми группами.</p></div></div>
        </Section>
        <Section id="controls" title="Компоненты" description="Те же React-компоненты, что используются в продукте, с их текущими вариантами и состояниями." source="components/ui/*">
          <div className="uk-specimen"><h3>Кнопки</h3><div className="uk-row">{buttons.map(([variant, label]) => <Button key={variant} variant={variant} onClick={() => setNotice(`Нажата кнопка «${label}». Это демонстрация.`)}>{label}</Button>)}</div><div className="uk-row"><Button size="sm" onClick={() => setNotice("Маленькая кнопка")}>Маленькая</Button><Button size="md" onClick={() => setNotice("Средняя кнопка")}>Средняя</Button><Button size="lg" onClick={() => setNotice("Большая кнопка")}>Большая</Button><Button size="xl" onClick={() => setNotice("Крупная кнопка")}>Крупная</Button><Button size="icon" variant="secondary" aria-label="Пример настроек" onClick={() => setNotice("Кнопка с иконкой")}><Settings /></Button><Button disabled>Недоступна</Button><Button aria-busy="true" disabled>Сохраняем…</Button></div><p className="uk-caption uk-feedback" role="status">{notice || "Нажмите на кнопку — здесь появится отклик."}</p></div>
          <div className="uk-specimen"><h3>Поля и подписи</h3><FormExamples /></div>
          <div className="uk-specimen"><h3>Метки и аватары</h3><div className="uk-row">{badges.map(([variant, label]) => <Badge key={variant} variant={variant}>{label}</Badge>)}<Avatar><AvatarFallback>ЮЛ</AvatarFallback></Avatar></div></div>
          <div className="uk-specimen"><h3>Диалог и меню</h3><div className="uk-row">
            <Dialog><DialogTrigger asChild><Button variant="secondary">Открыть диалог</Button></DialogTrigger><DialogContent {...themeProps}><DialogHeader><DialogTitle>Сохранить пример?</DialogTitle><DialogDescription>Это демонстрация диалога Yleum. Никакие данные приложения не изменятся.</DialogDescription></DialogHeader><DialogFooter><DialogClose asChild><Button variant="secondary">Отмена</Button></DialogClose><DialogClose asChild><Button onClick={() => setNotice("Пример подтверждён в диалоге.")}>Сохранить пример</Button></DialogClose></DialogFooter></DialogContent></Dialog>
            <DropdownMenu><DropdownMenuTrigger asChild><Button variant="secondary">Действия <ChevronDown /></Button></DropdownMenuTrigger><DropdownMenuContent {...themeProps}><DropdownMenuItem onSelect={() => setNotice("Выбрано: скопировать пример")}><Copy size={16} />Скопировать пример</DropdownMenuItem><DropdownMenuItem onSelect={() => setNotice("Выбрано: параметры примера")}><Settings size={16} />Параметры примера</DropdownMenuItem></DropdownMenuContent></DropdownMenu>
          </div></div>
        </Section>
        <Section id="patterns" title="Состояния" description="Пустой список, загрузка и готовый результат. Все данные ниже — демонстрационные." source="components/ui/skeleton.tsx · button.tsx · badge.tsx">
          <div className="uk-state-toolbar"><h3>Список приложений</h3><Button variant="outline" onClick={() => setLoading(value => !value)}>{loading ? "Показать результат" : "Показать загрузку"}</Button></div>
          <div className="uk-project-example" aria-busy={loading}>{loading ? <div className="uk-skeleton" role="status" aria-label="Пример загрузки"><Skeleton className="h-10 w-10" /><div><Skeleton className="h-4 w-40" /><Skeleton className="mt-3 h-3 w-24" /></div></div> : <><Avatar><AvatarFallback>К</AvatarFallback></Avatar><div><strong>Студия керамики</strong><p className="uk-caption">Демонстрационное приложение</p></div><Badge variant="success"><Check size={12} aria-hidden="true" /> Опубликовано</Badge></>}</div>
          <div className="uk-empty"><Search size={24} aria-hidden="true" /><h3>Здесь появятся ваши приложения</h3><p>Начните с идеи: каталог, запись на услуги или клуб клиентов.</p><Button variant="secondary" onClick={() => setNotice("Это пример пустого списка. Приложение не создаётся.")}><Plus />Создать пример</Button><span className="uk-caption" role="status">{notice.startsWith("Это пример") ? notice : "Без создания проекта и запросов к ИИ."}</span></div>
        </Section>
        <Section id="motion" title="Лаборатория движения" description="Предложения, пока только здесь. Эти анимации не подключены к рабочим экранам." source="components/ui-kit/motion-preview.ts · MotionLab.tsx">
          <MotionLab />
        </Section>
        <footer className="uk-footer">UI kit отражает текущий фронтенд. Новое движение остаётся в лаборатории до согласования.</footer>
      </main>
    </div>
  </div>;
}
