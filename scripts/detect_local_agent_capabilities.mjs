#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { basename, extname, join } from "node:path";
import { pathToFileURL } from "node:url";

const FALLBACK_CODEX_SANDBOXES = ["read-only", "workspace-write", "danger-full-access"];
const FALLBACK_CODEX_APPROVALS = ["untrusted", "on-request", "never"];
const FALLBACK_CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"];
const FALLBACK_CLAUDE_PERMISSIONS = [
  "acceptEdits",
  "auto",
  "bypassPermissions",
  "manual",
  "dontAsk",
  "plan",
];

function unique(values) {
  return [...new Set(values.filter((value) => typeof value === "string" && value.trim()).map((value) => value.trim()))];
}

function optionBlock(help, option) {
  const start = help.indexOf(option);
  if (start < 0) return "";
  const rest = help.slice(start);
  const next = rest.slice(option.length).search(/\n\s{0,6}(?:-[a-zA-Z]|--[a-zA-Z])/);
  return next < 0 ? rest : rest.slice(0, option.length + next);
}

function parenthesizedValues(block) {
  const groups = [...block.matchAll(/\((?:choices:\s*)?([^)]+)\)/g)];
  for (const match of groups) {
    const values = unique(
      match[1]
        .replaceAll('"', "")
        .split(",")
        .map((value) => value.trim()),
    );
    if (values.length > 1) return values;
  }
  return [];
}

function possibleValues(block) {
  const match = block.match(/\[possible values:\s*([^\]]+)\]/i);
  return match ? unique(match[1].split(",").map((value) => value.trim())) : [];
}

function bulletValues(block) {
  return unique([...block.matchAll(/^\s*-\s*([a-zA-Z][\w-]*):/gm)].map((match) => match[1]));
}

function quotedAliasValues(block) {
  const aliasSection = block.match(/alias[\s\S]*?\(e\.g\.\s*([^)]+)\)/i)?.[1] || "";
  return unique([...aliasSection.matchAll(/'([^']+)'/g)].map((match) => match[1]));
}

function tomlString(text, key) {
  const escaped = key.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
  return text.match(new RegExp(`^\\s*${escaped}\\s*=\\s*["']([^"']+)["']`, "m"))?.[1] || "";
}

function runCli(binary, args) {
  if (!binary || /[\r\n\0&|<>^]/.test(binary)) {
    return { ok: false, output: "", error: "invalid binary" };
  }
  const result = spawnSync(binary, args, {
    encoding: "utf8",
    timeout: 5000,
    maxBuffer: 2 * 1024 * 1024,
    shell: process.platform === "win32",
    windowsHide: true,
  });
  const output = String(result.stdout || result.stderr || "").trim();
  if (result.error) return { ok: false, output, error: result.error.code || result.error.message };
  if (result.status !== 0) return { ok: false, output, error: output.slice(0, 200) || `exit ${result.status}` };
  return { ok: true, output, error: null };
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function readText(path) {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return "";
  }
}

function codexProfiles(codexHome, configText) {
  const files = existsSync(codexHome)
    ? readdirSync(codexHome)
      .filter((name) => name.endsWith(".config.toml") && name !== "config.toml")
      .map((name) => name.slice(0, -".config.toml".length))
    : [];
  const sections = [...configText.matchAll(/^\s*\[profiles\.([^\]]+)\]/gm)].map((match) => match[1]);
  return unique([...files, ...sections]);
}

function claudeAgents(cwd, claudeHome) {
  const roots = [join(claudeHome, "agents"), join(cwd, ".claude", "agents")];
  const names = [];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    for (const name of readdirSync(root)) {
      if (extname(name).toLowerCase() === ".md") names.push(basename(name, extname(name)));
    }
  }
  return unique(names);
}

export function parseCodexCapabilities({
  help,
  version,
  cache,
  configText = "",
  profiles = [],
  available = true,
  error = null,
}) {
  const cachedModels = Array.isArray(cache?.models) ? cache.models : [];
  const models = cachedModels
    .filter((model) => model && model.slug && model.visibility !== "hide")
    .map((model) => ({
      id: model.slug,
      label: model.display_name || model.slug,
      description: model.description || "",
      efforts: unique((model.supported_reasoning_levels || []).map((item) => item?.effort)),
      default_effort: model.default_reasoning_level || "",
    }));
  const configuredModel = tomlString(configText, "model");
  const configuredEffort = tomlString(configText, "model_reasoning_effort");
  const configured = models.find((model) => model.id === configuredModel) || models[0] || null;
  const defaultModel = {
    id: "default",
    label: configuredModel ? `Default (${configuredModel})` : "Default",
    description: "Use the model selected by the local Codex configuration.",
    efforts: configured?.efforts || [],
    default_effort: configuredEffort || configured?.default_effort || "",
  };
  const sandbox = possibleValues(optionBlock(help, "--sandbox"));
  const approval = bulletValues(optionBlock(help, "--ask-for-approval"));
  return {
    available,
    version,
    error,
    source: unique(["cli_help", models.length ? "models_cache" : "", profiles.length ? "local_profiles" : ""]),
    models: [defaultModel, ...models],
    options: {
      sandbox: sandbox.length ? sandbox : FALLBACK_CODEX_SANDBOXES,
      approval_policy: approval.length ? approval : FALLBACK_CODEX_APPROVALS,
      profile: profiles,
    },
    defaults: {
      model: "default",
      effort: defaultModel.default_effort,
      sandbox: tomlString(configText, "sandbox_mode"),
      approval_policy: tomlString(configText, "approval_policy"),
    },
  };
}

export function parseClaudeCapabilities({
  help,
  version,
  settings = {},
  agents = [],
  available = true,
  error = null,
}) {
  const aliases = quotedAliasValues(optionBlock(help, "--model <model>"));
  const configuredModel = typeof settings.model === "string" ? settings.model : "";
  const modelIds = unique(["default", ...aliases, configuredModel]);
  const efforts = parenthesizedValues(optionBlock(help, "--effort <level>"));
  const permissionModes = parenthesizedValues(optionBlock(help, "--permission-mode <mode>"));
  return {
    available,
    version,
    error,
    source: unique(["cli_help", configuredModel || settings.effortLevel ? "local_settings" : "", agents.length ? "local_agents" : ""]),
    models: modelIds.map((id) => ({
      id,
      label: id === "default" ? "Default" : id,
      description: id === "default" ? "Use the model selected by Claude Code." : "Claude Code model alias advertised by the installed CLI.",
      efforts: efforts.length ? efforts : FALLBACK_CLAUDE_EFFORTS,
      default_effort: settings.effortLevel || "medium",
    })),
    options: {
      effort: efforts.length ? efforts : FALLBACK_CLAUDE_EFFORTS,
      permission_mode: permissionModes.length ? permissionModes : FALLBACK_CLAUDE_PERMISSIONS,
      agent: agents,
    },
    defaults: {
      model: configuredModel || "default",
      effort: settings.effortLevel || "medium",
      permission_mode: settings.permissions?.defaultMode || "",
    },
  };
}

export function detectLocalAgentCapabilities({
  codexBinary = process.platform === "win32" ? "codex.cmd" : "codex",
  claudeBinary = process.platform === "win32" ? "claude.cmd" : "claude",
  cwd = process.cwd(),
} = {}) {
  const codexHelp = runCli(codexBinary, ["--help"]);
  const codexVersion = runCli(codexBinary, ["--version"]);
  const codexHome = process.env.CODEX_HOME || join(homedir(), ".codex");
  const codexConfig = readText(join(codexHome, "config.toml"));
  const codexCache = readJson(join(codexHome, "models_cache.json"));

  const claudeHelp = runCli(claudeBinary, ["--help"]);
  const claudeVersion = runCli(claudeBinary, ["--version"]);
  const claudeHome = process.env.CLAUDE_CONFIG_DIR || join(homedir(), ".claude");
  const claudeSettings = readJson(join(claudeHome, "settings.json")) || {};

  return {
    schema_version: 1,
    detected_at: new Date().toISOString(),
    providers: {
      codex: parseCodexCapabilities({
        help: codexHelp.output,
        version: codexVersion.output,
        cache: codexCache,
        configText: codexConfig,
        profiles: codexProfiles(codexHome, codexConfig),
        available: codexHelp.ok,
        error: codexHelp.error,
      }),
      claude: parseClaudeCapabilities({
        help: claudeHelp.output,
        version: claudeVersion.output,
        settings: claudeSettings,
        agents: claudeAgents(cwd, claudeHome),
        available: claudeHelp.ok,
        error: claudeHelp.error,
      }),
    },
  };
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--codex-bin") options.codexBinary = argv[index += 1];
    else if (argv[index] === "--claude-bin") options.claudeBinary = argv[index += 1];
    else if (argv[index] === "--cwd") options.cwd = argv[index += 1];
    else if (argv[index] === "--pretty") options.pretty = true;
  }
  return options;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  const options = parseArgs(process.argv.slice(2));
  const result = detectLocalAgentCapabilities(options);
  process.stdout.write(`${JSON.stringify(result, null, options.pretty ? 2 : 0)}\n`);
}
