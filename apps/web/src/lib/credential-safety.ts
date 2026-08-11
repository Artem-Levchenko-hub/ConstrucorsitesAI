const SECRET_PATTERNS = [
  /\bsk-[A-Za-z0-9_-]{16,}\b/g,
  /\bAIza[0-9A-Za-z_-]{30,}\b/g,
  /\bgh[pousr]_[A-Za-z0-9]{20,}\b/g,
  /\bxox[baprs]-[0-9A-Za-z-]{10,}\b/g,
  /-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----/g,
] as const;

export function redactCredentialsBeforeTransport(value: string): {
  text: string;
  credentialsRemoved: boolean;
} {
  let text = value;
  let credentialsRemoved = false;
  for (const pattern of SECRET_PATTERNS) {
    text = text.replace(pattern, () => {
      credentialsRemoved = true;
      return "[CREDENTIAL REDACTED]";
    });
  }
  return { text, credentialsRemoved };
}
