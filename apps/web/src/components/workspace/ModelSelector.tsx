"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown } from "lucide-react";
import { getModels } from "@/lib/api/models";
import { useWorkspaceStore } from "@/store/workspace";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  yandex: "Яндекс",
  alibaba: "Alibaba",
  sber: "Сбер",
  google: "Google",
};

export function ModelSelector() {
  const selected = useWorkspaceStore((s) => s.selectedModelId);
  const setModel = useWorkspaceStore((s) => s.setModel);

  const { data, isPending } = useQuery({
    queryKey: ["models"],
    queryFn: getModels,
    staleTime: 60_000,
  });

  if (isPending) return <Skeleton className="h-8 w-44" />;

  const current = data?.find((m) => m.id === selected) ?? data?.[0];
  if (!current) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size="sm" className="gap-2">
          <span className="font-mono text-xs text-fg-secondary">
            {PROVIDER_LABELS[current.provider]}
          </span>
          <span>{current.display_name}</span>
          <ChevronDown className="h-3.5 w-3.5 text-fg-tertiary" />
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="end"
        className="w-72 max-h-[min(70vh,32rem)] overflow-y-auto"
      >
        <DropdownMenuLabel className="sticky top-0 bg-popover">
          Модель
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {data!.map((m) => {
          // `available` reflects whether the gateway has the provider key
          // on the server. Unavailable models stay visible for transparency
          // but are disabled to avoid the user picking a dead route.
          const isAvailable = m.available !== false;
          return (
            <DropdownMenuItem
              key={m.id}
              onSelect={() => isAvailable && setModel(m.id)}
              disabled={!isAvailable}
              className="flex-col items-start gap-0.5 py-2"
            >
              <div className="flex items-center justify-between w-full">
                <span className="text-sm">{m.display_name}</span>
                <div className="flex items-center gap-2">
                  {!isAvailable && (
                    <span className="text-[10px] text-fg-tertiary">
                      нет ключа
                    </span>
                  )}
                  {m.id === selected && (
                    <Check className="h-4 w-4 text-accent" />
                  )}
                </div>
              </div>
              <div className="flex items-center justify-between w-full text-xs text-fg-tertiary">
                <span className="font-mono">
                  {m.price_rub_per_1k_in}/{m.price_rub_per_1k_out} ₽ за 1k
                </span>
                <div className="flex gap-1">
                  {m.recommended_for.map((tag) => (
                    <Badge key={tag} variant="outline" className="text-[10px]">
                      {tag}
                    </Badge>
                  ))}
                </div>
              </div>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
