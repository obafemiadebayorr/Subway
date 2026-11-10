const vscode = require('vscode');
const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');
const crypto = require('crypto');
const cp = require('child_process');
const EXT_VERSION = require('./package.json').version;
// Must live inside the extension folder: a path like ../extensions/inline-estimate
// resolves outside the installed extension and stops activate() from running at all.
const est = require('./inline-estimate');

const terminalStates = new WeakMap();
const activeTerminals = new Set();
const eventRetryBuffer = [];
let eventPostChain = Promise.resolve();
let pollInterval = null;
let probePollInterval = null;
let heartbeatInterval = null;
let notificationWatcher = null;
let probeRunning = false;
let hudSharingEnabled = false;
let sharingPollInterval = null;

function isTerminalSharingActive() {
  const vsSetting = vscode.workspace.getConfiguration('subway').get('terminalSharing.enabled', false);
  return !!(hudSharingEnabled || vsSetting);
}

async function updateHudSharingStatus() {
  const data = await getJson('/api/terminals');
  if (data && typeof data.sharing === 'boolean') {
    hudSharingEnabled = data.sharing;
  }
}

function getHudPort() {
  try {
    const hudPath = path.join(os.homedir(), '.subway-context', 'hud.json');
    if (fs.existsSync(hudPath)) {
      const data = JSON.parse(fs.readFileSync(hudPath, 'utf8'));
      if (data && data.port) {
        return Number(data.port);
      }
    }
  } catch (e) {}
  return 7890;
}

function checkHealth(port) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/healthz`, { timeout: 2000 }, (res) => {
      resolve(res.statusCode >= 200 && res.statusCode < 400);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

function getHudToken() {
  try {
    const tokenPath = path.join(os.homedir(), '.subway-context', 'hud_token');
    return fs.readFileSync(tokenPath, 'utf8').trim();
  } catch (e) {
    return '';
  }
}

function postJson(pathStr, bodyObj) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (!settled) {
        settled = true;
        resolve(result);
      }
    };
    try {
      const req = http.request({
        hostname: '127.0.0.1',
        port: getHudPort(),
        path: pathStr,
        method: 'POST',
        timeout: 2000,
        headers: {
          'X-Subway-Token': getHudToken(),
          'Content-Type': 'application/json'
        }
      }, (res) => {
        res.resume();
        finish(res.statusCode >= 200 && res.statusCode < 300);
      });
      req.on('error', () => finish(false));
      req.on('timeout', () => {
        req.destroy();
        finish(false);
      });
      req.end(JSON.stringify(bodyObj));
    } catch (e) {
      finish(false);
    }
  });
}

function getJson(pathStr) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (!settled) {
        settled = true;
        resolve(result);
      }
    };
    try {
      const req = http.request({
        hostname: '127.0.0.1',
        port: getHudPort(),
        path: pathStr,
        method: 'GET',
        timeout: 2000,
        headers: {
          'X-Subway-Token': getHudToken(),
          'Content-Type': 'application/json'
        }
      }, (res) => {
        let response = '';
        res.setEncoding('utf8');
        res.on('data', (chunk) => { response += chunk; });
        res.on('end', () => {
          if (res.statusCode < 200 || res.statusCode >= 300) {
            finish(null);
            return;
          }
          try {
            finish(JSON.parse(response));
          } catch (e) {
            finish(null);
          }
        });
      });
      req.on('error', () => finish(null));
      req.on('timeout', () => {
        req.destroy();
        finish(null);
      });
      req.end();
    } catch (e) {
      finish(null);
    }
  });
}

function getTerminalState(terminal) {
  let entry = terminalStates.get(terminal);
  if (!entry) {
    entry = {
      id: crypto.randomUUID(),
      windowId: vscode.env.sessionId,
      sharing: false,
      allowCommands: false,
      runInFlight: false
    };
    terminalStates.set(terminal, entry);
    activeTerminals.add(terminal);
  }
  return entry;
}

function terminalOptInBody(terminal, entry) {
  return {
    terminal_id: entry.id,
    name: terminal.name,
    window_id: entry.windowId,
    folder: (vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || ''),
    share_enabled: entry.sharing,
    allow_commands: entry.allowCommands,
    shell_integration: !!terminal.shellIntegration
  };
}

function bufferTerminalEvent(pathStr, body) {
  if (eventRetryBuffer.length >= 50) eventRetryBuffer.shift();
  eventRetryBuffer.push({ path: pathStr, body });
}

function postTerminalEvent(body) {
  if (!isTerminalSharingActive()) return;
  eventPostChain = eventPostChain.then(async () => {
    if (!isTerminalSharingActive()) {
      eventRetryBuffer.length = 0;
      return;
    }
    while (eventRetryBuffer.length) {
      if (!isTerminalSharingActive()) {
        eventRetryBuffer.length = 0;
        return;
      }
      const queued = eventRetryBuffer[0];
      if (!(await postJson(queued.path, queued.body))) break;
      eventRetryBuffer.shift();
    }
    if (!(await postJson('/api/terminal_event', body))) {
      if (isTerminalSharingActive()) {
        bufferTerminalEvent('/api/terminal_event', body);
      }
    }
  });
}

async function readExecutionOutput(execution) {
  let output = '';
  try {
    for await (const chunk of execution.read()) {
      output += chunk;
      if (output.length > 65536) {
        output = output.slice(0, 65536);
        break;
      }
    }
  } catch (err) {
    // Some shells and terminals do not support reading execution output.
  }
  return output;
}

async function pollTerminalRuns(terminal, entry) {
  if (entry.pollInFlight || entry.runInFlight) return;
  entry.pollInFlight = true;
  try {
    const data = await getJson(`/api/pending_runs?terminal_id=${encodeURIComponent(entry.id)}`);
    if (terminalStates.get(terminal) !== entry || !data || !Array.isArray(data.runs)) return;
    const run = data.runs.find((candidate) => candidate.status === 'pending');
    if (!run) return;

    entry.runInFlight = true;
    if (!terminal.shellIntegration) {
      await postJson('/api/run_result', {
        run_id: run.id, output: '', exit_code: null, error: 'no shell integration'
      });
      entry.runInFlight = false;
      return;
    }

    const pendingRun = { runId: run.id, execution: null };
    entry.pendingRun = pendingRun;
    try {
      pendingRun.execution = terminal.shellIntegration.executeCommand(run.command);
    } catch (err) {
      await postJson('/api/run_result', {
        run_id: run.id, output: '', exit_code: null, error: 'execution failed'
      });
      entry.pendingRun = null;
      entry.runInFlight = false;
    }
  } finally {
    entry.pollInFlight = false;
  }
}

function writeProbeResult(data, log) {
  try {
    const dir = path.join(os.homedir(), '.subway-context');
    fs.mkdirSync(dir, { recursive: true });
    const targetFile = path.join(dir, 'inline_probe.json');
    const tmpFile = `${targetFile}.tmp-${process.pid}`;
    fs.writeFileSync(tmpFile, JSON.stringify(data, null, 2), 'utf8');
    fs.renameSync(tmpFile, targetFile);
  } catch (err) {
    const msg = `Failed to write inline probe result: ${err && err.message ? err.message : String(err)}`;
    if (typeof log === 'function') {
      log(msg);
    } else {
      console.error(msg);
    }
  }
}

function claimProbeRequest() {
  const reqPath = path.join(os.homedir(), '.subway-context', 'inline_probe_request.json');
  let req = null;
  try {
    req = JSON.parse(fs.readFileSync(reqPath, 'utf8'));
  } catch (_) {
    return null;
  }

  if (
    !req ||
    typeof req.nonce !== 'string' ||
    req.nonce.length < 16 ||
    typeof req.expires_at !== 'number' ||
    Date.now() >= req.expires_at * 1000 ||
    req.used
  ) {
    return null;
  }

  const claimedPath = reqPath + '.claimed-' + process.pid;
  try {
    fs.renameSync(reqPath, claimedPath);
  } catch (_) {
    return null;
  }

  try {
    const updated = {
      ...req,
      used: true,
      claimed_by: process.pid,
      claimed_version: EXT_VERSION
    };
    const tmpPath = `${reqPath}.tmp-${process.pid}`;
    fs.writeFileSync(tmpPath, JSON.stringify(updated, null, 2), 'utf8');
    fs.renameSync(tmpPath, reqPath);
  } catch (_) {}

  try {
    fs.unlinkSync(claimedPath);
  } catch (_) {}

  return req;
}

function playNotificationChime() {
  try {
    const cp = require('child_process');
    if (process.platform === 'darwin') {
      cp.exec('afplay /System/Library/Sounds/Glass.aiff', () => {});
    } else if (process.platform === 'win32') {
      cp.exec('powershell -NoProfile -Command "(New-Object Media.SoundPlayer \'C:\\Windows\\Media\\notify.wav\').PlaySync()"', () => {});
    } else if (process.platform === 'linux') {
      cp.exec('paplay /usr/share/sounds/freedesktop/stereo/complete.oga || aplay /usr/share/sounds/alsa/Front_Center.wav', () => {});
    }
  } catch (_) {}
}

function checkNotifications() {
  try {
    const dir = path.join(os.homedir(), '.subway-context', 'editor_windows');
    const notifyFile = path.join(dir, `${process.pid}.notify.json`);
    if (fs.existsSync(notifyFile)) {
      const raw = fs.readFileSync(notifyFile, 'utf8');
      try { fs.unlinkSync(notifyFile); } catch (_) {}
      const event = JSON.parse(raw);
      if (event && (event.message || event.title)) {
        const text = event.title ? `${event.title}: ${event.message}` : event.message;
        playNotificationChime();
        vscode.window.showInformationMessage(text);
      }
    }
  } catch (_) {}
}

function startNotificationListener() {
  const dir = path.join(os.homedir(), '.subway-context', 'editor_windows');
  try { fs.mkdirSync(dir, { recursive: true }); } catch (_) {}

  try {
    if (!notificationWatcher) {
      notificationWatcher = fs.watch(dir, (eventType, filename) => {
        if (!filename || (filename.includes(String(process.pid)) && filename.endsWith('.notify.json'))) {
          checkNotifications();
        }
      });
    }
  } catch (_) {}

  checkNotifications();
}

function writeWindowHeartbeat() {
  try {
    const dir = path.join(os.homedir(), '.subway-context', 'editor_windows');
    fs.mkdirSync(dir, { recursive: true });
    const targetFile = path.join(dir, `${process.pid}.json`);
    const tmpFile = `${targetFile}.tmp-${process.pid}`;
    const data = {
      version: EXT_VERSION,
      pid: process.pid,
      app: vscode.env.appName,
      uri_scheme: vscode.env.uriScheme,
      workspace: (vscode.workspace.workspaceFolders || []).map(f => f.name),
      probe: true,
      at: new Date().toISOString()
    };
    fs.writeFileSync(tmpFile, JSON.stringify(data, null, 2), 'utf8');
    fs.renameSync(tmpFile, targetFile);

    checkNotifications();
  } catch (_) {}
}

async function handleProbeUri(uri) {
  let nonce = '';
  try {
    nonce = new URLSearchParams(uri.query).get('nonce') || '';
  } catch (_) {
    nonce = '';
  }

  if (probeRunning) {
    vscode.window.showInformationMessage('Subway inline probe is already running.');
    return;
  }

  const req = claimProbeRequest();
  const preapproved = Boolean(req && req.nonce === nonce);
  probeRunning = true;
  try {
    await probeInlineCompletion({ preapproved, nonce });
  } finally {
    probeRunning = false;
  }
}

async function probeInlineCompletion(opts = {}) {
  let method = 'none';
  let ms = 0;
  let text = '';
  let found = [];

  if (opts.preapproved === true) {
    // Preapproved via valid request nonce: skip modal confirm
  } else {
    const ok = await vscode.window.showWarningMessage(
      'This asks Copilot for one inline completion in a scratch file. It uses about one of your monthly Copilot completions.',
      { modal: true },
      'Request one completion'
    );
    if (ok !== 'Request one completion') {
      writeProbeResult({
        version: EXT_VERSION,
        pid: process.pid,
        at: new Date().toISOString(),
        status: 'cancelled',
        method: 'none',
        ms: 0,
        chars: 0,
        text: '',
        found: [],
        error: '',
        nonce: opts.nonce || ''
      });
      return;
    }
  }

  const out = vscode.window.createOutputChannel('Subway Inline Probe');
  out.show(true);

  const log = (msg) => {
    out.appendLine(`[${new Date().toISOString()}] ${msg}`);
  };

  try {
    log('Listing candidate inline completion commands...');
    const all = await vscode.commands.getCommands(true);
    found = all.filter(c => /inline(completion|suggest)/i.test(c));
    for (const c of found) {
      log(c);
    }

    log('Creating scratch Python document...');
    const doc = await vscode.workspace.openTextDocument({
      language: 'python',
      content: 'def fibonacci(n):\n    """Return the nth Fibonacci number."""\n    '
    });
    const position = doc.positionAt(doc.getText().length);
    log(`Scratch doc open at position line ${position.line}, character ${position.character}`);

    let summary = '';
    let gotText = false;

    // Method A (API, no editor)
    const methodANames = [
      'vscode.executeInlineCompletionItemProvider',
      'vscode.executeInlineCompletionProvider'
    ];
    for (const name of methodANames) {
      if (!all.includes(name)) {
        continue;
      }
      try {
        const t0 = Date.now();
        const res = await vscode.commands.executeCommand(name, doc.uri, position);
        const duration = Date.now() - t0;
        const items = Array.isArray(res) ? res : (res && res.items) || [];
        let itemText = '';
        if (items.length > 0) {
          const it = items[0];
          itemText = typeof it.insertText === 'string' ? it.insertText : (it.insertText && it.insertText.value) || '';
        }
        log(`${name}: ${duration} ms, ${items.length} items, text: ${itemText.slice(0, 300)}`);
        if (itemText) {
          gotText = true;
          method = `A:${name}`;
          ms = duration;
          text = itemText.slice(0, 2000);
          const countLabel = items.length === 1 ? '1 suggestion' : `${items.length} suggestions`;
          summary = `Inline probe: Method A (${name}) returned ${countLabel} in ${duration} ms`;
          break;
        }
      } catch (e) {
        log(`${name} error: ${e && e.message ? e.message : String(e)}`);
      }
    }

    // Method B (only if A got no text)
    if (!gotText) {
      log('Method A yielded no text. Starting Method B (trigger + commit)...');
      const editor = await vscode.window.showTextDocument(doc, { preview: true, preserveFocus: false });
      editor.selection = new vscode.Selection(position, position);
      const before = doc.getText();
      const t0 = Date.now();
      await vscode.commands.executeCommand('editor.action.inlineSuggest.trigger');

      for (let i = 0; i < 12; i++) {
        await new Promise(r => setTimeout(r, 500));
        await vscode.commands.executeCommand('editor.action.inlineSuggest.commit');
        if (doc.getText() !== before) {
          const captured = doc.getText().slice(before.length);
          const duration = Date.now() - t0;
          log(`Method B captured (${duration} ms): ${captured.slice(0, 300)}`);
          summary = `Inline probe: Method B (trigger + commit) captured ${captured.length} characters in ${duration} ms`;
          gotText = true;
          method = 'B';
          ms = duration;
          text = captured.slice(0, 2000);
          break;
        }
      }

      if (vscode.window.activeTextEditor?.document === doc) {
        await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
      }
    }

    if (!summary) {
      summary = 'Inline probe: no suggestion came back. See the Subway Inline Probe output.';
    }

    log(summary);
    log('Check the Copilot completions quota in the Subway HUD to see whether it moved.');
    vscode.window.showInformationMessage(summary);

    const status = gotText ? 'ok' : 'none';
    writeProbeResult({
      version: EXT_VERSION,
      pid: process.pid,
      at: new Date().toISOString(),
      status,
      method,
      ms,
      chars: text.length,
      text,
      found,
      error: '',
      nonce: opts.nonce || ''
    }, log);
  } catch (err) {
    const errorMsg = err && err.message ? err.message : String(err);
    log(`Probe error: ${errorMsg}`);
    vscode.window.showErrorMessage(`Subway inline probe error: ${errorMsg}`);
    writeProbeResult({
      version: EXT_VERSION,
      pid: process.pid,
      at: new Date().toISOString(),
      status: 'error',
      method,
      ms,
      chars: text.length,
      text,
      found,
      error: errorMsg,
      nonce: opts.nonce || ''
    }, log);
  }
}

function findServerLauncher() {
  const serverPy = path.join(__dirname, '..', 'server.py');
  if (fs.existsSync(serverPy)) {
    const pyBin = process.env.PYTHON_BIN || (process.platform === 'win32' ? 'python' : 'python3');
    return { cmd: pyBin, args: [serverPy] };
  }
  return { cmd: 'subway-context', args: [] };
}

async function startOrRestartServer(port) {
  const launcher = findServerLauncher();
  const env = Object.assign({}, process.env, { SUBWAY_HUD_NO_OPEN: '1' });
  try {
    const child = cp.spawn(launcher.cmd, [...launcher.args, 'hud', String(port), '--no-open'], {
      detached: true,
      stdio: 'ignore',
      env
    });
    child.unref();
  } catch (err) {
    try {
      const npxCmd = process.platform === 'win32' ? 'npx.cmd' : 'npx';
      const child = cp.spawn(npxCmd, ['-y', 'subway-context', 'hud', String(port), '--no-open'], {
        detached: true,
        stdio: 'ignore',
        env
      });
      child.unref();
    } catch (e) {}
  }
}


/* ---- Antigravity GUI chat wake ---------------------------------------------
   Antigravity is a VS Code fork, and its bundle registers an internal command:
     id: "antigravity.sendPromptToAgentPanel"
     run(id, text) { this.sendMessageToChatPanel?.(text) }
   That is the GUI equivalent of pi.sendMessage(..., {triggerTurn:true}): it puts text
   into the agent panel and the panel runs a turn. We call it for spooled events.

   Why a spool: the MCP server and the GUI are different processes, so they cannot share
   memory. Same mechanism as the pi extension (wake_spools/<station>.jsonl).          */

const AGY_WAKE_COMMAND = 'antigravity.sendPromptToAgentPanel';
let agyWakeWatcher = null;
const agyWakeOffsets = new Map();

function subwayHomeFile() {
  return path.join(os.homedir(), '.subway-context');
}

function agyLog(line) {
  try {
    fs.appendFileSync(path.join(subwayHomeFile(), 'extension.log'),
      `[agy-wake ${new Date().toISOString()}] ${line}\n`);
  } catch (_) {}
}

function agyWakeDir() {
  return path.join(subwayHomeFile(), 'agy_wake');
}

/* Tell the Python side which windows exist, so it can address this one by pid. */
function writeAgyWindowRegistration() {
  try {
    const dir = path.join(subwayHomeFile(), 'agy_windows');
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, `${process.pid}.json`), JSON.stringify({
      pid: process.pid,
      app: vscode.env.appName,
      workspace: (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath),
      updated_at: new Date().toISOString(),
    }));
  } catch (_) {}
}

async function antigravityAvailableCommands() {
  try {
    const commands = await vscode.commands.getCommands(true);
    return commands.filter((c) => c.startsWith("antigravity."));
  } catch (_) {
    return [];
  }
}

/* The panel must exist before the send action can be consumed: sendMessageToChatPanel
   calls openPanel() then fires a chat action, and a window with no mounted chat view
   drops the action silently. So open the view, let it mount, then send. */
async function antigravityWake(text, tries = 2) {
  const body = String(text || "").trim();
  if (!body) return false;
  const available = await antigravityAvailableCommands();
  if (!available.includes(AGY_WAKE_COMMAND)) {
    agyLog(`command missing: ${AGY_WAKE_COMMAND} (have ${available.length} antigravity.*)`);
    return false;
  }
  for (let attempt = 1; attempt <= tries; attempt += 1) {
    try {
      // openAgent starts a session when there is none; openChatView only reveals the view,
      // and a revealed-but-sessionless panel drops the send action.
      for (const opener of ["antigravity.openAgent", "antigravity.openChatView"]) {
        if (!available.includes(opener)) continue;
        try { await vscode.commands.executeCommand(opener); } catch (_) {}
        await new Promise((r) => setTimeout(r, 600));
      }
      await vscode.commands.executeCommand(AGY_WAKE_COMMAND, body);
      agyLog(`delivered (attempt ${attempt}): ${body.slice(0, 120)}`);
      return true;
    } catch (err) {
      agyLog(`execute failed (attempt ${attempt}): ${err && err.message ? err.message : err}`);
      await new Promise((r) => setTimeout(r, 600));
    }
  }
  return false;
}

function drainAgySpool() {
  const dir = agyWakeDir();
  let names = [];
  try { names = fs.readdirSync(dir).filter((n) => n.endsWith('.jsonl')); } catch (_) { return; }
  // This window only takes its own file: one window must not eat another's events.
  for (const name of names) {
    if (!name.startsWith(`${process.pid}-`)) continue;
    const file = path.join(dir, name);
    let size = 0;
    try { size = fs.statSync(file).size; } catch (_) { continue; }
    const seen = agyWakeOffsets.get(file) || 0;
    if (size <= seen) continue;
    let chunk = '';
    try {
      const fd = fs.openSync(file, 'r');
      const buf = Buffer.alloc(size - seen);
      fs.readSync(fd, buf, 0, buf.length, seen);
      fs.closeSync(fd);
      chunk = buf.toString('utf8');
    } catch (_) { continue; }
    agyWakeOffsets.set(file, size);
    for (const line of chunk.split('\n')) {
      const text = line.trim();
      if (!text) continue;
      let payload;
      try { payload = JSON.parse(text); } catch (_) { payload = { text }; }
      antigravityWake(payload.text || payload.message || '');
    }
  }
}

function startAgyWakeListener(context) {
  // Only Antigravity exposes this command; other forks simply never have a spool.
  try {
    fs.mkdirSync(agyWakeDir(), { recursive: true });
  } catch (_) {}
  writeAgyWindowRegistration();
  const timer = setInterval(() => {
    writeAgyWindowRegistration();
    drainAgySpool();
  }, 1000);
  try {
    if (!agyWakeWatcher) {
      agyWakeWatcher = fs.watch(agyWakeDir(), () => drainAgySpool());
    }
  } catch (_) {}
  context.subscriptions.push({
    dispose() {
      clearInterval(timer);
      try { if (agyWakeWatcher) agyWakeWatcher.close(); } catch (_) {}
      agyWakeWatcher = null;
    },
  });
  agyLog(`listener armed for pid ${process.pid} (${vscode.env.appName})`);
}

function activate(context) {
  const runServerCommand = vscode.commands.registerCommand('subway.runServer', async () => {
    const port = getHudPort();
    let reloaded = false;
    const isHealthy = await checkHealth(port);
    if (isHealthy) {
      const reloadData = await postJson('/api/reload', {});
      if (reloadData && reloadData.ok) {
        reloaded = true;
      }
    }
    if (!reloaded) {
      await startOrRestartServer(port);
    }
    let ok = await checkHealth(port);
    if (!ok) {
      for (let i = 0; i < 15; i++) {
        await new Promise((resolve) => setTimeout(resolve, 200));
        ok = await checkHealth(port);
        if (ok) break;
      }
    }
    if (ok) {
      vscode.window.showInformationMessage(`Subway server is running at http://127.0.0.1:${port}`);
    } else {
      vscode.window.showErrorMessage(`Failed to start Subway server on port ${port}.`);
    }
  });
  context.subscriptions.push(runServerCommand);

  const openHudCommand = vscode.commands.registerCommand('subway.openHud', () => {
    const port = getHudPort();
    const url = `http://127.0.0.1:${port}`;
    vscode.commands.executeCommand('simpleBrowser.show', url);  // It takes a plain URL string.
  });
  context.subscriptions.push(openHudCommand);

  const probeInlineCompletionCommand = vscode.commands.registerCommand('subway.probeInlineCompletion', async () => {
    if (probeRunning) {
      vscode.window.showInformationMessage('Subway inline probe is already running.');
      return;
    }
    probeRunning = true;
    try {
      await probeInlineCompletion();
    } finally {
      probeRunning = false;
    }
  });
  context.subscriptions.push(probeInlineCompletionCommand);

  try {
    context.subscriptions.push(vscode.commands.registerCommand('subway.agyWake', async (text) => {
      const body = text || await vscode.window.showInputBox({ prompt: 'Text to deliver to the Antigravity agent panel' });
      if (!body) return false;
      return antigravityWake(body);
    }));
  } catch (_) {}
  startAgyWakeListener(context);

  const uriHandler = vscode.window.registerUriHandler({
    handleUri(uri) {
      if (uri.path === '/probe-inline') { handleProbeUri(uri); return; }
      if (uri.path === '/agy-wake') {
        let text = '';
        try { text = new URLSearchParams(uri.query || '').get('text') || ''; } catch (_) {}
        antigravityWake(text).then(async (ok) => {
          try {
            const cmds = await antigravityAvailableCommands();
            fs.writeFileSync(path.join(subwayHomeFile(), 'agy_wake_result.json'), JSON.stringify({
              ok, text, at: new Date().toISOString(), app: vscode.env.appName, pid: process.pid,
              has_send: cmds.includes(AGY_WAKE_COMMAND),
              has_open_chat: cmds.includes('antigravity.openChatView'),
              commands: cmds,
            }));
          } catch (_) {}
        });
        return;
      }
      let port = getHudPort();
      if (uri.query) {
        try {
          const params = new URLSearchParams(uri.query);
          const p = params.get('port');
          if (p && !isNaN(Number(p))) {
            port = Number(p);
          }
        } catch (e) {
          const match = uri.query.match(/port=(\d+)/);
          if (match) {
            port = Number(match[1]);
          }
        }
      }
      const url = `http://127.0.0.1:${port}`;
      vscode.commands.executeCommand('simpleBrowser.show', url);
    }
  });
  context.subscriptions.push(uriHandler);

  const shareTerminalCommand = vscode.commands.registerCommand('subway.shareTerminal', async () => {
    const terminal = vscode.window.activeTerminal;
    if (!terminal) {
      vscode.window.showWarningMessage('No active terminal.');
      return;
    }
    const entry = getTerminalState(terminal);
    entry.sharing = true;
    entry.allowCommands = false;
    await postJson('/api/terminal_opt_in', terminalOptInBody(terminal, entry));
    const message = terminal.shellIntegration
      ? 'Terminal sharing enabled.'
      : 'Terminal shared, but nothing will be captured until shell integration is available for this terminal.';
    vscode.window.showInformationMessage(message);
  });
  context.subscriptions.push(shareTerminalCommand);

  const stopSharingTerminalCommand = vscode.commands.registerCommand('subway.stopSharingTerminal', async () => {
    const terminal = vscode.window.activeTerminal;
    if (!terminal) {
      vscode.window.showWarningMessage('No active terminal.');
      return;
    }
    const entry = terminalStates.get(terminal);
    if (!entry || !entry.sharing) {
      vscode.window.showInformationMessage('Terminal is not shared.');
      return;
    }
    entry.sharing = false;
    await postJson('/api/terminal_opt_in', terminalOptInBody(terminal, entry));
    vscode.window.showInformationMessage('Terminal sharing stopped.');
  });
  context.subscriptions.push(stopSharingTerminalCommand);

  const allowTerminalCommandsCommand = vscode.commands.registerCommand('subway.allowTerminalCommands', async () => {
    const terminal = vscode.window.activeTerminal;
    if (!terminal) {
      vscode.window.showWarningMessage('No active terminal.');
      return;
    }
    const entry = terminalStates.get(terminal);
    if (!entry || !entry.sharing) {
      vscode.window.showWarningMessage('Share this terminal first.');
      return;
    }
    entry.allowCommands = !entry.allowCommands;
    await postJson('/api/terminal_opt_in', terminalOptInBody(terminal, entry));
    vscode.window.showInformationMessage(
      `Commands from the HUD ${entry.allowCommands ? 'allowed' : 'blocked'} for this terminal.`
    );
  });
  context.subscriptions.push(allowTerminalCommandsCommand);

  const startExecutionListener = vscode.window.onDidStartTerminalShellExecution((e) => {
    if (!isTerminalSharingActive()) return;
    const entry = terminalStates.get(e.terminal);
    if (!entry || !entry.sharing) return;
    entry.pendingCommand = {
      command: e.execution.commandLine.value,
      startTs: Date.now() / 1000
    };
  });
  const endExecutionListener = vscode.window.onDidEndTerminalShellExecution(async (e) => {
    const entry = terminalStates.get(e.terminal);
    if (!entry) return;
    const pendingRun = entry.pendingRun && entry.pendingRun.execution === e.execution
      ? entry.pendingRun
      : null;
    if (!entry.sharing && !pendingRun) return;
    const pendingCommand = (entry.sharing && isTerminalSharingActive()) ? entry.pendingCommand : null;
    if (!pendingCommand && !pendingRun) return;
    entry.pendingCommand = null;
    const output = await readExecutionOutput(e.execution);
    if (terminalStates.get(e.terminal) !== entry) return;
    if (pendingCommand && isTerminalSharingActive()) {
      postTerminalEvent({
        terminal_id: entry.id,
        command: pendingCommand.command,
        exit_code: (typeof e.exitCode === 'number' ? e.exitCode : null),
        start_ts: pendingCommand.startTs,
        end_ts: Date.now() / 1000,
        output,
        caller_id: 'editor',
        name: e.terminal.name,
        window_id: entry.windowId,
        folder: (vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || '')
      });
    }
    if (pendingRun) {
      await postJson('/api/run_result', {
        run_id: pendingRun.runId,
        output,
        exit_code: (typeof e.exitCode === 'number' ? e.exitCode : null),
        error: ''
      });
      if (entry.pendingRun === pendingRun) {
        entry.pendingRun = null;
        entry.runInFlight = false;
      }
    }
  });
  context.subscriptions.push(startExecutionListener, endExecutionListener);

  const closeTerminalListener = vscode.window.onDidCloseTerminal((terminal) => {
    terminalStates.delete(terminal);
    activeTerminals.delete(terminal);
  });
  context.subscriptions.push(closeTerminalListener);

  let estState = { days: {} }, estDirty = false, estLastBulkMs = 0;
  const estDoc = vscode.workspace.onDidChangeTextDocument((e) => {
    const isUndo = vscode.TextDocumentChangeReason && e.reason === vscode.TextDocumentChangeReason.Undo;
    const isRedo = vscode.TextDocumentChangeReason && e.reason === vscode.TextDocumentChangeReason.Redo;
    const bulk = !!(isUndo || isRedo);
    if (bulk) estLastBulkMs = Date.now();
    const ctx = {
      changes: e.contentChanges,
      now: Date.now(),
      lastBulkMs: estLastBulkMs,
      undo: isUndo,
      redo: isRedo,
      uri: e.document && e.document.uri && e.document.uri.toString(),
      docSize: e.document ? Buffer.byteLength(e.document.getText(), 'utf8') : 0
    };
    for (const ch of (e.contentChanges || [])) {
      if (est.classifyChange(ch, ctx)) {
        const day = new Date().toISOString().slice(0, 10);
        const rel = vscode.workspace && typeof vscode.workspace.asRelativePath === 'function' && e.document && e.document.uri
          ? vscode.workspace.asRelativePath(e.document.uri)
          : (e.document && e.document.fileName ? e.document.fileName : 'unknown');
        estState = est.aggregate(estState, rel, day);
        estDirty = true;
        break;
      }
    }
  });
  const estTimer = setInterval(() => {
    if (!estDirty) return;
    estDirty = false;
    const dir = process.env.SUBWAY_DIR || path.join(os.homedir(), '.subway-context');
    try {
      fs.mkdirSync(dir, { recursive: true });
      const f = path.join(dir, 'copilot_inline_estimate.json');
      const tmp = f + `.tmp-${process.pid}`;
      fs.writeFileSync(tmp, JSON.stringify(estState, null, 2), 'utf8');
      fs.renameSync(tmp, f);
    } catch (_) {}
  }, 30000);
  context.subscriptions.push(estDoc, { dispose() { clearInterval(estTimer); } });

  pollInterval = setInterval(() => {
    if (!isTerminalSharingActive()) return;
    for (const terminal of activeTerminals) {
      const entry = terminalStates.get(terminal);
      if (entry && entry.sharing && entry.allowCommands && !entry.runInFlight) {
        void pollTerminalRuns(terminal, entry);
      }
    }
  }, 3000);

  void updateHudSharingStatus();
  sharingPollInterval = setInterval(updateHudSharingStatus, 30000);

  writeWindowHeartbeat();
  heartbeatInterval = setInterval(writeWindowHeartbeat, 30000);
  startNotificationListener();

  probePollInterval = setInterval(async () => {
    if (probeRunning) return;
    const req = claimProbeRequest();
    if (!req) return;
    probeRunning = true;
    try { await probeInlineCompletion({ preapproved: true, nonce: req.nonce }); }
    finally { probeRunning = false; }
  }, 2000);

  // Activation onStartupFinished: if the HUD answers /healthz, do nothing more, never auto-open.
  const port = getHudPort();
  checkHealth(port).then((isHealthy) => {
    if (isHealthy) {
      // HUD is running; do nothing more and never auto-open.
    }
  });
}

function deactivate() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
  if (sharingPollInterval) {
    clearInterval(sharingPollInterval);
    sharingPollInterval = null;
  }
  if (probePollInterval) {
    clearInterval(probePollInterval);
    probePollInterval = null;
  }
  if (heartbeatInterval) {
    clearInterval(heartbeatInterval);
    heartbeatInterval = null;
  }
  if (notificationWatcher) {
    try { notificationWatcher.close(); } catch (_) {}
    notificationWatcher = null;
  }
  try {
    const file = path.join(os.homedir(), '.subway-context', 'editor_windows', `${process.pid}.json`);
    fs.unlinkSync(file);
  } catch (_) {}
}

module.exports = {
  activate,
  deactivate,
  probeInlineCompletion,
  handleProbeUri,
  claimProbeRequest,
  writeWindowHeartbeat
};
