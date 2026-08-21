const RELEASE_SHA = /^[0-9a-f]{7,40}$/;

export function normalizeReleaseSha(value: string | undefined): string {
  return value && RELEASE_SHA.test(value) ? value : "unknown";
}
