import { VerifyMaxEmail } from "@/components/max/VerifyMaxEmail";
import "@/components/max/max-studio.css";

export default async function VerifyMaxEmailPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return (
    <main data-max-studio className="flex min-h-screen items-center justify-center bg-bg-base px-5 text-fg-primary">
      <VerifyMaxEmail token={token ?? ""} />
    </main>
  );
}
