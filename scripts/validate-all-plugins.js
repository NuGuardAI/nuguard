#!/usr/bin/env node
/**
 * Validates the structural integrity of all NuGuard Claude Code plugin manifests.
 * Checks: JSON validity, required fields, path references, agent frontmatter,
 * skill SKILL.md presence, and cross-manifest version consistency.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

let errors = 0;
let warnings = 0;

function fail(msg) { console.error(`  FAIL  ${msg}`); errors++; }
function warn(msg) { console.warn(`  WARN  ${msg}`); warnings++; }
function ok(msg)   { console.log(`  ok    ${msg}`); }

function readJson(relPath) {
  const abs = join(ROOT, relPath);
  if (!existsSync(abs)) { fail(`${relPath} not found`); return null; }
  try {
    return JSON.parse(readFileSync(abs, 'utf8'));
  } catch (e) {
    fail(`${relPath} is not valid JSON: ${e.message}`);
    return null;
  }
}

function readText(relPath) {
  const abs = join(ROOT, relPath);
  if (!existsSync(abs)) { fail(`${relPath} not found`); return null; }
  try {
    return readFileSync(abs, 'utf8');
  } catch (e) {
    fail(`${relPath} could not be read: ${e.message}`);
    return null;
  }
}

function checkExists(relPath, label) {
  if (!existsSync(join(ROOT, relPath))) { fail(`${label}: path not found — ${relPath}`); return false; }
  ok(`${label} exists`);
  return true;
}

function checkDeclaredPaths(value, basePath, label) {
  const paths = typeof value === 'string' ? [value] : value;
  if (!Array.isArray(paths) || paths.some(path => typeof path !== 'string')) {
    fail(`${label}: expected a string or array of strings`);
    return;
  }
  for (const path of paths) {
    checkExists(join(basePath, path.replace(/^\.\//, '')), label);
  }
}

// ── 1. Canonical version ──────────────────────────────────────────────────────
console.log('\n[1] Canonical version');
const pyproject = readText('pyproject.toml');
if (pyproject === null) process.exit(1);
const vMatch = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
if (!vMatch) { fail('version not found in pyproject.toml'); process.exit(1); }
const VERSION = vMatch[1];
ok(`pyproject.toml version = ${VERSION}`);

const runtime = readText('nuguard/__init__.py');
if (runtime !== null) {
  const runtimeMatch = runtime.match(/^__version__\s*=\s*["']([^"']+)["']/m);
  if (!runtimeMatch) fail('nuguard/__init__.py __version__ not found');
  else if (runtimeMatch[1] !== VERSION) fail(`nuguard/__init__.py version ${runtimeMatch[1]} ≠ ${VERSION}`);
  else ok(`nuguard/__init__.py version = ${runtimeMatch[1]}`);
}

const lockfile = readText('uv.lock');
if (lockfile !== null) {
  const projectPackages = lockfile
    .split(/^\[\[package\]\]\s*$/m)
    .filter(block => /^name\s*=\s*"nuguard"\s*$/m.test(block));
  if (projectPackages.length !== 1) fail(`uv.lock expected one nuguard package, found ${projectPackages.length}`);
  else {
    const lockMatch = projectPackages[0].match(/^version\s*=\s*"([^"]+)"/m);
    if (!lockMatch) fail('uv.lock nuguard package missing version');
    else if (lockMatch[1] !== VERSION) fail(`uv.lock nuguard version ${lockMatch[1]} ≠ ${VERSION}`);
    else ok(`uv.lock nuguard version = ${lockMatch[1]}`);
  }
}

// ── 2. Root .claude-plugin/plugin.json ───────────────────────────────────────
console.log('\n[2] Root .claude-plugin/plugin.json');
const rootPlugin = readJson('.claude-plugin/plugin.json');
if (rootPlugin) {
  if (!rootPlugin.version)              fail('.claude-plugin/plugin.json missing version');
  else if (rootPlugin.version !== VERSION) fail(`.claude-plugin/plugin.json version ${rootPlugin.version} ≠ ${VERSION}`);
  else ok(`version = ${rootPlugin.version}`);

  // agents/commands/skills must NOT be string paths — auto-discovered via symlinks at root
  for (const field of ['agents', 'commands', 'skills']) {
    if (rootPlugin[field]) fail(`.claude-plugin/plugin.json must not declare "${field}" as a string path — use root symlinks for auto-discovery`);
  }

  if (rootPlugin.mcpServers) checkDeclaredPaths(rootPlugin.mcpServers, '', 'mcpServers path');
}

// Root symlinks enable convention-based auto-discovery
console.log('\n[2b] Root auto-discovery symlinks');
for (const link of ['agents', 'commands', 'skills']) {
  checkExists(link, `root ${link} symlink`);
}

// ── 3. plugin/.claude-plugin/plugin.json ─────────────────────────────────────
console.log('\n[3] plugin/.claude-plugin/plugin.json');
const subPlugin = readJson('plugin/.claude-plugin/plugin.json');
if (subPlugin) {
  if (!subPlugin.version)              fail('plugin/.claude-plugin/plugin.json missing version');
  else if (subPlugin.version !== VERSION) fail(`plugin/.claude-plugin/plugin.json version ${subPlugin.version} ≠ ${VERSION}`);
  else ok(`version = ${subPlugin.version}`);

  // agents is declared in plugin.json; commands/skills are in marketplace.json
  if (!subPlugin.agents) warn('plugin/.claude-plugin/plugin.json missing "agents" field');
  else checkDeclaredPaths(subPlugin.agents, 'plugin', 'plugin agents path');
}

// ── 4. Marketplace manifests ──────────────────────────────────────────────────
console.log('\n[4] Marketplace manifests');
for (const relPath of ['.claude-plugin/marketplace.json', 'plugin/.claude-plugin/marketplace.json', 'marketplace.json']) {
  const m = readJson(relPath);
  if (!m) continue;
  const mv = m.metadata?.version;
  if (!mv) fail(`${relPath} missing metadata.version`);
  else if (mv !== VERSION) fail(`${relPath} metadata.version ${mv} ≠ ${VERSION}`);
  else ok(`${relPath} metadata.version = ${mv}`);

  if (!Array.isArray(m.plugins)) {
    fail(`${relPath} plugins must be an array`);
  } else {
    for (const p of m.plugins) {
      if (!p || typeof p !== 'object' || Array.isArray(p)) {
        fail(`${relPath} plugins[] must contain objects`);
      } else if (!p.version) {
        fail(`${relPath} plugins[] missing version`);
      } else if (p.version !== VERSION) {
        fail(`${relPath} plugins[].version ${p.version} ≠ ${VERSION}`);
      } else {
        ok(`${relPath} plugins[${p.name}].version = ${p.version}`);
      }
    }
  }
}

// ── 5. Other manifests ────────────────────────────────────────────────────────
console.log('\n[5] Other manifests');
for (const [relPath, vPath] of [
  ['npm/package.json',      'version'],
  ['gemini-extension.json', 'version'],
  ['openclaw.plugin.json',  'version'],
]) {
  const m = readJson(relPath);
  if (m) {
    const v = m[vPath];
    if (!v) fail(`${relPath} missing ${vPath}`);
    else if (v !== VERSION) fail(`${relPath} ${vPath} ${v} ≠ ${VERSION}`);
    else ok(`${relPath} ${vPath} = ${v}`);
  }
}

// smithery.yaml
const smithery = readText('smithery.yaml');
if (smithery !== null) {
  const sy = smithery.match(/^version:\s*"([^"]+)"/m);
  if (!sy) fail('smithery.yaml version not found');
  else if (sy[1] !== VERSION) fail(`smithery.yaml version ${sy[1]} ≠ ${VERSION}`);
  else ok(`smithery.yaml version = ${sy[1]}`);
}

// ── 6. Agent files ────────────────────────────────────────────────────────────
console.log('\n[6] Agent files');
const agentsDir = join(ROOT, 'plugin/agents');
if (existsSync(agentsDir)) {
  const { readdirSync } = await import('fs');
  for (const f of readdirSync(agentsDir).filter(f => f.endsWith('.md'))) {
    const src = readFileSync(join(agentsDir, f), 'utf8');
    const fm = src.match(/^---\r?\n([\s\S]*?)\r?\n---/);
    if (!fm) { fail(`plugin/agents/${f}: no YAML frontmatter`); continue; }
    for (const field of ['name', 'description', 'model', 'color']) {
      if (!fm[1].includes(`${field}:`)) fail(`plugin/agents/${f}: frontmatter missing "${field}"`);
      else ok(`plugin/agents/${f}: has ${field}`);
    }
  }
} else warn('plugin/agents/ directory not found');

// ── 7. Skill directories ──────────────────────────────────────────────────────
console.log('\n[7] Skill directories');
const skillsBase = 'plugin/skills';
const skillDirs = ['ai-security-review', 'sbom-analysis'];
for (const s of skillDirs) {
  checkExists(join(skillsBase, s, 'SKILL.md'), `${s}/SKILL.md`);
}

// ── 8. Commands directory ─────────────────────────────────────────────────────
console.log('\n[8] Commands directory');
checkExists('plugin/commands', 'plugin/commands/');
if (existsSync(join(ROOT, 'plugin/commands'))) {
  const { readdirSync } = await import('fs');
  const cmds = readdirSync(join(ROOT, 'plugin/commands')).filter(f => f.endsWith('.md'));
  if (cmds.length === 0) warn('plugin/commands/ has no .md files');
  else ok(`${cmds.length} command file(s) found`);
}

// ── Summary ───────────────────────────────────────────────────────────────────
console.log(`\n${'─'.repeat(50)}`);
if (errors > 0) {
  console.error(`FAILED — ${errors} error(s), ${warnings} warning(s)`);
  process.exit(1);
} else {
  console.log(`PASSED — 0 errors, ${warnings} warning(s)`);
}
