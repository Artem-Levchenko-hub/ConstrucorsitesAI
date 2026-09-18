import { db } from "@/lib/db";
import { visits } from "@/lib/db/schema";

export async function GET() {
  return Response.json(await db.select().from(visits));
}

export const POST = async (request: Request) => {
  const body = await request.json();
  const [row] = await db.insert(visits).values(body).returning();
  return Response.json(row, { status: 201 });
};
