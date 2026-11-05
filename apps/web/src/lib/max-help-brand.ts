import { getSession } from "@/lib/auth-mock";

export async function getMaxHelpBrandProps(publicHref: "/" | "/max/product") {
  const session = await getSession();
  if (session && !session.isAnon) {
    return { href: "/max", ariaLabel: "Yleum — мои приложения" };
  }
  return { href: publicHref };
}
