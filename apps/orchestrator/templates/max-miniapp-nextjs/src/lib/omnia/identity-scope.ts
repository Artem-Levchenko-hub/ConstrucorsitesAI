"use client";

/** Browser lifecycle only: this generation is never a gateway epoch or credential.
 * The protected Provider owns writes. Its keyed subtree contains personal React
 * state; arbitrary module globals and unscoped browser storage are not erased. */
export type VerifiedPrincipal = Readonly<{ actor: string; mode: "max" | "preview" }>;
export type IdentitySnapshot = Readonly<{
  generation: number;
  principal: VerifiedPrincipal | null;
  checking: boolean;
}>;
export type IdentityScope = Readonly<VerifiedPrincipal & { generation: number }>;

export class IdentityScopeError extends Error {
  code: "actor_changed" | "unauthorized" | "unavailable";
  status: number;
  constructor(code: "actor_changed" | "unauthorized" | "unavailable", status: number) {
    super("The verified identity scope is no longer available");
    this.code = code;
    this.status = status;
  }
}

const initial: IdentitySnapshot = Object.freeze({ generation: 0, principal: null, checking: true });
let snapshot = initial;
let writer: object | null = null;
let invalidate: ((scope: IdentityScope) => boolean) | null = null;
const listeners = new Set<() => void>();

function publish(principal: VerifiedPrincipal | null, checking: boolean, changed = false): void {
  if (!changed && snapshot.principal === principal && snapshot.checking === checking) return;
  snapshot = Object.freeze({ generation: snapshot.generation + Number(changed), principal, checking });
  for (const listener of [...listeners]) listener();
}
export function getIdentitySnapshot(): IdentitySnapshot { return snapshot; }
export function getServerIdentitySnapshot(): IdentitySnapshot { return initial; }
export function subscribeIdentityScope(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function matches(scope: IdentityScope): boolean {
  return snapshot.generation === scope.generation && snapshot.principal?.actor === scope.actor &&
    snapshot.principal.mode === scope.mode;
}
export function captureIdentityScope(): IdentityScope {
  if (snapshot.checking) throw new IdentityScopeError("unavailable", 503);
  if (!snapshot.principal) throw new IdentityScopeError("unauthorized", 401);
  return Object.freeze({ ...snapshot.principal, generation: snapshot.generation });
}
export function assertIdentityScope(scope: IdentityScope): void {
  if (!matches(scope)) throw new IdentityScopeError("actor_changed", 409);
  if (snapshot.checking) throw new IdentityScopeError("unavailable", 503);
}
export function reportIdentityInvalidation(scope: IdentityScope): boolean {
  return invalidate?.(scope) ?? false;
}

/** @internal Only the protected Provider creates a controller in production. */
export function createIdentityController(onInvalidation?: () => void) {
  const token = {};
  let attempt = 0;
  let disposed = false;
  writer = token;
  publish(null, true, true);
  const active = () => !disposed && writer === token;
  const isCurrent = (id: number) => active() && attempt === id;
  invalidate = scope => {
    if (!active() || !matches(scope)) return false;
    attempt++;
    publish(null, false, true);
    onInvalidation?.();
    return true;
  };
  return {
    begin(): number {
      if (!active()) return -1;
      attempt++;
      publish(snapshot.principal, true);
      return attempt;
    },
    isCurrent,
    revoke(id: number): boolean {
      if (!isCurrent(id)) return false;
      publish(null, true, true);
      return true;
    },
    commit(id: number, principal: VerifiedPrincipal): boolean {
      if (!isCurrent(id)) return false;
      if (!principal.actor.trim() || principal.actor.length > 128 ||
        /[\u0000-\u001f\u007f]/.test(principal.actor) || !["max", "preview"].includes(principal.mode)) {
        throw new Error("Verified principal required");
      }
      const same = snapshot.principal?.actor === principal.actor && snapshot.principal.mode === principal.mode;
      publish(same ? snapshot.principal : Object.freeze({ ...principal }), false, !same);
      return true;
    },
    fail(id: number, unauthorized = false): boolean {
      if (!isCurrent(id)) return false;
      if (unauthorized) { attempt++; publish(null, false, true); }
      else publish(snapshot.principal, true);
      return true;
    },
    dispose(): void {
      if (active()) { writer = null; invalidate = null; publish(null, true, true); }
      disposed = true;
      attempt++;
    },
  };
}
