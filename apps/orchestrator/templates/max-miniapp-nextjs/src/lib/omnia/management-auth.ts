import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import {
  HASH,
  UUID,
  ManagementError,
  object,
} from "@/lib/omnia/management-resources";
import type { ManagementContract } from "@/lib/omnia/management-resources";
export type OwnerIdentity = {
  project_id: string;
  owner_id: string;
  publication_id: string;
};
export function verifyOwnerAssertion(
  request: Request,
  body: Buffer,
  contract: ManagementContract,
): OwnerIdentity {
  const env = process.env,
    key = env.YLEUM_OWNER_ASSERTION_KEY;
  if (
    !key ||
    !HASH.test(key) ||
    ![
      env.YLEUM_OWNER_PROJECT_ID,
      env.YLEUM_OWNER_ID,
      env.YLEUM_OWNER_PUBLICATION_ID,
    ].every((v) => typeof v === "string" && UUID.test(v))
  )
    throw new ManagementError("unavailable", 503);
  try {
    const token = request.headers.get("X-Yleum-Owner-Assertion") || "";
    if (token.length > 4096) throw Error();
    const [version, encoded, signature, ...rest] = token.split(".");
    if (
      version !== "1" ||
      rest.length ||
      !encoded ||
      !signature ||
      !/^[-_A-Za-z0-9]+$/.test(encoded) ||
      !/^[-_A-Za-z0-9]{43}$/.test(signature)
    )
      throw Error();
    const expected = createHmac("sha256", Buffer.from(key, "hex"))
      .update(`1.${encoded}`)
      .digest();
    const supplied = Buffer.from(signature, "base64url");
    if (
      supplied.length !== expected.length ||
      !timingSafeEqual(supplied, expected)
    )
      throw Error();
    const claims = object(
      JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(
          Buffer.from(encoded, "base64url"),
        ),
      ),
      [
        "project_id",
        "owner_id",
        "publication_id",
        "contract_hash",
        "method",
        "path",
        "body_sha256",
        "iat",
        "exp",
      ],
    );
    if (Object.keys(claims).length !== 9) throw Error();
    const now = Math.floor(Date.now() / 1000);
    if (
      !Number.isSafeInteger(claims.iat) ||
      !Number.isSafeInteger(claims.exp) ||
      (claims.exp as number) <= now ||
      (claims.iat as number) > now + 5 ||
      (claims.exp as number) <= (claims.iat as number) ||
      (claims.exp as number) > (claims.iat as number) + 60
    )
      throw Error();
    if (
      claims.project_id !== env.YLEUM_OWNER_PROJECT_ID ||
      claims.owner_id !== env.YLEUM_OWNER_ID ||
      claims.publication_id !== env.YLEUM_OWNER_PUBLICATION_ID ||
      claims.contract_hash !== contract.contract_hash ||
      claims.method !== "POST" ||
      request.method !== "POST" ||
      claims.path !== "/api/omnia/owner" ||
      new URL(request.url).pathname !== claims.path ||
      claims.body_sha256 !== createHash("sha256").update(body).digest("hex")
    )
      throw Error();
    return {
      project_id: claims.project_id as string,
      owner_id: claims.owner_id as string,
      publication_id: claims.publication_id as string,
    };
  } catch {
    throw new ManagementError("unauthorized", 401);
  }
}
