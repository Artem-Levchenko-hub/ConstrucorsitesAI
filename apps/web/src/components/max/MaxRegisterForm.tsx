"use client";

import Link from "next/link";
import { useActionState } from "react";
import { ArrowRight, Building2, Check, ShieldCheck } from "lucide-react";

import { maxRegisterAction } from "@/app/(auth)/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function MaxRegisterForm() {
  const [state, action, pending] = useActionState(maxRegisterAction, {
    error: null,
  });

  return (
    <form action={action} className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor="max-email">Рабочий email</Label>
          <Input
            id="max-email"
            name="email"
            type="email"
            autoComplete="email"
            required
            placeholder="name@company.ru"
            className="h-12 border-white/10 bg-white/[0.04]"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="max-password">Пароль</Label>
          <Input
            id="max-password"
            name="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            className="h-12 border-white/10 bg-white/[0.04]"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="max-confirm">Повторите пароль</Label>
          <Input
            id="max-confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            className="h-12 border-white/10 bg-white/[0.04]"
          />
        </div>
      </div>

      <div className="space-y-3 rounded-2xl border border-white/[0.08] bg-black/20 p-4 text-sm">
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="terms_accepted"
            type="checkbox"
            required
            className="mt-1 size-4 accent-[#315bd7]"
          />
          <span className="text-white/65">
            Принимаю{" "}
            <Link className="text-[#7897f4] hover:underline" href="/legal/terms">
              условия использования
            </Link>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="privacy_accepted"
            type="checkbox"
            required
            className="mt-1 size-4 accent-[#315bd7]"
          />
          <span className="text-white/65">
            Ознакомлен с{" "}
            <Link className="text-[#7897f4] hover:underline" href="/legal/privacy">
              политикой конфиденциальности
            </Link>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="personal_data_accepted"
            type="checkbox"
            required
            className="mt-1 size-4 accent-[#315bd7]"
          />
          <span className="text-white/65">
            Даю отдельное{" "}
            <Link
              className="text-[#7897f4] hover:underline"
              href="/legal/personal-data"
            >
              согласие на обработку персональных данных
            </Link>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="marketing_accepted"
            type="checkbox"
            className="mt-1 size-4 accent-[#315bd7]"
          />
          <span className="text-white/45">
            Получать новости продукта — необязательно
          </span>
        </label>
      </div>

      {state.error && (
        <p role="alert" className="rounded-xl bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {state.error}
        </p>
      )}

      <Button
        disabled={pending}
        className="h-12 w-full rounded-md bg-[#315bd7] text-base hover:bg-[#2449b7]"
      >
        {pending ? "Создаём аккаунт…" : "Продолжить"}
        {!pending && <ArrowRight className="ml-2 size-4" />}
      </Button>

      <div className="grid gap-2 text-xs text-white/40 sm:grid-cols-3">
        {[
          [Building2, "ООО, ИП или самозанятый"],
          [ShieldCheck, "Реквизиты проверяются"],
          [Check, "Один бизнес — один лимит"],
        ].map(([Icon, text]) => {
          const ItemIcon = Icon as typeof Building2;
          return (
            <div key={String(text)} className="flex items-center gap-2">
              <ItemIcon className="size-3.5 text-[#7897f4]" />
              {String(text)}
            </div>
          );
        })}
      </div>
    </form>
  );
}
