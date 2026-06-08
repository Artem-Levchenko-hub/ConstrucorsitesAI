/**
 * UploadFile integration endpoint — multipart POST, stores the file in object
 * storage server-side (creds never reach the browser) and returns a public URL.
 * Auth-gated. Fixed template file — AI never edits it.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextRequest, NextResponse } from "next/server";

import { getCurrentUser } from "@/lib/session";
import { uploadFile } from "@/lib/integrations/server";

export async function POST(req: NextRequest) {
  try {
    const user = await getCurrentUser();
    if (!user) {
      return NextResponse.json({ error: "authentication required" }, { status: 401 });
    }
    const form = await req.formData();
    const file = form.get("file");
    if (!(file instanceof File)) {
      return NextResponse.json({ error: "no 'file' in form data" }, { status: 400 });
    }
    const data = await uploadFile(file);
    return NextResponse.json({ data });
  } catch (e) {
    console.error("[integrations/upload-file]", e);
    return NextResponse.json({ error: "upload failed" }, { status: 500 });
  }
}
