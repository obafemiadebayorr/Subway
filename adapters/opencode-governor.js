/**
 * OpenCode plugin adapter for Subway Governor.
 *
 * OpenCode 2 loads `export default { id, setup(ctx) }` and runs
 * ctx.tool.hook("execute.before", fn) before every tool call; a rejected
 * promise stops the call. OpenCode 1 hook maps (a function export returning
 * { 'tool.execute.before': fn }) are no longer accepted.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

function getLatestUserMessageId(sessionId) {
  if (!sessionId) return null;
  try {
    const dbPath = path.join(os.homedir(), '.local', 'share', 'opencode', 'opencode.db');
    if (!fs.existsSync(dbPath)) return null;

    const res = spawnSync(
      'sqlite3',
      [
        dbPath,
        `SELECT id FROM session_message WHERE session_id='${sessionId.replace(/'/g, "''")}' AND type='user' ORDER BY seq DESC LIMIT 1;`,
      ],
      { encoding: 'utf8', timeout: 1000 }
    );
    if (res.status === 0 && res.stdout) {
      const val = res.stdout.trim();
      return val || null;
    }
  } catch (_e) {}
  return null;
}

// The installed copy lives in ~/.config/opencode/plugin, away from the repo:
// prefer SUBWAY_CLI, then the repo layout, then `subway` on PATH.
function governorCommand() {
  if (process.env.SUBWAY_CLI) return ['node', [process.env.SUBWAY_CLI]];
  const here = path.dirname(fileURLToPath(import.meta.url));
  const repoCli = path.join(path.dirname(here), 'bin', 'subway.js');
  if (fs.existsSync(repoCli)) return ['node', [repoCli]];
  return ['subway', []];
}

function checkTool(sessionId, toolName, toolInput) {
  let blockErr = null;
  try {
    let turnKey = null;
    try {
      turnKey = getLatestUserMessageId(sessionId);
    } catch (_e) {
      turnKey = null;
    }

    const [cmd, prefix] = governorCommand();
    const args = [...prefix, 'governor', 'check', '--provider', 'opencode', '--session', String(sessionId)];
    if (turnKey !== null && turnKey !== undefined) {
      args.push('--turn-key', String(turnKey));
    }

    const res = spawnSync(cmd, args, {
      input: JSON.stringify({ tool: toolName, input: toolInput }),
      encoding: 'utf8',
      timeout: 5000,
    });

    if (res && res.status === 2) {
      let reason = 'Blocked by governor';
      try {
        const out = JSON.parse(res.stdout || '{}');
        if (out.reason) reason = out.reason;
      } catch (_e) {}
      blockErr = new Error(reason);
    }
  } catch (_err) {
    // Any other failure allows the call: the governor must never break the agent.
  }
  if (blockErr) throw blockErr;
}

export default {
  id: 'subway.governor',
  setup(ctx) {
    // Subway workers have their own budgets and guards; the governor limits principals only.
    if (process.env.SUBWAY_WORKER === '1') return;
    ctx?.tool?.hook?.('execute.before', async (evt) => {
      checkTool(
        evt?.sessionID || evt?.sessionId || '',
        evt?.tool || evt?.name || '',
        evt?.input || evt?.args || {}
      );
    });
  },
};
