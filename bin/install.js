#!/usr/bin/env node

/**
 * Antigravity Compaction Guard - Universal One-Step Installer
 * Distributes Compaction Guard directly into Google Antigravity plugin directories.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawnSync } = require('child_process');

const VERSION = '1.5.0';
const PACKAGE_ROOT = path.resolve(__dirname, '..');

function printBanner() {
  console.log(`\n============================================================`);
  console.log(`  Antigravity Compaction Guard v${VERSION}`);
  console.log(`  Deterministic Session Evidence & Recovery Engine`);
  console.log(`============================================================\n`);
}

function printUsage() {
  console.log(`Usage: npx antigravity-compaction-guard [options]\n`);
  console.log(`Options:`);
  console.log(`  --global     Install to global Antigravity config (~/.gemini/config/plugins) [default]`);
  console.log(`  --workspace  Install to current workspace (.agents/plugins)`);
  console.log(`  --verify     Run test suite on active installation without copying`);
  console.log(`  --clean      Uninstall/remove Compaction Guard from target directory`);
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
  const skipList = new Set(['.git', '.github', 'node_modules', '__pycache__', '.system_generated']);

  for (const entry of entries) {
    if (skipList.has(entry.name) || entry.name.endsWith('.tmp') || entry.name.endsWith('.pyc') || entry.name.endsWith('.lock')) {
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
  // P0-04: Strict Semver check verifying Python >= 3.10
  for (const cmd of ['python', 'python3']) {
    try {
      const res = spawnSync(cmd, ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'], { stdio: 'pipe' });
      if (res.status === 0) {
        return cmd;
      }
    } catch {
      // try next
    }
  }
  return null;
}

function configureHooksInterpreter(hooksFile, pyCmd) {
  // P0-04: Dynamically configure hooks.json with verified interpreter
  if (!fs.existsSync(hooksFile)) {
    return;
  }
  try {
    const raw = fs.readFileSync(hooksFile, 'utf8');
    const data = JSON.parse(raw);
    const pluginKey = Object.keys(data)[0] || 'compaction-guard';
    const conf = data[pluginKey];
    if (conf) {
      for (const hookType of ['PostToolUse', 'PreInvocation', 'Stop']) {
        const arr = conf[hookType];
        if (Array.isArray(arr)) {
          for (const item of arr) {
            if (item.hooks && Array.isArray(item.hooks)) {
              for (const h of item.hooks) {
                if (h.command && typeof h.command === 'string') {
                  h.command = h.command.replace(/^(python3?)/, pyCmd);
                }
              }
            } else if (item.command && typeof item.command === 'string') {
              item.command = item.command.replace(/^(python3?)/, pyCmd);
            }
          }
        }
      }
    }
    fs.writeFileSync(hooksFile, JSON.stringify(data, null, 2) + '\n', 'utf8');
  } catch (err) {
    console.warn(`[!] Warning: Failed to adapt hooks.json interpreter: ${err.message}`);
  }
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
  const isClean = args.includes('--clean') || args.includes('--uninstall');
  const targetDir = resolveTargetDir(isWorkspace);

  if (isClean) {
    if (fs.existsSync(targetDir)) {
      console.log(`[*] Removing Compaction Guard installation from: ${targetDir}`);
      fs.rmSync(targetDir, { recursive: true, force: true });
      console.log(`[✓] Successfully removed Compaction Guard.\n`);
    } else {
      console.log(`[*] Target directory does not exist: ${targetDir}\n`);
    }
    process.exit(0);
  }

  const pyCmd = findPythonCommand();
  if (!pyCmd) {
    console.error(`[!] Error: Python 3.10+ was not found in PATH.`);
    console.error(`    Compaction Guard strictly requires Python >= 3.10 to execute lifecycle hooks.`);
    process.exit(1);
  }

  if (isVerifyOnly) {
    let verifyDir = targetDir;
    if (!fs.existsSync(verifyDir)) {
      if (!isWorkspace && fs.existsSync(path.join(PACKAGE_ROOT, 'plugin.json')) && fs.existsSync(path.join(PACKAGE_ROOT, 'scripts', 'test_compaction_guard.py'))) {
        console.log(`[*] Target ${targetDir} not installed; verifying repository package directly at ${PACKAGE_ROOT}`);
        verifyDir = PACKAGE_ROOT;
      } else {
        console.error(`[!] Verification failed: target directory does not exist: ${targetDir}`);
        process.exit(1);
      }
    }

    console.log(`[*] Running strict installation verification on: ${verifyDir}`);

    const requiredFiles = [
      path.join(verifyDir, 'plugin.json'),
      path.join(verifyDir, 'hooks.json'),
      path.join(verifyDir, 'rules', 'AGENTS.md'),
      path.join(verifyDir, 'scripts', 'compaction_guard.py'),
      path.join(verifyDir, 'scripts', 'test_compaction_guard.py'),
      path.join(verifyDir, 'src', '__init__.py')
    ];

    for (const rf of requiredFiles) {
      if (!fs.existsSync(rf)) {
        console.error(`[!] Verification failed: required file missing: ${rf}`);
        process.exit(1);
      }
    }

    const testScript = path.join(verifyDir, 'scripts', 'test_compaction_guard.py');
    console.log(`[*] Executing verification test suite via ${pyCmd}...`);
    const testRes = spawnSync(pyCmd, [testScript], { stdio: 'inherit' });
    if (testRes.status !== 0) {
      console.error(`[!] Verification failed: test suite exited with code ${testRes.status}`);
      process.exit(testRes.status || 1);
    }

    const parityScript = path.join(verifyDir, 'scripts', 'test_differential_parity.py');
    if (fs.existsSync(parityScript)) {
      console.log(`[*] Executing shadow differential parity suite via ${pyCmd}...`);
      const parityRes = spawnSync(pyCmd, [parityScript], { stdio: 'inherit' });
      if (parityRes.status !== 0) {
        console.error(`[!] Verification failed: parity suite exited with code ${parityRes.status}`);
        process.exit(parityRes.status || 1);
      }
    }

    console.log(`\n[✓] Verification passed! Compaction Guard v${VERSION} is structurally intact and fully operational.\n`);
    process.exit(0);
  }

  console.log(`[*] Target directory: ${targetDir}`);
  console.log(`[*] Copying plugin assets and hook scripts...`);
  try {
    copyDirectory(PACKAGE_ROOT, targetDir);
    console.log(`[✓] Successfully staged files to ${targetDir}`);
  } catch (err) {
    console.error(`[!] Failed to copy plugin files: ${err.message}`);
    process.exit(1);
  }

  const hooksFile = path.join(targetDir, 'hooks.json');
  configureHooksInterpreter(hooksFile, pyCmd);

  const testScript = path.join(targetDir, 'scripts', 'test_compaction_guard.py');
  if (fs.existsSync(testScript)) {
    console.log(`[*] Running self-test suite via ${pyCmd}...`);
    const testRes = spawnSync(pyCmd, [testScript], { stdio: 'inherit' });
    if (testRes.status !== 0) {
      console.error(`[!] Test suite reported errors: exit code ${testRes.status}`);
      process.exit(testRes.status || 1);
    }
    console.log(`\n[✓] All lifecycle hook tests passed!`);
  }

  console.log(`\n[✓] Compaction Guard v${VERSION} is successfully installed and active.`);
  console.log(`    Mode: ${isWorkspace ? 'Workspace-Local (.agents/plugins/)' : 'Global (~/.gemini/config/plugins/)'}`);
  console.log(`    Interpreter: ${pyCmd} (Python >= 3.10 verified)`);
  console.log(`    Hooks Manifest: ${path.join(targetDir, 'hooks.json')}\n`);
}

main();
