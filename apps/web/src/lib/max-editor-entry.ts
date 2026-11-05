export type MaxProjectSearch = Record<string, string | string[] | undefined>;

export function maxConnectionSearch(query: MaxProjectSearch) {
  const search = new URLSearchParams();
  if (query.tab === "services") search.set("tab", "services");
  if (typeof query.oauth === "string" && ["connected", "cancelled", "error", "conflict"].includes(query.oauth)) {
    search.set("oauth", query.oauth);
  }
  if (query.integration === "moysklad") search.set("integration", "moysklad");
  return search;
}

export function legacyMaxEditorDestination(projectId: string, query: MaxProjectSearch) {
  const base = `/max/${projectId}`;
  if (query.data === "content") return `${base}/catalog`;
  if (query.data === "details" || query.data === "owner" || query.data === "policies") {
    return `${base}/settings?tab=app&section=${query.data}`;
  }
  if (query.panel === "max") return `${base}/integrations`;
  if (query.panel === "publish" || query.panel === "hosting") return `${base}/publish`;
  if (query.panel === "services") {
    return `${base}/integrations?${maxConnectionSearch({ ...query, tab: "services" })}`;
  }
  return null;
}
