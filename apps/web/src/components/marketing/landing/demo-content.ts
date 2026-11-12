export type DemoScenario = "cafe" | "salon" | "club" | "shop";

export const CAFE_DEMO = {
  name: "Кофейня «Зёрна»",
  section: "Меню и заказ",
  readyAt: "9:56",
  wait: "15 мин",
} as const;

export const SALON_DEMO = { name: "Студия красоты «Линия»", section: "Услуги и запись" } as const;
export const CLUB_DEMO = { name: "Книжный клуб «Между строк»", section: "Встречи и обсуждения" } as const;
export const SHOP_DEMO = { name: "Магазин растений «Листва»", section: "Каталог и самовывоз" } as const;
