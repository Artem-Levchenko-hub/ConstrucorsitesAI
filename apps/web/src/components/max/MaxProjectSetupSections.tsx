"use client";

import { useId, type ReactNode } from "react";
import { Check, PackageOpen, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { MaxProjectConfigPayload } from "@/lib/api/types";
import { MAX_BRIEF_LENGTH } from "@/lib/max-brief";

export type SetupSection = "details" | "content" | "owner" | "policies";

const CHECKS = [
  { key: "has_sales", label: "Продажи или оплата", description: "Условия заказа, цены, отмены и возврата." },
  { key: "has_user_content", label: "Пользовательский контент", description: "Правила публикаций, жалоб и модерации." },
  { key: "marketing_notifications", label: "Маркетинговые уведомления", description: "Явное согласие на рассылку и возможность отписаться." },
] as const;

function Group({ title, children, description }: { title: string; children: ReactNode; description?: string }) {
  return <fieldset className="max-setup-group">
    <legend>{title}</legend>
    {description && <p className="max-setup-group-note">{description}</p>}
    <div className="max-setup-grid">{children}</div>
  </fieldset>;
}

function Intro({ title, children, publication = false }: { title: string; children: ReactNode; publication?: boolean }) {
  return <div className="max-setup-intro"><span className="max-setup-purpose" data-publication={publication}>{publication ? "Перед публичным запуском" : "Необязательно для запуска"}</span><h3>{title}</h3><p>{children}</p></div>;
}

export function MaxProjectSetupSections({ section, current, onChange }: {
  section: SetupSection;
  current: MaxProjectConfigPayload;
  onChange: (value: MaxProjectConfigPayload) => void;
}) {
  const contentId = useId();
  const summaryLength = Array.from(current.summary.trim()).length;
  const updateContent = (index: number, patch: Partial<MaxProjectConfigPayload["content"][number]>) => {
    const content = [...current.content];
    content[index] = { ...content[index], ...patch };
    onChange({ ...current, content });
  };

  if (section === "details") return <section className="max-setup-section">
    <Intro title="Описание и оформление">Здесь сохранена идея вашего приложения — заполнять её заново не нужно. Уточняйте аудиторию, функции и стиль только если хотите изменить результат.</Intro>
    <Group title="О приложении">
      <div className="max-setup-field">
        <Label htmlFor="max-config-name">Название</Label>
        <Input id="max-config-name" value={current.app_name} maxLength={100} placeholder="Например, Кофе рядом"
          onChange={event => onChange({ ...current, app_name: event.target.value })} />
      </div>
      <div className="max-setup-field">
        <Label htmlFor="max-config-action">Главное действие</Label>
        <Input id="max-config-action" value={current.primary_action} maxLength={200} placeholder="Например, оформить заказ"
          onChange={event => onChange({ ...current, primary_action: event.target.value })} />
      </div>
      <div className="max-setup-field max-setup-wide">
        <Label htmlFor="max-config-summary">Описание сервиса</Label>
        <Textarea id="max-config-summary" value={current.summary} aria-describedby="max-config-summary-limit"
          aria-invalid={summaryLength > MAX_BRIEF_LENGTH} placeholder="Для кого приложение и какую задачу оно решает?"
          onChange={event => onChange({ ...current, summary: event.target.value })} />
        <p id="max-config-summary-limit" className="max-setup-hint" data-invalid={summaryLength > MAX_BRIEF_LENGTH}>
          {summaryLength.toLocaleString("ru-RU")} / {MAX_BRIEF_LENGTH.toLocaleString("ru-RU")} символов
          {summaryLength > MAX_BRIEF_LENGTH && " · Сократите описание перед сохранением — текст не обрезан."}
        </p>
      </div>
      <div className="max-setup-field">
        <Label htmlFor="max-config-type">Тип приложения</Label>
        <select id="max-config-type" value={current.app_type} onChange={event => onChange({ ...current, app_type: event.target.value as MaxProjectConfigPayload["app_type"] })}>
          <option value="loyalty">Лояльность</option><option value="catalog">Каталог и заказы</option>
          <option value="booking">Запись и бронирование</option><option value="event">Событие</option>
          <option value="education">Обучение</option><option value="custom">Свой сценарий</option>
        </select>
      </div>
      <div className="max-setup-field">
        <Label htmlFor="max-config-audience">Аудитория</Label>
        <Input id="max-config-audience" value={current.audience} placeholder="Например, гости кофейни"
          onChange={event => onChange({ ...current, audience: event.target.value })} />
      </div>
      <div className="max-setup-field max-setup-wide">
        <Label htmlFor="max-config-features">Возможности приложения</Label>
        <Textarea id="max-config-features" className="max-setup-short-textarea" value={current.features.join(", ")}
          placeholder="Заказы, бонусы, запись, уведомления" aria-describedby="max-config-features-hint"
          onChange={event => onChange({ ...current, features: event.target.value.split(",").map(feature => feature.trim()).filter(Boolean).slice(0, 24) })} />
        <p id="max-config-features-hint" className="max-setup-hint">До 24 функций, через запятую.</p>
      </div>
    </Group>
    <Group title="Оформление" description="Можно оставить текущий стиль. Дополнительные цвета не нужны для публикации.">
      <div className="max-setup-field">
        <Label htmlFor="max-config-colors">Цвета бренда</Label>
        <Input id="max-config-colors" value={current.brand_colors} placeholder="#4f81f7, #121519"
          onChange={event => onChange({ ...current, brand_colors: event.target.value })} />
      </div>
      <div className="max-setup-field">
        <Label htmlFor="max-config-style">Визуальный стиль</Label>
        <select id="max-config-style" value={current.style} onChange={event => onChange({ ...current, style: event.target.value as MaxProjectConfigPayload["style"] })}>
          <option value="brand">В цветах бренда</option><option value="clean">Чистый и спокойный</option><option value="bright">Яркий и акцентный</option>
        </select>
      </div>
    </Group>
  </section>;

  if (section === "content") return <section className="max-setup-section">
    <div className="max-setup-content-heading">
      <Intro title="Каталог и контент">Товары, услуги, события или уроки. Сохранение добавляет их в данные; «Применить к приложению» свяжет каталог с экранами.</Intro>
      <Button size="sm" className="max-setup-add" onClick={() => onChange({ ...current, content: [...current.content, {
        id: `item-${Date.now()}`, title: "Новый элемент", description: "", price: "", action_label: "Открыть", active: true,
      }] })}><Plus aria-hidden="true" className="h-4 w-4" />Добавить элемент</Button>
    </div>
    {current.content.length === 0 ? <div className="max-setup-empty">
      <PackageOpen aria-hidden="true" />
      <h4>Пока нет элементов</h4>
      <p>Добавьте первый товар, услугу или урок. Если каталог не нужен, этот раздел можно оставить пустым.</p>
    </div> : <div className="max-setup-content-list">
      <p className="max-setup-hint">Элементов: {current.content.length} · Видимость каждого можно настроить отдельно.</p>
      {current.content.map((item, index) => <fieldset key={item.id} className="max-setup-content-item">
        <legend>Элемент {index + 1}</legend>
        <div className="max-setup-item-toolbar">
          <button type="button" role="switch" aria-checked={item.active} aria-label={`Показывать элемент ${index + 1}`}
            className="max-setup-visibility" onClick={() => updateContent(index, { active: !item.active })}>
            <span className="max-setup-switch-track" aria-hidden="true"><span /></span>
            {item.active ? "Показан в приложении" : "Скрыт из приложения"}
          </button>
          <button type="button" className="max-setup-delete" aria-label={`Удалить ${item.title}`}
            onClick={() => onChange({ ...current, content: current.content.filter((_, itemIndex) => itemIndex !== index) })}>
            <Trash2 className="h-4 w-4" aria-hidden="true" /><span>Удалить</span>
          </button>
        </div>
        <div className="max-setup-grid">
          <div className="max-setup-field">
            <Label htmlFor={`${contentId}-title-${index}`}>Название</Label>
            <Input id={`${contentId}-title-${index}`} aria-label={`Название элемента ${index + 1}`} value={item.title}
              onChange={event => updateContent(index, { title: event.target.value })} />
          </div>
          <div className="max-setup-field">
            <Label htmlFor={`${contentId}-price-${index}`}>Цена или подпись</Label>
            <Input id={`${contentId}-price-${index}`} aria-label={`Цена или подпись элемента ${index + 1}`} value={item.price} maxLength={80}
              placeholder="Например, 250 ₽" onChange={event => updateContent(index, { price: event.target.value })} />
          </div>
          <div className="max-setup-field max-setup-wide">
            <Label htmlFor={`${contentId}-description-${index}`}>Описание</Label>
            <Textarea id={`${contentId}-description-${index}`} aria-label={`Описание элемента ${index + 1}`} className="max-setup-short-textarea"
              value={item.description} placeholder="Что важно знать об этом элементе?" onChange={event => updateContent(index, { description: event.target.value })} />
          </div>
          <div className="max-setup-field">
            <Label htmlFor={`${contentId}-action-${index}`}>Текст кнопки</Label>
            <Input id={`${contentId}-action-${index}`} aria-label={`Текст кнопки элемента ${index + 1}`} value={item.action_label}
              maxLength={40} placeholder="Открыть" onChange={event => updateContent(index, { action_label: event.target.value })} />
          </div>
        </div>
      </fieldset>)}
    </div>}
    <p className="max-setup-footnote">После подключения каталога к экрану приложение сможет читать обновления из этих настроек.</p>
  </section>;

  if (section === "owner") return <section className="max-setup-section">
    <Intro title="Кто отвечает за приложение" publication>Для публикации нужны имя или наименование владельца и email поддержки. Пока вы создаёте и проверяете приложение, этот раздел можно пропустить.</Intro>
    <p className="max-setup-notice">Сведения будут видны пользователям в документах и поддержке. Их сохранение не запускает ИИ и не расходует баланс.</p>
    <Group title="Реквизиты владельца" description="Укажите реквизиты, применимые к вашей форме деятельности: физлицо, ИП или организация.">
      <div className="max-setup-field max-setup-wide">
        <Label htmlFor="max-legal-name">ИП, ООО или ФИО владельца</Label>
        <Input id="max-legal-name" value={current.operator.legal_name} onChange={event => onChange({ ...current, operator: { ...current.operator, legal_name: event.target.value } })} />
        <p className="max-setup-hint">Нужно для публикации</p>
      </div>
      <div className="max-setup-field">
        <Label htmlFor="max-inn">ИНН</Label>
        <Input id="max-inn" value={current.operator.inn} inputMode="numeric" onChange={event => onChange({ ...current, operator: { ...current.operator, inn: event.target.value } })} />
      </div>
      <div className="max-setup-field">
        <Label htmlFor="max-ogrn">ОГРН / ОГРНИП</Label>
        <Input id="max-ogrn" value={current.operator.ogrn} inputMode="numeric" onChange={event => onChange({ ...current, operator: { ...current.operator, ogrn: event.target.value } })} />
      </div>
      <div className="max-setup-field max-setup-wide">
        <Label htmlFor="max-address">Адрес</Label>
        <Input id="max-address" value={current.operator.address} onChange={event => onChange({ ...current, operator: { ...current.operator, address: event.target.value } })} />
      </div>
    </Group>
    <Group title="Связь с поддержкой" description="Укажите действующие контакты, по которым вы готовы отвечать.">
      <div className="max-setup-field">
        <Label htmlFor="max-support-email">Email поддержки</Label>
        <Input id="max-support-email" type="email" value={current.support.email ?? ""} placeholder="support@example.ru"
          onChange={event => onChange({ ...current, support: { ...current.support, email: event.target.value || null } })} />
        <p className="max-setup-hint">Нужно для публикации</p>
      </div>
      <div className="max-setup-field">
        <Label htmlFor="max-support-phone">Телефон поддержки</Label>
        <Input id="max-support-phone" type="tel" value={current.support.phone}
          onChange={event => onChange({ ...current, support: { ...current.support, phone: event.target.value } })} />
      </div>
      <div className="max-setup-field max-setup-wide">
        <Label htmlFor="max-support-response-time">Срок ответа поддержки</Label>
        <Input id="max-support-response-time" value={current.support.response_time} maxLength={120} placeholder="Ответим в течение 2 рабочих дней"
          onChange={event => onChange({ ...current, support: { ...current.support, response_time: event.target.value } })} />
      </div>
    </Group>
  </section>;

  return <section className="max-setup-section">
    <Intro title="Правила для пользователей" publication>Проверьте данные для документов перед публикацией. Отметьте только функции, которые действительно есть в приложении — включать всё не нужно.</Intro>
    <Group title="Только если используется" description="Эти настройки добавляют разделы документов, но сами по себе не подключают оплату, модерацию или рассылки.">
      <div className="max-setup-policy-list max-setup-wide">{CHECKS.map(item => {
        const checked = Boolean(current.legal[item.key]);
        return <button key={item.key} type="button" aria-pressed={checked} className="max-setup-policy-option"
          onClick={() => onChange({ ...current, legal: { ...current.legal, [item.key]: !checked } })}>
          <span className="max-setup-choice" aria-hidden="true">{checked && <Check className="h-3.5 w-3.5" />}</span>
          <span className="max-setup-option-copy"><span>{item.label}</span><small>{item.description}</small></span>
          <span className="max-setup-choice-status" aria-hidden="true">{checked ? "Включено" : "Выключено"}</span>
        </button>;
      })}</div>
      <div className="max-setup-field">
        <Label htmlFor="max-age-rating">Возрастная маркировка</Label>
        <select id="max-age-rating" value={current.legal.age_rating} onChange={event => onChange({ ...current, legal: { ...current.legal, age_rating: event.target.value as MaxProjectConfigPayload["legal"]["age_rating"] } })}>
          {["0+", "6+", "12+", "16+", "18+"].map(rating => <option key={rating} value={rating}>{rating}</option>)}
        </select>
      </div>
    </Group>
    <Group title="Подтверждение и согласия">
      <label className="max-setup-consent max-setup-wide">
        <input type="checkbox" checked={current.legal.terms_accepted} onChange={event => onChange({ ...current, legal: { ...current.legal, terms_accepted: event.target.checked } })} />
        <span><span>Подтверждаю корректность данных владельца</span><small>Владелец отвечает за актуальность реквизитов и соответствие документов своей деятельности.</small></span>
      </label>
      <label className="max-setup-consent max-setup-wide">
        <input type="checkbox" checked={current.legal.personal_data_consent} onChange={event => onChange({ ...current, legal: { ...current.legal, personal_data_consent: event.target.checked } })} />
        <span><span>Требуется согласие на обработку персональных данных</span><small>Если приложение получает имя, телефон, email, адрес или другие данные пользователя. Для добавления запроса согласия в экраны используйте «Применить к приложению».</small></span>
      </label>
    </Group>
    <p className="max-setup-footnote">Автоматический комплект документов — основа. Проверьте его перед публикацией.</p>
  </section>;
}
