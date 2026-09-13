/**
 * `<SignOutButton>` — CSRF-safe sign-out via POST to `/signout`. Styles
 * are intentionally minimal — AI is expected to restyle per project
 * brand. Renders as a `<button>` inside a `<form>` so it works without
 * JS (no useEffect, no client component overhead).
 */

export function SignOutButton({
  children = "Выйти",
  className,
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <form action="/signout" method="post" className="inline">
      <button
        type="submit"
        className={
          className ??
          "text-sm text-zinc-600 hover:text-zinc-900 transition underline-offset-4 hover:underline"
        }
      >
        {children}
      </button>
    </form>
  );
}
