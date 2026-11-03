/** Public product guidance only. No account lookups or model requests. */
export const supportTopics = [
  { id: "account", title: "Регистрация и вход" },
  { id: "billing", title: "Тарифы и оплата" },
  { id: "generation", title: "Генерации и правки" },
  { id: "publication", title: "Превью и публикация" },
  { id: "operator", title: "Связь с оператором" },
] as const;

export type SupportTopic = (typeof supportTopics)[number]["id"];
export type SupportAnswer = {
  id: string;
  topic: SupportTopic;
  question: string;
  paragraphs: readonly string[];
  links?: readonly { href: string; label: string }[];
  keywords: readonly string[];
};

const applicationEditKeywords: readonly string[] = ["изменить", "поменять", "редактир"];

export const supportAnswers: readonly SupportAnswer[] = [
  {
    id: "register", topic: "account", question: "Как зарегистрироваться и войти?",
    paragraphs: ["Откройте регистрацию Yleum, укажите email, телефон и пароль. Выполните подтверждение, которое предложит сайт, и примите документы.", "Для последующего входа используйте email и пароль. Коды подтверждения и пароль не нужно сообщать поддержке."],
    links: [{ href: "/max/register", label: "Регистрация" }, { href: "/login", label: "Войти" }],
    keywords: ["регистрац", "зарегистр", "создать аккаунт", "вход", "войти", "авторизац"],
  },
  {
    id: "password", topic: "account", question: "Забыл пароль — как восстановить доступ?",
    paragraphs: ["На странице входа выберите восстановление пароля и укажите email своей учётной записи. Проверьте входящие и папку «Спам». Используйте последнее письмо восстановления.", "Если письмо не приходит или доступа к почте нет, напишите оператору. Автопомощник не меняет пароль и не получает доступ к вашему аккаунту."],
    links: [{ href: "/forgot-password", label: "Восстановить пароль" }],
    keywords: ["парол", "восстановить доступ", "забыл", "забыла"],
  },
  {
    id: "verification", topic: "account", question: "Не приходит код подтверждения",
    paragraphs: ["Проверьте правильность email или номера телефона. Для письма также проверьте папку «Спам»; для SMS — приём сообщений на указанном номере.", "Повторную отправку запрашивайте, когда это разрешит сайт. Используйте последний полученный код. Если код по-прежнему не приходит, передайте оператору время попытки и текст ошибки, но не сам код."],
    keywords: ["код", "смс", "sms", "подтвержд", "не приходит письмо"],
  },
  {
    id: "plans", topic: "billing", question: "Где посмотреть тарифы и доступные правки?",
    paragraphs: ["В разделе «Тариф» показаны условия подписки и её лимиты, а в разделе «Баланс» — доступные пакеты. Остаток генераций и правок также отображается в редакторе.", "Актуальную цену и состав покупки проверяйте перед оплатой: условия уже оформленной подписки могут отличаться от текущего предложения."],
    links: [{ href: "/billing/plan", label: "Тариф" }, { href: "/billing", label: "Пакеты и баланс" }],
    keywords: ["тариф", "стоим", "цена", "сколько стоит", "pro", "лимит", "пакет", "остаток"],
  },
  {
    id: "payment", topic: "billing", question: "Оплата прошла, а тариф или пакет не появился",
    paragraphs: ["Откройте «Операции» и проверьте статус платежа. Обновите страницу аккаунта: закрытие окна оплаты само по себе ещё не подтверждает успешный платёж.", "Если деньги списаны, а начисления нет, не оплачивайте ту же покупку повторно. Напишите оператору сумму, примерное время и идентификатор платежа, если он есть. Данные карты и банковские коды не отправляйте."],
    links: [{ href: "/billing/transactions", label: "История операций" }],
    keywords: ["оплата", "платеж", "списал", "деньги", "оплата прошла", "деньги списаны", "не активиров", "не начисл", "возврат"],
  },
  {
    id: "renewal", topic: "billing", question: "Как управлять подпиской и продлением?",
    paragraphs: ["Откройте раздел «Тариф»: там можно проверить текущую подписку, дату окончания и доступные действия по продлению.", "Личный статус подписки и результат списания проверяет оператор. Автопомощник не отменяет платежи и не меняет настройки продления."],
    links: [{ href: "/billing/plan", label: "Управление тарифом" }],
    keywords: ["продлен", "подписк", "автоплат", "автопрод", "отменить подписку"],
  },
  {
    id: "create", topic: "generation", question: "Как создать приложение и внести правку?",
    paragraphs: ["В разделе «Мои приложения» создайте приложение и опишите нужный результат. После готовности откройте редактор и отправьте конкретное изменение: что добавить, убрать или изменить.", "Дождитесь завершения запроса и проверьте результат в превью. Лучше описать одну понятную задачу и указать, что важно сохранить."],
    links: [{ href: "/max", label: "Мои приложения" }, { href: "/max/guide", label: "Руководство Yleum" }],
    keywords: ["создать приложение", "сгенерир", "правк", ...applicationEditKeywords],
  },
  {
    id: "waiting", topic: "generation", question: "Генерация долго выполняется — что делать?",
    paragraphs: ["Генерация может занимать несколько минут; длительность зависит от задачи. Смотрите статус текущего запроса в редакторе. Пока он выполняется, не отправляйте тот же запрос повторно.", "Если статус долго не меняется или появилась ошибка, передайте оператору ссылку на приложение, время запуска и текст статуса. Автопомощник не запускает и не отменяет генерации."],
    keywords: ["генерация", "долго", "завис", "очеред", "ожидан", "сколько ждать"],
  },
  {
    id: "generation-error", topic: "generation", question: "Генерация или правка завершилась ошибкой",
    paragraphs: ["Сохраните точный текст ошибки, ссылку на приложение и время запроса. Проверьте статус в редакторе и доступную сохранённую версию.", "Не удаляйте приложение и не повторяйте платные запросы подряд. По одной надписи нельзя определить причину или подтвердить списание: оператор проверит конкретный запрос и операции."],
    keywords: ["ошиб", "сбой", "502", "503", "не запуска", "не отправ", "упал", "упала"],
  },
  {
    id: "preview", topic: "publication", question: "Превью пустое или показывает старую версию",
    paragraphs: ["Сначала убедитесь, что текущая генерация завершилась успешно. Нажмите доступную кнопку повтора или обновления превью; при необходимости откройте приложение заново.", "Превью и опубликованное приложение — разные этапы. Если сохранённая версия всё равно не открывается, сообщите оператору ссылку на приложение и текст ошибки."],
    keywords: ["превью", "предпросмотр", "белый экран", "пустой экран", "старая версия", "не открывается"],
  },
  {
    id: "publish", topic: "publication", question: "Как опубликовать приложение и подключить MAX?",
    paragraphs: ["Проверьте готовое приложение в превью и откройте раздел запуска или публикации. Выполните показанные шаги, включая подключение бота MAX, если он нужен, и дождитесь подтверждения публикации.", "Новая правка в редакторе не означает, что опубликованная версия уже обновлена. Если сайт просит проверить изменение данных, завершите эту проверку и отдельно подтвердите доступное изменение перед новой публикацией."],
    links: [{ href: "/max/guide", label: "Руководство по запуску" }],
    keywords: ["публик", "опублик", "запуск", "max", "макс", "подключить бота", "миграц", "изменение данных"],
  },
  {
    id: "operator", topic: "operator", question: "Как связаться с человеком из поддержки?",
    paragraphs: ["Нажмите «Написать оператору», опишите вопрос и отправьте обращение. Для гостя понадобятся email для ответа и согласие на обработку обращения.", "После отправки появится номер обращения. Ответ и история переписки доступны здесь; о новом ответе предусмотрено уведомление по email. Пароли, коды подтверждения и данные карты не отправляйте."],
    keywords: ["оператор", "человек", "поддержк", "сотрудник", "связаться", "помощь"],
  },
];

function normalize(value: string): string {
  return value.normalize("NFKC").toLocaleLowerCase("ru-RU").replace(/ё/g, "е")
    .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

// A small product vocabulary, not a general Russian stemmer: an application noun
// alone is never a creation intent, and arbitrary words are not reduced to stems.
const wordForms: readonly (readonly string[])[] = [
  ["создать", "создай", "создайте", "создаю", "создаем", "создаешь", "создает", "создаете", "создают", "создается", "создаются", "создавать", "создание", "создания", "создании", "созданием", "сделать", "сделай", "сделайте", "собрать", "собери", "соберите"],
  ["приложение", "приложения", "приложению", "приложении", "приложением", "приложений", "приложениях", "приложениями", "проект", "проекта", "проекту", "проекте", "проектом", "проекты", "проектов", "сайт", "сайта", "сайту", "сайте", "сайтом", "сайты", "сайтов"],
  ["оплата", "оплаты", "оплате", "оплату", "оплатой", "оплатить", "оплатил", "оплатила", "оплатили", "оплатите", "оплачу", "оплатим", "оплатят", "оплачивать"],
  ["генерация", "генерации", "генерацию", "генерацией", "генераций"],
];
const canonicalWords = new Map(wordForms.flatMap(forms => forms.map(form => [form, forms[0]] as const)));
const phraseModifiers = new Set(["новый", "новое", "новую", "новая", "новые", "нового", "новой", "новых", "свой", "свое", "свою", "своя", "свои", "своего", "своей", "своих"]);

function tokenize(value: string): string[] {
  return normalize(value).split(" ").filter(word => word && !phraseModifiers.has(word))
    .map(word => canonicalWords.get(word) ?? word);
}

/** One insertion, deletion, substitution or adjacent transposition, over a whole word. */
function oneEdit(sample: string, term: string): boolean {
  if (Math.abs(sample.length - term.length) > 1) return false;
  let left = 0, right = 0, edits = 0;
  while (left < sample.length && right < term.length) {
    if (sample[left] === term[right]) { left++; right++; continue; }
    if (++edits > 1) return false;
    if (sample.length === term.length && sample[left] === term[right + 1] && sample[left + 1] === term[right]) { left += 2; right += 2; continue; }
    if (sample.length >= term.length) left++;
    if (sample.length <= term.length) right++;
  }
  return edits + sample.length - left + term.length - right <= 1;
}

function matchesWord(word: string, term: string): boolean {
  // Short words, Latin identifiers and numeric error codes must be whole tokens.
  return term.length <= 3 || term === "макс" || /^[a-z0-9]+$/.test(term) ? word === term : word.startsWith(term);
}

function matchesPhrase(words: readonly string[], terms: readonly string[], fuzzy: boolean): boolean {
  return words.some((_, start) => {
    let typos = 0;
    return terms.every((term, offset) => {
      const word = words[start + offset];
      if (!word) return false;
      if (matchesWord(word, term)) return true;
      return fuzzy && word.length >= 5 && term.length >= 5 && ++typos <= 1 && oneEdit(word, term);
    });
  });
}

const indexedAnswers = supportAnswers.map(answer => ({ answer, keywords: answer.keywords.map(tokenize) }));

function rankedAnswers(question: string, partial: boolean) {
  const words = tokenize(question.slice(0, 500));
  if (words.join("").length < 3) return [];
  // An edit verb does not identify what is being changed. Use the existing
  // normalized app/site/project vocabulary, never the preceding conversation.
  const applicationContext = words.some(word => word === "приложение"
    || (partial && oneEdit(word, "приложение")));
  const singleWord = words.length === 1 ? words[0] : "";
  return indexedAnswers.map(({ answer, keywords }) => ({
    answer,
    score: keywords.reduce((score, terms) => {
      if (terms.length === 1 && applicationEditKeywords.includes(terms[0])
        && !applicationContext && !(partial && singleWord)) return score;
      if (matchesPhrase(words, terms, false)) return score + (terms.length > 1 ? 3 : 1);
      if (!partial) return score;
      if (matchesPhrase(words, terms, true)) return score + (terms.length > 1 ? 2.5 : 0.5);
      // An unfinished word is useful while typing, but not evidence inside an
      // unrelated sentence such as "создать таблицу в Excel".
      return score + (singleWord.length >= 3 && terms.some(term => term.startsWith(singleWord)
        || (singleWord.length >= 5 && term.length >= 5 && oneEdit(singleWord, term))) ? 0.25 : 0);
    }, 0),
  })).filter(item => item.score > 0).sort((a, b) => b.score - a.score);
}

/** Suggestions are choices, never an automatic submission of a partial question. */
export function searchSupportAnswers(question: string): readonly SupportAnswer[] {
  return rankedAnswers(question, true).slice(0, 3).map(item => item.answer);
}
export function relatedSupportAnswers(answer: SupportAnswer): readonly SupportAnswer[] {
  return supportAnswers.filter(item => item.topic === answer.topic && item.id !== answer.id).slice(0, 2);
}

/** Ties become explicit choices rather than an invented account-specific answer. */
export function findSupportAnswers(question: string): readonly SupportAnswer[] {
  const ranked = rankedAnswers(question, false);
  return ranked.filter(item => item.score === ranked[0]?.score).slice(0, 3).map(item => item.answer);
}
