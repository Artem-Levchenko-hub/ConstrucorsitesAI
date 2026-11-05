/** New passwords only. Match core/password_policy.py and the shared test vectors. */
export const PASSWORD_MIN_LENGTH = 15;
export const PASSWORD_MAX_LENGTH = 128;
export const PASSWORD_HELP = "От 15 до 128 символов. Можно фразу из нескольких слов; цифры и спецсимволы необязательны.";

const COMMON_WORDS = [
  "password", "passw0rd", "qwerty", "qwertyuiop", "asdfgh", "zxcvbn",
  "admin", "administrator", "letmein", "welcome", "iloveyou", "changeme",
  "temporarypassword", "testpassword", "demopassword", "demo", "test",
  "secret", "omnia", "yleum", "пароль", "йцукен",
];
const COMMON_PHRASES = new Set([
  "correcthorsebatterystaple", "thisisapassword", "thisismypassword",
  "notasecurepassword", "yourpasswordhere", "pleasechangeme",
  "changethispassword", "changemeplease",
]);
const COMMON_VARIANT = new RegExp(`^[0-9]*(?:${COMMON_WORDS.join("|")})+[0-9]*$`, "u");
const SEQUENCES = [
  "0123456789", "1234567890", "9876543210", "0987654321",
  "12345", "123456", "654321", "abcdef", "abc123",
  "abcdefghijklmnopqrstuvwxyz", "zyxwvutsrqponmlkjihgfedcba",
  "qwertyuiopasdfghjklzxcvbnm", "mnbvcxzlkjhgfdsapoiuytrewq",
];

export function passwordLength(password: string): number {
  return Array.from(password).length;
}

/** Existing credentials keep their original composition and one-character minimum. */
export function existingPasswordError(password: string): string | null {
  if (!password) return "Введите пароль";
  return passwordLength(password) > PASSWORD_MAX_LENGTH
    ? "Пароль должен содержать не более 128 символов." : null;
}

export function newPasswordError(password: string): string | null {
  const length = passwordLength(password);
  if (length < PASSWORD_MIN_LENGTH || length > PASSWORD_MAX_LENGTH) {
    return "Пароль должен содержать от 15 до 128 символов.";
  }
  // Use Unicode White_Space plus BOM on both sides, rather than the different
  // built-in JS/Python whitespace sets. This comparison never changes the value.
  const normalized = password.normalize("NFKC").toLowerCase()
    .replace(/^[\p{White_Space}\ufeff]+|[\p{White_Space}\ufeff]+$/gu, "");
  const compact = normalized.replace(/[\p{White_Space}\ufeff._!@#$%^&*+=?\-]/gu, "");
  const characters = Array.from(normalized);
  const repeated = [1, 2, 3, 4].some(width => characters.length % width === 0 &&
    normalized === characters.slice(0, width).join("").repeat(characters.length / width));
  if (!normalized || repeated || /^([\s\S])\1{7,}[0-9]{0,8}$/u.test(normalized) ||
    COMMON_PHRASES.has(compact) || COMMON_VARIANT.test(compact) ||
    SEQUENCES.some(sequence => compact && sequence.repeat(14).includes(compact))) {
    return "Выберите менее распространённый пароль без простых повторов.";
  }
  return null;
}
