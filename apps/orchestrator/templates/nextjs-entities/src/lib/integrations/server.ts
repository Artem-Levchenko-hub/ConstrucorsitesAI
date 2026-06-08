/**
 * Server-side integrations — Base44-style "Core" helpers the generated app can
 * call without ever holding credentials. Each runs on the server (route
 * handler) with creds from the orchestrator-injected env; the frontend touches
 * them only through the SDK. Fixed template file — the AI never edits it.
 *
 * Phase 2a (this file): UploadFile (→ MinIO) + SendEmail (SMTP, stubbed until
 * configured). InvokeLLM / GenerateImage (→ llm-gateway) land in a later pass.
 */

import { randomUUID } from "crypto";

import { Client } from "minio";

const BUCKET = process.env.MINIO_BUCKET || "omnia-user-uploads";
const PUBLIC_URL = (process.env.MINIO_PUBLIC_URL || "").replace(/\/+$/, "");

function minioClient(): Client {
  const endpoint = process.env.MINIO_ENDPOINT || "localhost:9000";
  const [host, portStr] = endpoint.split(":");
  return new Client({
    endPoint: host,
    port: portStr ? Number(portStr) : 9000,
    useSSL: process.env.MINIO_SECURE === "true",
    accessKey: process.env.MINIO_ACCESS_KEY || "",
    secretKey: process.env.MINIO_SECRET_KEY || "",
  });
}

function publicReadPolicy(bucket: string): string {
  return JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Principal: { AWS: ["*"] },
        Action: ["s3:GetObject"],
        Resource: [`arn:aws:s3:::${bucket}/*`],
      },
    ],
  });
}

async function ensureBucket(mc: Client): Promise<void> {
  const exists = await mc.bucketExists(BUCKET).catch(() => false);
  if (!exists) {
    await mc.makeBucket(BUCKET).catch(() => {});
  }
  // Public read so the returned URL loads in the browser. Idempotent.
  await mc.setBucketPolicy(BUCKET, publicReadPolicy(BUCKET)).catch(() => {});
}

export interface UploadResult {
  url: string;
  key: string;
  size: number;
  contentType: string;
}

/** Store a file in object storage and return a public URL. */
export async function uploadFile(file: File): Promise<UploadResult> {
  const mc = minioClient();
  await ensureBucket(mc);

  const buf = Buffer.from(await file.arrayBuffer());
  const safeName = (file.name || "file")
    .replace(/[^A-Za-z0-9._-]/g, "_")
    .slice(-80);
  const key = `${randomUUID()}-${safeName}`;
  const contentType = file.type || "application/octet-stream";

  await mc.putObject(BUCKET, key, buf, buf.length, {
    "Content-Type": contentType,
  });

  const url = PUBLIC_URL
    ? `${PUBLIC_URL}/${BUCKET}/${key}`
    : `/${BUCKET}/${key}`;
  return { url, key, size: buf.length, contentType };
}

export interface SendEmailInput {
  to: string;
  subject: string;
  body: string;
}
export interface SendEmailResult {
  sent: boolean;
  stubbed?: boolean;
  note?: string;
}

/**
 * Send an email. SMTP is opt-in: without SMTP_HOST configured we STUB (log +
 * report stubbed) so the app's flow still works in dev/preview. Real SMTP
 * sending (nodemailer) is wired in a later pass once credentials exist.
 */
export async function sendEmail(input: SendEmailInput): Promise<SendEmailResult> {
  if (!process.env.SMTP_HOST) {
    console.log(
      `[integrations.sendEmail:stub] to=${input.to} subject=${JSON.stringify(input.subject)}`,
    );
    return { sent: false, stubbed: true, note: "SMTP not configured" };
  }
  // Placeholder for the real SMTP path (SMTP_HOST/PORT/USER/PASS + nodemailer).
  return {
    sent: false,
    stubbed: true,
    note: "SMTP host set but sender not yet implemented",
  };
}
