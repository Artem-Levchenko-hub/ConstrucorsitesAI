import { VerifyMaxEmail } from "@/components/max/VerifyMaxEmail";

export default async function VerifyMaxEmailPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return (
    <main data-light-shell className="flex min-h-screen items-center justify-center bg-[#f5f3ee] px-5 text-[#171716]">
      <VerifyMaxEmail token={token ?? ""} />
    </main>
  );
}
