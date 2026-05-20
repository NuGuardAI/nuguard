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

function checkExists(relPath, label) {
  if (!existsSync(join(ROOT, relPath))) { fail(`${label}: path not found — ${relPath}`); return false; }
  ok(`${label} exists`);
  return true;
}

// ── 1. Canonical version ──────────────────────────────────────────────────────
console.log('\n[1] Canonical version');
const pyproject = readFileSync(join(ROOT, 'pyproject.toml'), 'utf8');
const vMatch = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
if (!vMatch) { fail('version not found in pyproject.toml'); process.exit(1); }
const VERSION = vMatch[1];
ok(`pyproject.toml version = ${VERSION}`);

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

  if (rootPlugin.mcpServers) checkExists(rootPlugin.mcpServers.replace(/^\.\//, ''), 'mcpServers path');
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
  else checkExists(join('plugin', subPlugin.agents).replace(/\/\.\//g, '/'), 'plugin agents path');
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

  for (const p of (m.plugins || [])) {
    if (p.version && p.version !== VERSION)
      fail(`${relPath} plugins[].version ${p.version} ≠ ${VERSION}`);
    else if (p.version) ok(`${relPath} plugins[${p.name}].version = ${p.version}`);
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
const smithery = readFileSync(join(ROOT, 'smithery.yaml'), 'utf8');
const sy = smithery.match(/^version:\s*"([^"]+)"/m);
if (!sy) fail('smithery.yaml version not found');
else if (sy[1] !== VERSION) fail(`smithery.yaml version ${sy[1]} ≠ ${VERSION}`);
else ok(`smithery.yaml version = ${sy[1]}`);

// ── 6. Agent files ────────────────────────────────────────────────────────────
console.log('\n[6] Agent files');
const agentsDir = join(ROOT, 'plugin/agents');
if (existsSync(agentsDir)) {
  const { readdirSync } = await import('fs');
  for (const f of readdirSync(agentsDir).filter(f => f.endsWith('.md'))) {
    const src = readFileSync(join(agentsDir, f), 'utf8');
    const fm = src.match(/^---\n([\s\S]*?)\n---/);
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
