"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, CircleAlert, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { verifyEmail } from "@/lib/api/max-account";

export function VerifyMaxEmail({ token }: { token: string }) {
  const [state, setState] = useState<"loading" | "success" | "error">("loading");

  useEffect(() => {
    let alive = true;
    verifyEmail(token)
      .then(() => alive && setState("success"))
      .catch(() => alive && setState("error"));
    return () => {
      alive = false;
    };
  }, [token]);

  return (
    <div className="w-full max-w-md rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-8 text-center shadow-[0_18px_50px_rgba(23,23,22,.06)]">
      <div className="mx-auto flex size-12 items-center justify-center rounded-lg bg-[#4f81f7]/10 text-[#4f81f7]">
        {state === "loading" && <Loader2 className="size-5 animate-spin" />}
        {state === "success" && <Check className="size-5" />}
        {state === "error" && <CircleAlert className="size-5 text-red-300" />}
      </div>
      <h1 className="mt-5 text-2xl font-semibold">
        {state === "loading"
          ? "Проверяем ссылку"
          : state === "success"
            ? "Email подтверждён"
            : "Ссылка не сработала"}
      </h1>
      <p className="mt-3 text-sm leading-6 text-[#9fa1b1]">
        {state === "loading"
          ? "Это займёт несколько секунд."
          : state === "success"
            ? "Теперь можно добавить данные владельца приложения."
            : "Возможно, ссылка устарела или уже была использована."}
      </p>
      {state !== "loading" && (
        <Button asChild className="mt-6 h-11 rounded-lg bg-[#4f81f7] text-[#121519] hover:bg-[#6a95fa]">
          <Link href="/max/onboarding">
            {state === "success" ? "Продолжить" : "Вернуться в настройку"}
          </Link>
        </Button>
      )}
    </div>
  );
}
