"use client";

import { Button } from "@/components/ui/button";

export function AuthPageRefresh() {
  return (
    <Button type="button" variant="secondary" onClick={() => window.location.reload()}>
      Обновить страницу
    </Button>
  );
}
