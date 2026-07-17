/**
 * Generic folder-tree builder shared by the committed file browser (CodeView)
 * and the live build view (StreamingAgentCodeView). Each file leaf carries an
 * arbitrary payload `T` (a size, an activity record, …) so the same nesting +
 * single-child-chain collapse logic serves both. One place owns "how paths
 * become a tree" (DRY).
 */

export type TreeFile<T> = { kind: "file"; name: string; path: string; data: T };
export type TreeDir<T> = {
  kind: "dir";
  name: string;
  path: string;
  children: TreeNode<T>[];
};
export type TreeNode<T> = TreeFile<T> | TreeDir<T>;

function sortNodes<T>(nodes: TreeNode<T>[]): TreeNode<T>[] {
  return [...nodes].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1; // dirs first
    return a.name.localeCompare(b.name);
  });
}

/**
 * Build a nested folder tree from flat `{ path, data }` entries, then collapse
 * single-child directory chains (`src › app › (app)` → one `src/app/(app)` row)
 * so deep Next.js routes stay readable. Later entries for the same path win
 * (their `data` replaces earlier), so a caller can feed a running stream.
 */
export function buildFileTree<T>(
  entries: Array<{ path: string; data: T }>,
): TreeNode<T>[] {
  const root: TreeDir<T> = { kind: "dir", name: "", path: "", children: [] };

  for (const { path, data } of entries) {
    const parts = path.split("/").filter(Boolean);
    if (parts.length === 0) continue;
    let cur = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isLeaf = i === parts.length - 1;
      if (isLeaf) {
        const existing = cur.children.find(
          (c): c is TreeFile<T> => c.kind === "file" && c.name === part,
        );
        if (existing) existing.data = data; // last write wins
        else cur.children.push({ kind: "file", name: part, path, data });
      } else {
        const dirPath = parts.slice(0, i + 1).join("/");
        let next = cur.children.find(
          (c): c is TreeDir<T> => c.kind === "dir" && c.path === dirPath,
        );
        if (!next) {
          next = { kind: "dir", name: part, path: dirPath, children: [] };
          cur.children.push(next);
        }
        cur = next;
      }
    }
  }

  const collapseChain = (node: TreeDir<T>): TreeDir<T> => {
    let n = node;
    while (n.children.length === 1 && n.children[0].kind === "dir") {
      const only = n.children[0] as TreeDir<T>;
      n = {
        kind: "dir",
        name: `${n.name}/${only.name}`,
        path: only.path,
        children: only.children,
      };
    }
    n.children = sortNodes(
      n.children.map((c) => (c.kind === "dir" ? collapseChain(c) : c)),
    );
    return n;
  };

  return sortNodes(
    root.children.map((c) => (c.kind === "dir" ? collapseChain(c) : c)),
  );
}
