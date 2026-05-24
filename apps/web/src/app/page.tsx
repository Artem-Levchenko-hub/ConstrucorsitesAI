import { redirect } from "next/navigation";

const LANDING_URL =
  process.env.NEXT_PUBLIC_LANDING_URL ?? "https://landing.omniadevelop.ru/";

export default function RootRedirect() {
  redirect(LANDING_URL);
}
