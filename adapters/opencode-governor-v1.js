/**
 * OpenCode plugin adapter for Subway Governor.
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

export const SubwayGovernor = async () => ({
  'tool.execute.before': async (input, _output) => {
    // Subway workers have their own budgets and guards; the governor limits principals only.
    if (process.env.SUBWAY_WORKER === '1') return;
    try {
      const sessionId = input?.sessionID || input?.sessionId || '';
      const toolName = input?.tool || input?.name || '';
      const toolInput = input?.input || input?.args || input?.parameters || {};

      let turnKey = null;
      try {
        turnKey = getLatestUserMessageId(sessionId);
      } catch (_e) {
        turnKey = null;
      }

      const [cmd, prefix] = governorCommand();
      const args = [
        ...prefix,
        'governor',
        'check',
        '--provider',
        'opencode',
        '--session',
        String(sessionId),
      ];

      if (turnKey !== null && turnKey !== undefined) {
        args.push('--turn-key', String(turnKey));
      }

      const stdinData = JSON.stringify({
        tool: toolName,
        input: toolInput,
      });

      const res = spawnSync(cmd, args, {
        input: stdinData,
        encoding: 'utf8',
        timeout: 5000,
      });

      if (res && res.status === 2) {
        let reason = 'Blocked by governor';
        try {
          const out = JSON.parse(res.stdout || '{}');
          if (out.reason) {
            reason = out.reason;
          }
        } catch (_e) {}
        const blockErr = new Error(reason);
        blockErr.isGovernorBlock = true;
        throw blockErr;
      }
    } catch (err) {
      if (err && err.isGovernorBlock) {
        throw err;
      }
      // Any other exception → allow (ignore)
    }
  },
});

export default SubwayGovernor;
