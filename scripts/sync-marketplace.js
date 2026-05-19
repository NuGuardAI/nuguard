#!/usr/bin/env node
/**
 * Reads the canonical version from pyproject.toml and propagates it to every
 * plugin/marketplace manifest in the repo so they stay in sync.
 */
import { readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

// Canonical version lives in pyproject.toml
const pyproject = readFileSync(join(ROOT, 'pyproject.toml'), 'utf8');
const match = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
if (!match) {
  console.error('ERROR: version field not found in pyproject.toml');
  process.exit(1);
}
const VERSION = match[1];
console.log(`version: ${VERSION}\n`);

function patchJson(relPath, patcher) {
  const abs = join(ROOT, relPath);
  const data = JSON.parse(readFileSync(abs, 'utf8'));
  patcher(data);
  writeFileSync(abs, JSON.stringify(data, null, 2) + '\n');
  console.log(`  updated ${relPath}`);
}

function patchYaml(relPath) {
  const abs = join(ROOT, relPath);
  const src = readFileSync(abs, 'utf8');
  const out = src.replace(/^version:\s*"[^"]*"/m, `version: "${VERSION}"`);
  writeFileSync(abs, out);
  console.log(`  updated ${relPath}`);
}

// npm package
patchJson('npm/package.json', d => { d.version = VERSION; });

// Root-level Claude plugin manifests
patchJson('.claude-plugin/plugin.json', d => { d.version = VERSION; });
patchJson('.claude-plugin/marketplace.json', d => {
  d.metadata.version = VERSION;
  for (const p of d.plugins) p.version = VERSION;
});

// plugin/ subdirectory manifests
patchJson('plugin/.claude-plugin/plugin.json', d => { d.version = VERSION; });
patchJson('plugin/.claude-plugin/marketplace.json', d => {
  d.metadata.version = VERSION;
  for (const p of d.plugins) p.version = VERSION;
});

// Root-level marketplace.json (Claude Code marketplace registry)
patchJson('marketplace.json', d => {
  d.metadata.version = VERSION;
  for (const p of d.plugins) p.version = VERSION;
});

// Other extension manifests
patchJson('gemini-extension.json', d => { d.version = VERSION; });
patchJson('openclaw.plugin.json', d => { d.version = VERSION; });

// Smithery YAML
patchYaml('smithery.yaml');

console.log(`\nAll manifests synced to ${VERSION}`);
