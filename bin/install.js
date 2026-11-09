#!/usr/bin/env node

/**
 * Antigravity Compaction Guard - Universal One-Step Installer
 * Distributes Compaction Guard directly into Google Antigravity plugin directories.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

const VERSION = '1.2.1';
const PACKAGE_ROOT = path.resolve(__dirname, '..');

function printBanner() {
  console.log(`\n============================================================`);
  console.log(`  Antigravity Compaction Guard v${VERSION}`);
  console.log(`  Deterministic Anti-Amnesia Lifecycle Hook Engine`);
  console.log(`============================================================\n`);
}

function printUsage() {
  console.log(`Usage: npx antigravity-compaction-guard [options]\n`);
  console.log(`Options:`);
  console.log(`  --global     Install to global Antigravity config (~/.gemini/config/plugins) [default]`);
  console.log(`  --workspace  Install to current workspace (.agents/plugins)`);
  console.log(`  --verify     Run test suite on active installation without copying`);
  console.log(`  --help       Show this help message\n`);
}

function resolveTargetDir(isWorkspace) {
  if (isWorkspace) {
    return path.resolve(process.cwd(), '.agents', 'plugins', 'compaction-guard');
  }
  return path.resolve(os.homedir(), '.gemini', 'config', 'plugins', 'compaction-guard');
}

function copyDirectory(src, dest) {
  if (!fs.existsSync(dest)) {
    fs.mkdirSync(dest, { recursive: true });
  }

  const entries = fs.readdirSync(src, { withFileTypes: true });
  const skipList = new Set(['.git', '.github', 'bin', 'node_modules', '__pycache__', '.system_generated']);

  for (const entry of entries) {
    if (skipList.has(entry.name) || entry.name.endsWith('.tmp') || entry.name.endsWith('.pyc')) {
      continue;
    }
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);

    if (entry.isDirectory()) {
      copyDirectory(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function findPythonCommand() {
  for (const cmd of ['python', 'python3']) {
    try {
      execSync(`${cmd} --version`, { stdio: 'pipe' });
      return cmd;
    } catch {
      // try next
    }
  }
  return null;
}

function main() {
  const args = process.argv.slice(2);
  printBanner();

  if (args.includes('--help') || args.includes('-h')) {
    printUsage();
    process.exit(0);
  }

  const isWorkspace = args.includes('--workspace') || args.includes('--project');
  const isVerifyOnly = args.includes('--verify');
  const targetDir = resolveTargetDir(isWorkspace);

  if (!isVerifyOnly) {
    console.log(`[*] Target directory: ${targetDir}`);
    console.log(`[*] Copying plugin assets and hook scripts...`);
    try {
      copyDirectory(PACKAGE_ROOT, targetDir);
      console.log(`[✓] Successfully staged files to ${targetDir}`);
    } catch (err) {
      console.error(`[!] Failed to copy plugin files: ${err.message}`);
      process.exit(1);
    }
  }

  const pyCmd = findPythonCommand();
  if (!pyCmd) {
    console.warn(`[!] Warning: Python 3.10+ was not found in PATH.`);
    console.warn(`    Compaction Guard requires Python to execute lifecycle hooks at runtime.`);
    console.log(`\n[✓] Plugin files deployed. Please ensure Python is installed and accessible.\n`);
    process.exit(0);
  }

  const testScript = path.join(targetDir, 'scripts', 'test_compaction_guard.py');
  if (fs.existsSync(testScript)) {
    console.log(`[*] Running self-test suite via ${pyCmd}...`);
    try {
      execSync(`${pyCmd} "${testScript}"`, { stdio: 'inherit' });
      console.log(`\n[✓] All lifecycle hook tests passed!`);
    } catch (err) {
      console.error(`[!] Test suite reported errors: ${err.message}`);
      process.exit(1);
    }
  }

  console.log(`\n[✓] Compaction Guard v${VERSION} is successfully installed and active.`);
  console.log(`    Mode: ${isWorkspace ? 'Workspace-Local (.agents/plugins/)' : 'Global (~/.gemini/config/plugins/)'}`);
  console.log(`    Google Antigravity will automatically load hooks from:`);
  console.log(`    ${path.join(targetDir, 'hooks.json')}\n`);
}

main();
