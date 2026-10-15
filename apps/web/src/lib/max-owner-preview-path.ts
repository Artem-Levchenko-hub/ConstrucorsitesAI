/** Exact authenticated owner-viewer route; other MAX routes retain onboarding admission. */
export function isMaxOwnerPreviewPath(path: string): boolean {
  return /^\/max\/[a-zA-Z0-9_-]+\/preview(?:\?[^#]*)?$/.test(path);
}
