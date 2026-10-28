/** Public, privacy-safe activity codes. App opens belong to verified login only. */
export type MaxActivityType =
  | "screen_view" | "item_view" | "button_click" | "cart_add" | "checkout_start"
  | "form_submit" | "order_created" | "booking_created" | "lead_created";

/** Code family only; client reports always use event, even for business codes. */
export function maxActivityKind(value: unknown): "action" | "event" | null {
  switch (value) {
    case "screen_view": case "item_view": case "button_click":
    case "cart_add": case "checkout_start":
      return "event";
    case "form_submit": case "order_created": case "booking_created": case "lead_created":
      return "action";
    default:
      return null;
  }
}
