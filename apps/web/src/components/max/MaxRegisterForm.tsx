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
            className="h-12 border-[#2b2d32] bg-[#191b20]"
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
            className="h-12 border-[#2b2d32] bg-[#191b20]"
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
            className="h-12 border-[#2b2d32] bg-[#191b20]"
          />
        </div>
      </div>

      <div className="space-y-3 rounded-[10px] border border-[#2b2d32] bg-[#121519] p-4 text-sm">
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="terms_accepted"
            type="checkbox"
            required
            className="mt-1 size-4 accent-[#4f81f7]"
          />
          <span className="text-[#9fa1b1]">
            Принимаю{" "}
            <Link className="text-[#6a95fa] hover:underline" href="/legal/terms">
              условия использования
            </Link>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="privacy_accepted"
            type="checkbox"
            required
            className="mt-1 size-4 accent-[#4f81f7]"
          />
          <span className="text-[#9fa1b1]">
            Ознакомлен с{" "}
            <Link className="text-[#6a95fa] hover:underline" href="/legal/privacy">
              политикой конфиденциальности
            </Link>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3">
          <input
            name="personal_data_accepted"
            type="checkbox"
            required
            className="mt-1 size-4 accent-[#4f81f7]"
          />
          <span className="text-[#9fa1b1]">
            Даю отдельное{" "}
            <Link
              className="text-[#6a95fa] hover:underline"
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
            className="mt-1 size-4 accent-[#4f81f7]"
          />
          <span className="text-[#828491]">
            Получать новости продукта — необязательно
          </span>
        </label>
      </div>

      {state.error && (
        <p role="alert" className="rounded-[8px] bg-[#c63d35]/10 px-4 py-3 text-sm text-danger-fg">
          {state.error}
        </p>
      )}

      <Button
        disabled={pending}
        className="h-12 w-full rounded-lg bg-[#4f81f7] text-base text-[#121519] hover:bg-[#6a95fa]"
      >
        {pending ? "Создаём аккаунт…" : "Продолжить"}
        {!pending && <ArrowRight className="ml-2 size-4" />}
      </Button>

      <div className="grid gap-2 text-xs text-[#828491] sm:grid-cols-3">
        {[
          [Building2, "Для первого старта нужен только email"],
          [ShieldCheck, "Секреты не передаются агенту"],
          [Check, "MAX и платежи подключаются позже"],
        ].map(([Icon, text]) => {
          const ItemIcon = Icon as typeof Building2;
          return (
            <div key={String(text)} className="flex items-center gap-2">
              <ItemIcon className="size-3.5 text-[#4f81f7]" />
              {String(text)}
            </div>
          );
        })}
      </div>
    </form>
  );
}
