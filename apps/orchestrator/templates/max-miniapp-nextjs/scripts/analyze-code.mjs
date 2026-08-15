#!/usr/bin/env node
/**
 * Opt-in, read-only code intelligence for agents.
 * Never passes a rewrite/fix option and emits a deliberately bounded JSON report.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

const MAX_DIAGNOSTICS = 100;
const MAX_SECURITY_FINDINGS = 30;
const MAX_AFFECTED_FILES = 100;
const MAX_OUTPUT_BYTES = 2_000_000;
const MAX_REPORT_BYTES = 140_000;
const TOOL_TIMEOUT_MS = 60_000;
const sourceDir = existsSync("src") ? "src" : ".";
const oxlintCli = resolve("node_modules", "oxlint", "dist", "cli.js");
const dependencyCruiserCli = resolve("node_modules", "dependency-cruiser", "bin", "dependency-cruise.mjs");
const astGrepCli = resolve("node_modules", "@ast-grep", "cli", process.platform === "win32" ? "ast-grep.exe" : "ast-grep");
const report = {
  diagnostics: [],
  security_findings: [],
  security_scan_completed: false,
  affected_files: [],
  counts: { oxlint: 0, dependency_cruiser: 0, ast_grep: 0, security: 0 },
  unavailable: [],
};

function normalizeDiagnostic(diagnostic) {
  const rawFile = typeof diagnostic.file === "string" ? diagnostic.file : "";
  const relativeFile = isAbsolute(rawFile) ? relative(process.cwd(), rawFile) : rawFile;
  const normalizedFile = relativeFile.replaceAll("\\", "/").replace(/^\.\//, "").slice(0, 500);
  const file = normalizedFile && !normalizedFile.startsWith("../") ? normalizedFile : undefined;
  return {
    tool: diagnostic.tool,
    severity: diagnostic.severity || "warning",
    message: String(diagnostic.message || "Unknown diagnostic").slice(0, 1_000),
    ...(file ? { file } : {}),
    ...(diagnostic.rule ? { rule: String(diagnostic.rule).slice(0, 200) } : {}),
  };
}

function pushDiagnostic(diagnostic) {
  const normalized = normalizeDiagnostic(diagnostic);
  if (report.diagnostics.length >= MAX_DIAGNOSTICS) {
    if (normalized.severity !== "error") return;
    const advisoryIndex = report.diagnostics.findIndex((item) => item.severity !== "error");
    if (advisoryIndex < 0) return;
    report.diagnostics.splice(advisoryIndex, 1);
  }
  report.diagnostics.push(normalized);
  const file = normalized.file;
  if (file && report.affected_files.length < MAX_AFFECTED_FILES && !report.affected_files.includes(file)) {
    report.affected_files.push(file);
  }
}

function pushSecurityFinding(diagnostic) {
  if (report.security_findings.length >= MAX_SECURITY_FINDINGS) return;
  report.security_findings.push(normalizeDiagnostic(diagnostic));
}

function unavailable(tool, reason) {
  if (!report.unavailable.some((item) => item.tool === tool)) {
    report.unavailable.push({ tool, reason: String(reason).slice(0, 500) });
  }
}

function serializeReport() {
  let serialized = JSON.stringify(report);
  while (Buffer.byteLength(serialized, "utf8") > MAX_REPORT_BYTES && report.diagnostics.length > 1) {
    const advisoryIndex = report.diagnostics.findLastIndex((item) => item.severity !== "error");
    report.diagnostics.splice(advisoryIndex >= 0 ? advisoryIndex : report.diagnostics.length - 1, 1);
    serialized = JSON.stringify(report);
  }
  while (Buffer.byteLength(serialized, "utf8") > MAX_REPORT_BYTES && report.affected_files.length > 0) {
    report.affected_files.pop();
    serialized = JSON.stringify(report);
  }
  if (Buffer.byteLength(serialized, "utf8") > MAX_REPORT_BYTES) {
    return JSON.stringify({
      diagnostics: [{ tool: "analyze-code", severity: "error", message: "Analyzer report exceeded its safe output budget" }],
      security_findings: report.security_findings.length > 0
        ? [{ tool: "osv-scanner", severity: "error", message: "Security findings exceeded their safe output budget" }]
        : [],
      security_scan_completed: report.security_scan_completed && report.security_findings.length === 0,
      affected_files: [],
      counts: report.counts,
      unavailable: [{ tool: "analyze-code", reason: "Report was reduced to a bounded fail-closed summary" }],
    });
  }
  return serialized;
}

function parseJson(raw, tool) {
  try {
    return JSON.parse(raw);
  } catch {
    unavailable(tool, "Tool did not return valid JSON");
    return null;
  }
}

function run(command, args) {
  return new Promise((done) => {
    const child = spawn(command, args, { cwd: process.cwd(), shell: false, windowsHide: true });
    let settled = false;
    let stdout = "";
    let stderr = "";
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      done(result);
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish({ code: null, stdout, stderr, error: new Error(`Tool timed out after ${TOOL_TIMEOUT_MS}ms`) });
    }, TOOL_TIMEOUT_MS);
    child.stdout.on("data", (chunk) => {
      if (stdout.length < MAX_OUTPUT_BYTES) stdout += chunk.toString().slice(0, MAX_OUTPUT_BYTES - stdout.length);
    });
    child.stderr.on("data", (chunk) => {
      if (stderr.length < MAX_OUTPUT_BYTES) stderr += chunk.toString().slice(0, MAX_OUTPUT_BYTES - stderr.length);
    });
    child.on("error", (error) => finish({ code: null, stdout, stderr, error }));
    child.on("close", (code) => finish({ code, stdout, stderr }));
  });
}

async function runNodeTool(tool, cli, args) {
  if (!existsSync(cli)) {
    unavailable(tool, "Pinned package binary is not installed");
    return { error: new Error("Tool not installed"), stdout: "", stderr: "", code: null };
  }
  const result = await run(process.execPath, [cli, ...args]);
  if (result.error) unavailable(tool, result.error.message);
  return result;
}

function readArgs(argv) {
  const options = { security: false, pattern: null, language: "tsx" };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--security") options.security = true;
    else if (arg === "--pattern" && argv[index + 1]) options.pattern = argv[++index];
    else if (arg === "--language" && argv[index + 1]) options.language = argv[++index];
    else throw new Error(`Unsupported argument: ${arg}`);
  }
  if (!/^[a-z0-9+-]{1,32}$/i.test(options.language)) throw new Error("Invalid --language value");
  if (options.pattern && options.pattern.length > 2_000) throw new Error("--pattern exceeds 2000 characters");
  return options;
}

async function analyzeOxlint() {
  const result = await runNodeTool("oxlint", oxlintCli, ["--format", "json", sourceDir]);
  if (result.error) return;
  const payload = parseJson(result.stdout, "oxlint");
  const entries = Array.isArray(payload) ? payload : payload?.diagnostics;
  if (!Array.isArray(entries)) return;
  for (const entry of entries) {
    const messages = entry.messages || [entry];
    for (const message of messages) {
      const label = message.labels?.[0];
      report.counts.oxlint += 1;
      pushDiagnostic({
        tool: "oxlint",
        severity: message.severity === 2 || message.severity === "error" ? "error" : "warning",
        message: message.message?.text || message.message,
        file: entry.filePath || label?.span?.source_path,
        rule: message.ruleId || message.code,
      });
    }
  }
}

async function analyzeDependencies() {
  const result = await runNodeTool("depcruise", dependencyCruiserCli, [
    "--config",
    ".dependency-cruiser.cjs",
    "--output-type",
    "json",
    sourceDir,
  ]);
  if (result.error) return;
  if (!result.stdout.trim()) {
    unavailable("dependency-cruiser", result.stderr || "Tool returned no JSON output");
    return;
  }
  const payload = parseJson(result.stdout, "dependency-cruiser");
  if (!payload) return;
  for (const module of payload.modules || []) {
    for (const dependency of module.dependencies || []) {
      const circular = dependency.circular === true || (Array.isArray(dependency.cycle) && dependency.cycle.length > 0);
      const unresolved = dependency.couldNotResolve === true;
      if (dependency.valid !== false && !circular && !unresolved) continue;
      report.counts.dependency_cruiser += 1;
      const reasons = [
        ...(dependency.valid === false ? dependency.rules || ["Invalid dependency"] : []),
        ...(circular ? ["Circular dependency"] : []),
        ...(unresolved ? ["Unresolved dependency"] : []),
      ];
      pushDiagnostic({
        tool: "dependency-cruiser",
        severity: "error",
        message: reasons.join(", "),
        file: module.source,
        rule: "dependency-cruiser",
      });
    }
  }
}

async function analyzePattern(pattern, language) {
  if (!existsSync(astGrepCli)) {
    unavailable("ast-grep", "Pinned package binary is not installed");
    return;
  }
  const result = await run(astGrepCli, ["--pattern", pattern, "--lang", language, "--json=stream", sourceDir]);
  if (result.error) return;
  for (const line of result.stdout.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const match = parseJson(line, "ast-grep");
    if (!match) break;
    report.counts.ast_grep += 1;
    pushDiagnostic({
      tool: "ast-grep",
      severity: "info",
      message: "Structural pattern match",
      file: match.file || match.fileName,
      rule: "pattern",
    });
  }
}

async function analyzeSecurity() {
  const result = await run("osv-scanner", [
    "scan",
    "-L",
    resolve(process.cwd(), "pnpm-lock.yaml"),
    "--format",
    "json",
  ]);
  if (result.error) {
    unavailable("osv-scanner", result.error.message);
    return;
  }
  const payload = parseJson(result.stdout, "osv-scanner");
  if (!payload) {
    unavailable("osv-scanner", result.stderr || "OSV service unavailable or scan failed");
    return;
  }
  if (!Array.isArray(payload.results)) {
    unavailable("osv-scanner", "Scanner response is missing the results array");
    return;
  }
  for (const resultItem of payload.results) {
    for (const packageItem of resultItem.packages || []) {
      for (const vulnerability of packageItem.vulnerabilities || []) {
        report.counts.security += 1;
        const diagnostic = {
          tool: "osv-scanner",
          severity: "error",
          message: `${vulnerability.id || "OSV vulnerability"}: ${packageItem.package?.name || "package"}`,
          file: resultItem.source?.path || resultItem.source?.name,
          rule: vulnerability.id,
        };
        pushSecurityFinding(diagnostic);
        pushDiagnostic(diagnostic);
      }
    }
  }
  if (result.code !== 0 && report.counts.security === 0) {
    unavailable("osv-scanner", result.stderr || `Scanner exited with code ${result.code}`);
    return;
  }
  report.security_scan_completed = true;
}

try {
  const options = readArgs(process.argv.slice(2));
  await analyzeOxlint();
  await analyzeDependencies();
  if (options.pattern) await analyzePattern(options.pattern, options.language);
  if (options.security) await analyzeSecurity();
  process.stdout.write(`${serializeReport()}\n`);
} catch (error) {
  report.unavailable.push({ tool: "analyze-code", reason: error instanceof Error ? error.message : String(error) });
  process.stdout.write(`${serializeReport()}\n`);
  process.exitCode = 2;
}
