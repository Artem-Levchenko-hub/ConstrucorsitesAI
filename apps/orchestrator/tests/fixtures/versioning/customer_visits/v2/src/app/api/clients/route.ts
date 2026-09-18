import { db } from "@/lib/db";
import { clients } from "@/lib/db/schema";

export async function GET() {
  return Response.json(await db.select().from(clients));
}

export async function POST(request: Request) {
  const body = await request.json();
  const [row] = await db
    .insert(clients)
    .values({ name: body.name, phone: body.phone, email: body.email })
    .returning();
  return Response.json(row, { status: 201 });
}
