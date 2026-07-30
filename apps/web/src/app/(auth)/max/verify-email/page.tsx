import { VerifyMaxEmail } from "@/components/max/VerifyMaxEmail";

export default async function VerifyMaxEmailPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#0b0c12] px-5 text-white">
      <VerifyMaxEmail token={token ?? ""} />
    </main>
  );
}
