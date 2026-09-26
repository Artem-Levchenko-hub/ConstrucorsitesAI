"use client";

import { type ReactNode } from "react";
import { Check, PackageOpen, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { MaxContentItem, MaxProjectConfigPayload } from "@/lib/api/types";
import { MAX_BRIEF_LENGTH, MAX_FEATURES, MAX_FEATURE_INFO } from "@/lib/max-brief";
import { MaxContentItemFields } from "./MaxContentItemEditor";

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
  return <div className="max-setup-intro"><span className="max-setup-purpose" data-publication={publication}>{publication ? "Есть обязательные пункты для публикации" : "Можно заполнить позже"}</span><h3>{title}</h3><p>{children}</p></div>;
}

/** Новая позиция: только название-заглушка, остальное владелец дозаполняет. */
function blankContentItem(): MaxContentItem {
  return {
    id: `item-${Date.now()}`,
    title: "",
    category: "",
    description: "",
    price: "",
    availability: "in_stock",
    options: [],
    image_url: "",
    action_label: "Открыть",
    active: true,
  };
}

export function MaxProjectSetupSections({ section, projectId, current, onChange }: {
  section: SetupSection;
  projectId: string;
  current: MaxProjectConfigPayload;
  onChange: (value: MaxProjectConfigPayload) => void;
}) {
  const summaryLength = Array.from(current.summary.trim()).length;
  // Функции, введённые раньше руками или агентом, словарю не принадлежат —
  // показываем их отдельной строкой, а не теряем при переключении карточек.
  const extraFeatures = current.features.filter(
    feature => !(MAX_FEATURES as readonly string[]).includes(feature),
  );
  const updateContent = (index: number, patch: Partial<MaxContentItem>) => {
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
        {/* В мастере создания функции выбираются карточками с пояснениями, а
            здесь была строка через запятую: один смысл двумя интерфейсами.
            Теперь тот же словарь и тот же способ выбора. */}
        <span className="max-content-label" id="max-config-features-label">Возможности приложения</span>
        <div className="max-setup-features" role="group" aria-labelledby="max-config-features-label">
          {MAX_FEATURES.map(feature => {
            const selected = current.features.includes(feature);
            return <button key={feature} type="button" aria-pressed={selected} className="max-setup-feature"
              onClick={() => onChange({
                ...current,
                features: selected
                  ? current.features.filter(item => item !== feature)
                  : [...current.features, feature].slice(0, 24),
              })}>
              <span className="max-setup-feature-check" aria-hidden="true">{selected && <Check className="h-3 w-3" />}</span>
              <span>
                <span className="max-setup-feature-label">{feature}</span>
                <span className="max-setup-feature-hint">{MAX_FEATURE_INFO[feature].summary}</span>
              </span>
            </button>;
          })}
        </div>
        {extraFeatures.length > 0 && (
          <p className="max-setup-hint">Добавлено вручную: {extraFeatures.join(", ")} — останется как есть.</p>
        )}
      </div>
    </Group>
    <Group title="Оформление" description="Можно оставить текущий стиль. Дополнительные цвета не нужны для публикации.">
      <div className="max-setup-field">
        <Label htmlFor="max-config-colors">Цвета бренда</Label>
        <Input id="max-config-colors" value={current.brand_colors} placeholder="Например: #1F6F4A, тёплый бежевый"
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

  if (section === "content") {
    const shown = current.content.filter(item => item.active).length;
    const sections = [...new Set(current.content.map(item => item.category.trim()).filter(Boolean))];
    return <section className="max-setup-section">
      <div className="max-setup-content-heading">
        <Intro title="Каталог и контент">Товары, услуги, сеансы или уроки — то, что пользователь увидит списком и сможет открыть. Заполнять необязательно: без каталога приложение тоже работает.</Intro>
        <Button size="sm" className="max-setup-add" onClick={() => onChange({ ...current, content: [...current.content, blankContentItem()] })}>
          <Plus aria-hidden="true" className="h-4 w-4" />Добавить позицию
        </Button>
      </div>

      <ol className="max-content-steps">
        <li><span aria-hidden="true">1</span><span><b>«Сохранить и проверить»</b> — позиции уезжают в приложение как данные. ИИ не запускается, баланс не расходуется.</span></li>
        <li><span aria-hidden="true">2</span><span><b>«Применить к приложению»</b> — разовая доработка: ИИ выводит каталог на экраны и учит их читать эти данные. Расходует баланс.</span></li>
        <li><span aria-hidden="true">3</span><span>После этого правки позиций видны в приложении <b>без новой сборки</b> — достаточно сохранить.</span></li>
      </ol>

      {current.content.length === 0 ? <div className="max-setup-empty">
        <PackageOpen aria-hidden="true" />
        <h4>Пока нет позиций</h4>
        <p>Добавьте первый товар, услугу или урок: фото, название, раздел, цену, наличие и варианты. Если каталог не нужен, оставьте раздел пустым.</p>
      </div> : <div className="max-setup-content-list">
        <p className="max-setup-hint">
          Позиций: {current.content.length} · показываем в приложении: {shown}
          {sections.length > 0 && ` · разделов: ${sections.length} (${sections.join(", ")})`}
        </p>
        {current.content.map((item, index) => <fieldset key={item.id} className="max-setup-content-item">
          <legend>{item.title.trim() || `Позиция ${index + 1}`}</legend>
          <div className="max-setup-item-toolbar">
            <button type="button" role="switch" aria-checked={item.active} aria-label={`Показывать элемент ${index + 1}`}
              className="max-setup-visibility" onClick={() => updateContent(index, { active: !item.active })}>
              <span className="max-setup-switch-track" aria-hidden="true"><span /></span>
              {item.active ? "Показан в приложении" : "Скрыт из приложения"}
            </button>
            <button type="button" className="max-setup-delete" aria-label={`Удалить ${item.title || `позицию ${index + 1}`}`}
              onClick={() => onChange({ ...current, content: current.content.filter((_, itemIndex) => itemIndex !== index) })}>
              <Trash2 className="h-4 w-4" aria-hidden="true" /><span>Удалить</span>
            </button>
          </div>
          <MaxContentItemFields projectId={projectId} index={index} item={item}
            onPatch={patch => updateContent(index, patch)} />
        </fieldset>)}
      </div>}
      <p className="max-setup-footnote">Цену, наличие и состав каталога приложение перечитывает при каждом открытии экрана. Если приложение собиралось раньше, чем у позиций появились фото и разделы, на экранах они покажутся после следующего «Применить к приложению».</p>
    </section>;
  }

  if (section === "owner") return <section className="max-setup-section">
    <Intro title="Документы приложения">Yleum не запрашивает реквизиты: бизнес за ботом проверяет сам MAX. Здесь только то, что увидят пользователи в документах и поддержке приложения — всё необязательно.</Intro>
    <p className="max-setup-notice">Сохранение этих полей не запускает ИИ и не расходует баланс.</p>
    <Group title="Владелец в документах" description="Как назвать владельца в политике и условиях приложения. Если оставить пустым, документы назовут само приложение.">
      <div className="max-setup-field max-setup-wide">
        <Label htmlFor="max-legal-name">Название владельца для документов</Label>
        <Input id="max-legal-name" value={current.operator.legal_name} placeholder={current.app_name} onChange={event => onChange({ ...current, operator: { legal_name: event.target.value } })} />
      </div>
      <div className="max-setup-field max-setup-wide">
        <Label htmlFor="max-policy-url">Ссылка на свою политику конфиденциальности</Label>
        <Input id="max-policy-url" type="url" value={current.legal.policy_url} placeholder="https://example.ru/privacy" inputMode="url"
          onChange={event => onChange({ ...current, legal: { ...current.legal, policy_url: event.target.value } })} />
        <p className="max-setup-group-note">Если у компании уже есть политика — укажите её, и страница конфиденциальности приложения будет вести на неё.</p>
      </div>
    </Group>
    <Group title="Связь с поддержкой" description="Контакт, по которому пользователи приложения смогут задать вопрос.">
      <div className="max-setup-field">
        <Label htmlFor="max-support-email">Email поддержки</Label>
        <Input id="max-support-email" type="email" value={current.support.email ?? ""} placeholder="support@example.ru"
          onChange={event => onChange({ ...current, support: { ...current.support, email: event.target.value || null } })} />
      </div>
      <div className="max-setup-field max-setup-wide">
        <Label htmlFor="max-support-response-time">Срок ответа поддержки</Label>
        <Input id="max-support-response-time" value={current.support.response_time} maxLength={120} placeholder="Ответим в течение 2 рабочих дней"
          onChange={event => onChange({ ...current, support: { ...current.support, response_time: event.target.value } })} />
      </div>
    </Group>
  </section>;

  return <section className="max-setup-section">
    <Intro title="Правила для пользователей" publication>Для публикации обязательно подтвердите документы приложения ниже. Продажи, контент, рассылки и согласие на обработку данных отмечайте только если они нужны вашему приложению — включать всё не нужно.</Intro>
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
        <span><span>Я проверил политику и условия приложения</span><small className="max-setup-required">Обязательно для публикации</small><small>Документы формируются автоматически из настроек приложения; владелец отвечает за их актуальность.</small></span>
      </label>
      <label className="max-setup-consent max-setup-wide">
        <input type="checkbox" checked={current.legal.personal_data_consent} onChange={event => onChange({ ...current, legal: { ...current.legal, personal_data_consent: event.target.checked } })} />
        <span><span>Требуется согласие на обработку персональных данных</span><small>Если приложение получает имя, телефон, email, адрес или другие данные пользователя. Для добавления запроса согласия в экраны используйте «Применить к приложению».</small></span>
      </label>
    </Group>
    <p className="max-setup-footnote">Автоматический комплект документов — основа. Проверьте его перед публикацией.</p>
  </section>;
}
