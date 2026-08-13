declare module "jsdom" {
  export type DOMWindow = Window &
    typeof globalThis & {
      close(): void;
    };

  export class JSDOM {
    constructor(
      html?: string,
      options?: {
        runScripts?: "dangerously" | "outside-only";
        url?: string;
      },
    );

    window: DOMWindow;
  }
}
