import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

const MAX_API_BASE_URL =
  process.env.MAX_API_BASE_URL || "https://platform-api2.max.ru";
const MIN_REQUEST_GAP_MS = 40; // 25 req/s, below MAX's documented 30 req/s ceiling.
let nextRequestAt = 0;

async function waitForRateSlot(): Promise<void> {
  const now = Date.now();
  const wait = Math.max(0, nextRequestAt - now);
  nextRequestAt = Math.max(now, nextRequestAt) + MIN_REQUEST_GAP_MS;
  if (wait > 0) {
    await new Promise((resolve) => setTimeout(resolve, wait));
  }
}

export async function sendMaxWelcome(
  userId: string,
  appUrl: string,
): Promise<void> {
  const token = process.env.MAX_BOT_TOKEN;
  if (!token) throw new Error("MAX_BOT_TOKEN is not configured");
  await waitForRateSlot();
  const response = await fetch(
    `${MAX_API_BASE_URL}/messages?user_id=${encodeURIComponent(userId)}`,
    {
      method: "POST",
      headers: {
        Authorization: token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text: `Добро пожаловать в «${app.app_name}»! ${app.summary}`,
        attachments: [
          {
            type: "inline_keyboard",
            payload: {
              buttons: [
                [
                  {
                    type: "open_app",
                    text: app.primary_action || "Открыть",
                    web_app: appUrl,
                  },
                ],
              ],
            },
          },
        ],
      }),
      signal: AbortSignal.timeout(10_000),
    },
  );
  if (!response.ok) {
    throw new Error(`MAX Bot API returned HTTP ${response.status}`);
  }
}

export async function sendMaxHelp(userId: string, appUrl: string): Promise<void> {
  const token = process.env.MAX_BOT_TOKEN;
  if (!token) throw new Error("MAX_BOT_TOKEN is not configured");
  await waitForRateSlot();
  const response = await fetch(
    `${MAX_API_BASE_URL}/messages?user_id=${encodeURIComponent(userId)}`,
    {
      method: "POST",
      headers: {
        Authorization: token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text: `Откройте «${app.app_name}», чтобы ${app.primary_action.toLocaleLowerCase("ru-RU") || "продолжить"}. Если возникла проблема, используйте раздел поддержки.`,
        attachments: [
          {
            type: "inline_keyboard",
            payload: {
              buttons: [
                [
                  { type: "open_app", text: "Открыть приложение", web_app: appUrl },
                  { type: "link", text: "Поддержка", url: `${appUrl}/support` },
                ],
              ],
            },
          },
        ],
      }),
      signal: AbortSignal.timeout(10_000),
    },
  );
  if (!response.ok) throw new Error(`MAX Bot API returned HTTP ${response.status}`);
}
