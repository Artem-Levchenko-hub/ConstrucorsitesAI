import {
  File,
  FileCode2,
  FileJson,
  FileText,
  Image as ImageIcon,
  Database,
  Palette,
  Settings2,
} from "lucide-react";

/**
 * Minimalist file-type icon + a soft colour tint, keyed off the extension.
 * Shared by the static file tree (CodeView) and the live build stream
 * (StreamingCodeView) so a `.tsx`, `.css` or `.py` reads the same everywhere.
 * One place owns "what a file type looks like" (DRY).
 */
export type FileIcon = { Icon: typeof FileCode2; color: string };

export function fileIcon(name: string): FileIcon {
  const ext = name.slice(name.lastIndexOf(".") + 1).toLowerCase();
  switch (ext) {
    case "tsx":
    case "jsx":
      return { Icon: FileCode2, color: "text-cyan-400" };
    case "ts":
      return { Icon: FileCode2, color: "text-blue-400" };
    case "js":
    case "mjs":
    case "cjs":
      return { Icon: FileCode2, color: "text-yellow-400" };
    case "py":
      return { Icon: FileCode2, color: "text-green-400" };
    case "css":
    case "scss":
    case "sass":
      return { Icon: Palette, color: "text-sky-400" };
    case "json":
      return { Icon: FileJson, color: "text-amber-400" };
    case "html":
      return { Icon: FileCode2, color: "text-orange-400" };
    case "sql":
      return { Icon: Database, color: "text-violet-400" };
    case "md":
    case "mdx":
    case "txt":
      return { Icon: FileText, color: "text-fg-tertiary" };
    case "png":
    case "jpg":
    case "jpeg":
    case "webp":
    case "gif":
    case "svg":
    case "ico":
      return { Icon: ImageIcon, color: "text-pink-400" };
    case "env":
    case "yml":
    case "yaml":
    case "toml":
    case "conf":
    case "config":
      return { Icon: Settings2, color: "text-fg-tertiary" };
    default:
      return { Icon: File, color: "text-fg-tertiary" };
  }
}
