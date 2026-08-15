/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: "no-circular",
      severity: "error",
      from: { path: "^src/" },
      to: { circular: true, path: "^src/" },
    },
    {
      name: "not-to-unresolvable",
      severity: "error",
      from: { path: "^src/" },
      to: { couldNotResolve: true },
    },
  ],
  options: {
    includeOnly: ["^src/"],
    doNotFollow: { path: "^node_modules/" },
    exclude: { path: "^node_modules/" },
    tsConfig: { fileName: "tsconfig.json" },
    tsPreCompilationDeps: true,
    enhancedResolveOptions: {
      exportsFields: ["exports"],
      conditionNames: ["import", "require", "node", "default", "types"],
      extensions: [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"],
      mainFields: ["module", "main", "types", "typings"],
    },
  },
};
