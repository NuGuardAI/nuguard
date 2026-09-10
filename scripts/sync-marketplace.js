#!/usr/bin/env node
/**
 * Optionally bumps the canonical pyproject.toml version with uv, then
 * transactionally propagates it to every NuGuard-owned version projection.
 */
import { readFileSync, writeFileSync } from 'fs';
import { spawnSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const TARGET_VERSION = process.argv[2];
if (process.env.npm_lifecycle_event === 'version:bump' && !TARGET_VERSION) {
  console.error('ERROR: version:bump requires a target version');
  process.exit(1);
}
const JSON_TARGETS = [
  'npm/package.json',
  '.claude-plugin/plugin.json',
  '.claude-plugin/marketplace.json',
  'plugin/.claude-plugin/plugin.json',
  'plugin/.claude-plugin/marketplace.json',
  'marketplace.json',
  'gemini-extension.json',
  'openclaw.plugin.json',
];
const VERSION_FILES = [
  'pyproject.toml',
  'uv.lock',
  'nuguard/__init__.py',
  ...JSON_TARGETS,
  'smithery.yaml',
];
let originals = new Map();

function synchronize() {
  originals = new Map(
    VERSION_FILES.map(relPath => [relPath, readFileSync(join(ROOT, relPath))]),
  );

function requireObject(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function requireVersion(object, label) {
  if (typeof object.version !== 'string' || object.version.length === 0) {
    throw new Error(`${label} missing string version field`);
  }
}

function loadJson(relPath) {
  const data = JSON.parse(originals.get(relPath).toString('utf8'));
  return requireObject(data, relPath);
}

  const jsonDocuments = new Map(JSON_TARGETS.map(path => [path, loadJson(path)]));

for (const path of [
  'npm/package.json',
  '.claude-plugin/plugin.json',
  'plugin/.claude-plugin/plugin.json',
  'gemini-extension.json',
  'openclaw.plugin.json',
]) {
  requireVersion(jsonDocuments.get(path), path);
}

for (const path of [
  '.claude-plugin/marketplace.json',
  'plugin/.claude-plugin/marketplace.json',
  'marketplace.json',
]) {
  const document = jsonDocuments.get(path);
  const metadata = requireObject(document.metadata, `${path} metadata`);
  requireVersion(metadata, `${path} metadata`);
  if (!Array.isArray(document.plugins) || document.plugins.length === 0) {
    throw new Error(`${path} plugins must be a non-empty array`);
  }
  for (const [index, plugin] of document.plugins.entries()) {
    requireVersion(requireObject(plugin, `${path} plugins[${index}]`), `${path} plugins[${index}]`);
  }
}

  const initSource = originals.get('nuguard/__init__.py').toString('utf8');
  if (!/^__version__\s*=\s*["'][^"']+["']/m.test(initSource)) {
    throw new Error('nuguard/__init__.py missing __version__ assignment');
  }
  const smitherySource = originals.get('smithery.yaml').toString('utf8');
  if (!/^version:\s*"[^"]+"/m.test(smitherySource)) {
    throw new Error('smithery.yaml missing quoted version field');
  }

  const originalPyproject = originals.get('pyproject.toml').toString('utf8');
  const originalVersion = originalPyproject.match(/^version\s*=\s*"([^"]+)"/m);
  if (!originalVersion) {
    throw new Error('version field not found in pyproject.toml');
  }

  if (TARGET_VERSION) {
    const result = spawnSync('uv', ['version', TARGET_VERSION, '--no-sync'], {
      cwd: ROOT,
      encoding: 'utf8',
    });
    if (result.error) {
      throw new Error(`uv version failed: ${result.error.message}`);
    }
    if (result.status !== 0) {
      const details = (result.stderr || result.stdout || 'unknown error').trim();
      throw new Error(`uv version failed: ${details}`);
    }
  }

  // Canonical version lives in pyproject.toml
  const pyproject = readFileSync(join(ROOT, 'pyproject.toml'), 'utf8');
  const match = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
  if (!match) {
    throw new Error('version field not found in pyproject.toml');
  }
  const VERSION = match[1];
  console.log(`version: ${VERSION}\n`);

function patchJson(relPath, patcher) {
  const abs = join(ROOT, relPath);
  const data = jsonDocuments.get(relPath);
  const before = JSON.stringify(data);
  patcher(data);
  if (JSON.stringify(data) === before) {
    console.log(`  unchanged ${relPath}`);
    return;
  }
  writeFileSync(abs, JSON.stringify(data, null, 2) + '\n');
  console.log(`  updated ${relPath}`);
}

function patchYaml(relPath) {
  const abs = join(ROOT, relPath);
  const src = originals.get(relPath).toString('utf8');
  const out = src.replace(/^version:\s*"[^"]*"/m, `version: "${VERSION}"`);
  if (out === src) {
    console.log(`  unchanged ${relPath}`);
    return;
  }
  writeFileSync(abs, out);
  console.log(`  updated ${relPath}`);
}

  const updatedInit = initSource.replace(
    /^(__version__\s*=\s*)["'][^"']+["']/m,
    `$1"${VERSION}"`,
  );
  if (updatedInit === initSource) {
    console.log('  unchanged nuguard/__init__.py');
  } else {
    writeFileSync(join(ROOT, 'nuguard/__init__.py'), updatedInit);
    console.log('  updated nuguard/__init__.py');
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
}

try {
  synchronize();
} catch (error) {
  for (const [relPath, content] of originals) {
    writeFileSync(join(ROOT, relPath), content);
  }
  console.error(`ERROR: ${error.message}`);
  console.error('All version files were restored.');
  process.exit(1);
}
