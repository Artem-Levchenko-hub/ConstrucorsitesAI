/**
 * Auth.js (NextAuth v5) — Credentials (email + password) over a JWT session.
 * FIXED template file. AI extends session shape or adds OAuth providers here.
 *
 * No database adapter: the Credentials provider requires the `jwt` strategy
 * (Auth.js refuses a credentials sign-in under the `database` strategy), and a
 * JWT session needs no `sessions`/`accounts` tables. We store users in our own
 * `users` table and authenticate against it directly — leaner and fully typed.
 * To force a global sign-out, rotate AUTH_SECRET. To add OAuth later, add a
 * provider here and (if you want linked accounts) introduce the adapter tables.
 *
 * The orchestrator injects on container start:
 *   AUTH_SECRET   — JWT signing key, generated per project at provision time.
 *   DATABASE_URL  — Postgres DSN for the per-project schema.
 */

import bcrypt from "bcryptjs";
import { eq, isNotNull } from "drizzle-orm";
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { z } from "zod";

import { db } from "@/lib/db";
import { users } from "@/lib/db/schema";

const credentialsSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
});

export const { handlers, signIn, signOut, auth } = NextAuth({
  session: { strategy: "jwt", maxAge: 30 * 24 * 60 * 60 },
  trustHost: true,
  pages: { signIn: "/signin", error: "/signin" },
  providers: [
    Credentials({
      name: "Email и пароль",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Пароль", type: "password" },
      },
      authorize: async (raw) => {
        const parsed = credentialsSchema.safeParse(raw);
        if (!parsed.success) return null;
        const { email, password } = parsed.data;
        const [user] = await db
          .select()
          .from(users)
          .where(eq(users.email, email.toLowerCase()))
          .limit(1);
        if (!user?.passwordHash) return null; // no password / no such user
        const ok = await bcrypt.compare(password, user.passwordHash);
        if (!ok) return null;
        return {
          id: user.id,
          email: user.email,
          name: user.name,
          image: user.image,
        };
      },
    }),
  ],
  callbacks: {
    jwt: async ({ token, user }) => {
      if (user?.id) {
        token.id = user.id;
        const [row] = await db
          .select({ role: users.role })
          .from(users)
          .where(eq(users.id, user.id))
          .limit(1);
        token.role = row?.role ?? "user";
      }
      return token;
    },
    session: async ({ session, token }) => {
      if (session.user) {
        session.user.id = (token.id as string) ?? session.user.id;
        session.user.role = (token.role as string) ?? "user";
      }
      return session;
    },
  },
});

/** Hash a plaintext password for `users.passwordHash` (bcrypt, 10 rounds). */
export async function hashPassword(plain: string): Promise<string> {
  return bcrypt.hash(plain, 10);
}

/**
 * Role for a brand-new signup. The first real (password-bearing) account is the
 * app OPERATOR → "admin"; everyone after is a "user". Fail-soft to "user".
 */
export async function roleForNewUser(): Promise<"admin" | "user"> {
  try {
    const [row] = await db
      .select({ id: users.id })
      .from(users)
      .where(isNotNull(users.passwordHash))
      .limit(1);
    return row ? "user" : "admin";
  } catch {
    return "user";
  }
}
