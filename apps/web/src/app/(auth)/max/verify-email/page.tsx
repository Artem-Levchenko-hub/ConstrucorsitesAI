import { VerifyMaxEmail } from "@/components/max/VerifyMaxEmail";

export default async function VerifyMaxEmailPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return (
    <main data-product-shell className="flex min-h-screen items-center justify-center bg-[#121519] px-5 text-white">
      <VerifyMaxEmail token={token ?? ""} />
    </main>
  );
}
