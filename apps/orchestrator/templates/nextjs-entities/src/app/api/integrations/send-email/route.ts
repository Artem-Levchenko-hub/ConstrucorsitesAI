/**
 * SendEmail integration endpoint — auth-gated JSON POST. SMTP is opt-in; without
 * it configured this returns a stub (so app flows still work in preview).
 * Fixed template file — AI never edits it.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextRequest, NextResponse } from "next/server";

import { getCurrentUser } from "@/lib/session";
import { sendEmail } from "@/lib/integrations/server";

export async function POST(req: NextRequest) {
  try {
    const user = await getCurrentUser();
    if (!user) {
      return NextResponse.json({ error: "authentication required" }, { status: 401 });
    }
    const body = (await req.json().catch(() => ({}))) as {
      to?: string;
      subject?: string;
      body?: string;
    };
    const to = String(body.to ?? "");
    const subject = String(body.subject ?? "");
    const text = String(body.body ?? "");
    if (!to || !subject) {
      return NextResponse.json(
        { error: "'to' and 'subject' are required" },
        { status: 400 },
      );
    }
    const data = await sendEmail({ to, subject, body: text });
    return NextResponse.json({ data });
  } catch (e) {
    console.error("[integrations/send-email]", e);
    return NextResponse.json({ error: "send failed" }, { status: 500 });
  }
}
