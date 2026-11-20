# Changelog

## 1.3.9 - 2026-11-20

### Fixed (regression audit: 20 findings, all high-severity closed)
- **Budget caps could silently disarm** (`B3b`): `budget_stop` skipped whenever a worker response existed, so multi-turn workers ran past every token/tool cap (validated: a 1.5M-token runaway is now stopped at its cap). Caps now skip only on finished workers.
- **Dead pi sessions looked online** (`A11`): delivery into closed sessions was faked with a "Delivered via spool" receipt that no one would ever read. Liveness now checks the specific chat's session mapping + its process, and closed chats refuse delivery loudly ("chat is closed").
- **Worker milestones routed to the wrong chat** (`A12`): IDE-hosted pi chats share MCP servers whose ancestry never reaches them, so `launched_by` resolved to whichever chat spoke last — false history in the Workers panel and misrouted alerts. Caller identity now resolves by per-session liveness markers (`pi_live/`), touched by the wake extension before every tool call.
- **HUD-started chats died silently** (`A13`): "New Pi chat" spawned a bare pi CLI that lacks the app-managed deepai provider and exits 0 after the /login banner — the HUD reported success, then the panel matched the dead tab to a wrong chat. Headless spawns now fail loudly with the cause, and a new **DeepAI models in HUD-started chats** setting (default off) opts into the app's own local-proxy path (identical billing to in-app).
- **Spool graveyards**: dead chats' undelivered wake spools accumulated for weeks (322 files, 156 with events). `archive_orphaned_spools()` now archives them (never deletes) and the registries were swept (3,312 stale pi sessions, 31 stale window files).
- **Wake spool cross-process corruption** (`A2`), **mute semantics** (`A3`), **guard crashes on Antigravity** (`B2` additionalContext), **pi edit-tool gating** (`B1` lowercase edit/write), **fastmail alias/self-send guards** (`A5/A6`), **dual-format agy wake incl. UUID chat ids** (`B4`), **log-read UTF-8 boundary + mid-flush cache guard** (`A7/A8`), **alias spool resolution + VM honesty** (`A4`), **auditor-role classification** (`B3c`), **remote-weakening settings guard** (`B5`), **failure-doctor hint** (`B6`), **composer model inheritance** (`F1`), and unit-verify pinning (`F6`).

### Added
- **DeepAI tunnel (carrier-restricted networks)**: Settings → Startup → `deepai_tunnel` — a pure IPv4 TCP forwarder to the same api.deepai.org servers (zero TLS termination, billing byte-identical) for networks where direct TLS fails; one-command arm/teardown, status card, hosts backup.
- **Spool hygiene API**: `wake_driver.archive_orphaned_spools(min_age_days)`.

### Known residuals (ledger: `plan/1.3.9-regression-audit.md`)
- ~24 test failures are assertion drift from concurrent feature merges (none from this release's fixes; verified by stash-testing); repair queued for 1.3.10.
- OpenCode worker shutdown may SIGTERM the child before the final report (F0-family).
- Threads page: the tested canvas atlas implementation is retained; the older shadow-root wrapper was not merged.

## 1.3.8 - 2026-11-15

### Added / Changed
- **`hud_next` default HUD & `/classic`**: `hud_next` is now the default HUD served at `/` (with the classic dashboard kept at `/classic`), featuring no inline scripts (strict CSP), notifications from `/`, dropping dev live-reload, and porting the dedicated Permissions panel to workers.
- **Multi-provider pill strip & iframe CSP**: multi-provider pill strip styling and provider pill indicators in `hud_next`, along with iframe CSP handling.
- **`Load:` filter & `/api/home_prefs`**: home panel chat filtering by load status (`Load:` filter in `homepanel.js`) backed by persistent server-side preferences via `/api/home_prefs` and `/api/chats` filter support.
- **Tab options menu & `hoverEl` fixes**: panel tabs options menu (`...`), view scoping, and resolved stuck `hoverEl` card dismissal issues in `homepanel.js`.
- **Chat and mail as separate channels**: direct chat continuation (`/api/chat`) and Fast mail (`send_note`, `reply_note`, `fastmail_status`, `ask_room`) operate as distinct channels; Fast mail supports direct delivery into online chats, threaded replies, room broadcasts, offline resume with HUD approval, spam/loop guards, `mail.*` settings, and a dedicated Fast mail panel in the HUD.
- **Background continue for OpenCode and Claude chats**: OpenCode and Claude sessions can be continued in the background / headless without blocking or requiring an interactive terminal window.
- **Multi-provider PreToolUse guard**: `guard.py` and `install.py` install and enforce PreToolUse hooks and workspace guards across multiple agent providers.
- **Documentation & cross-platform readiness**: updated documentation across API endpoints, README, changelog, and Claude doc runs; performed Windows readiness audit.
- Unit chains run each unit in an isolated worktree and land it by merge; skeleton packets are focus skeletons (nested focus, full signatures, folded constants).
- **Provider swap in one conversation**: Swap a conversation's provider like swapping a model so the conversation stays in one HUD tab; the new provider gets a read-back of recent exchanges (with a full read-back option in settings), with thinking and tool ids stripped.
- **Live token streaming**: Chat replies stream token by token into the HUD panel instead of appearing only after the turn finishes.
- **New chat button**: The new-chat (+) button opens a fresh chat for the selected provider.
- **Antigravity MCP tools in headless turns**: Antigravity headless turns may call Subway's MCP tools through a narrow per-session allow rule instead of being auto-denied.
- **Low-power mode**: Settings → Appearance → Display (Auto/On/Off) uses a static map and background with no idle animation; Auto turns on for 4-core / 4 GB machines and the map pauses when the tab is hidden or idle.
- **Lightweight chat connections (`mcp_relay`)**: `subway settings set mcp_relay true` turns each chat's MCP process into a lightweight relay to the running HUD instead of a full server (off by default).
- **Principal small edits**: Settings → Principal → "Small edits allowed" lets the principal make edits up to a configurable changed-line limit while the edit block is on.
- **Diagnostics streaming**: The Diagnostics panel streams samples as they are taken, updates in place, and shows when the last sample was taken.
- **Provider transplant learning**: HUD Settings → Providers shows shipped, learned, unmapped, and drifted adapters with last probe status and can draft a mapping brief for unmapped providers.
- **Dynamic model menus and principal picker**: The composer offers dynamic model and effort choices with a live principal picker and backend-backed principal/initiator commands.
- **HUD landing and diff views**: `hud_next` adds a Landing page and inline or side-by-side patch diff rendering for worker changes.
- **Principal handoff digest**: Principal handoff carries an outgoing chat digest to the new principal with recent context and handoff details.
- **Chat send headless continue**: Sending to an OpenCode or Claude chat continues it headless when no window is open.
- **Station links and peer collaboration**: Linked stations can share chats, coordination, files, media, and remote worker status across peers where grants allow.
- **Screen sharing grants**: "See my screen" supports off, snapshot, and live sharing with per-session approval, a viewing banner, quality presets, and audit entries.
- **Terminals and phone pairing**: The HUD exposes shared terminal controls and device pairing flows, including tunnel-based pairing and push-capable mobile connections.
- **Unmerged worker branches**: The Landing page hides worker branches that were deleted or merged outside the HUD, so the list shows only branches still awaiting review.

### Fixed
- **HUD CPU**: The usage sampler no longer rescans every worker and rebuilds status each second, reducing idle HUD CPU use.
- **Memory**: Listing chats no longer loads whole pi transcripts, pi session summaries are cached on disk and shared across chats, and later chat loads use much less memory.
- **Worker notices**: Worker notices reach chats through OS file-change events instead of a once-a-second poll and are no longer re-sent every second.
- **Provider swap replies**: Switching a conversation back to a provider now shows its reply, and a moved conversation keeps late native replies.
- **Chats list stability**: The HUD chats list no longer crashes when a debug endpoint shadows a module name.
- **Principal edit block scope**: The principal edit block now applies to projects under the temp folder.
- **Panel send behavior**: The panel stays on the open tab after a plain send and switches only on a real handoff.
- **Wallpaper upload path**: An uploaded wallpaper image is served from the state directory instead of the HUD directory.
- **Watchdog for literal edits**: Literal-edit briefs allow more calls before the no-edit stop so workers have time to locate anchors.
- **Progress guard counting**: Code packets alone no longer cut the no-edit stop threshold.
- **HUD settings validation**: Saving HUD settings with out-of-range values is rejected with an error instead of being saved.

## 1.3.7 - 2026-11-10

### Added / Changed
- Composer: queue messages while a reply is running, and an expandable results section for sent messages.
- Composer: `#` lists files from the project the HUD was started from (SUBWAY_PROJECT_DIR), and `@` loads chats on demand through a lighter /api/chats call.
- Worker token caps now count cache reads (weighted by `cap_cache_weight`, default 0.25), and briefs that carry exact edits stop after 6 calls without an edit.
- The Subway version moved from the top of the Overview to its footer.

### Fixed
- Stats: the spend chart no longer pages into the past on vertical scrolling, and stale responses no longer overwrite newer data.
- HUD: an UnboundLocalError in a request handler, and several test fixtures updated for the HUD's host, origin and content-type checks.

## 1.3.6 r1 (2026-11-09)

### Added / Changed

- **Antigravity chat wake**: a worker event now reaches a live Antigravity chat through the app's local daemon (ConnectRPC), delivered as a high-priority message from `subway` in the running conversation. Verified end to end: the message appears in the conversation's own database within seconds and the agent acts on it. This replaces the `agy --print --conversation` path, which could never work because the CLI keeps a separate store from the GUI.
- **AST-first code packets**: a line range in a Python file expands to the enclosing functions; `read_symbol` and `claim_symbol` are preferred.
- **HUD cards**: added cards for station, activity, track, thread logs, exclusions, and pending runs.
- **Principal settings**: HUD Settings row "Principal" (block principal edits, read budget per session) with the guard enforcing it.
- **Copilot inline suggestion estimate**: fetched from the VS Code extension and displayed as an estimate in the HUD.
- **Design documentation**: added design docs for live provider checks (`docs/design/24-provider-checks.md`) and a resumable pipeline (`docs/design/25-pipeline.md`).
- Every HUD switch shows On/Off inside the pill through one shared component, with its own knob icon.
- The Overview activity terrain shows each day's spun/finished/tokens on hover (desktop) or tap (mobile), replacing the Usage page's Worker Activity list.
- Station capacity chips show nested values and readable sizes.

### Fixed

- **Reconnecting MCP server**: reconnecting the MCP server no longer stops running workers; reconnecting now only ends the MCP wait and workers keep running.
- **Principal read budget**: the principal read budget now also counts MCP code reads (`read_symbol`, `context_slice`, `read_terminal`, `search_terminal`, `project_files`; list editable in Settings > Principal), while code packets in briefs never count.
- **Worker progress guard**: the worker progress guard is fair (coordination calls and test runs after an edit don't count as idle, one warning before a stop, limits in settings).
- **Polling guard**: repeat status checks from leads return "no change since" and a poll guard points them to `await_worker`.
- **Claude worker reports on cap**: Claude workers keep their last text as a partial report when stopped at a cap.
- **Worker activity ledger**: worker activity counts no longer drop when workers are archived or removed (durable per-day ledger, backfilled once).
- **Read-only worker guard**: read-only workers can no longer write files through the terminal.
- **Progress guard**: shell commands that write files now count as edits for the progress guard.
- **Pi (DeepAI) workers**: workers keep their final report, provider errors (e.g. 403 quota) are recorded as failures rather than success, and `worker_status` trims pi logs.
- **Review steps**: default to low effort (never max).
- **Worker status track filter**: fixed track filtering for log-only workers.
- **Test isolation & fixes**: three flaky/broken tests fixed (npm notice lines in pi model discovery, a background refresh leak between tests, the relay test `test_relay_and_permission_answering`); isolated `hud_fast` tests, and resolved a test-order leak between containment, worker-rules, and read-only tests.
- **Pi model test**: updated stale pi model test expectations.
- **HUD log scroll**: log scroll no longer jumps to the top on refresh.
- **Iteration control**: fixed `spin_iteration` invocation from the HUD.
- **Forks tests**: added coverage for edge cases in forks.

### Known issues (carry to 1.3.7)

- Report-on-cap for non-Claude providers.
- Automatic code packets.
- The "[PERMISSION NEEDED] 0 request(s)" banner showing when nothing is pending.
- Budget-stop continuation not firing.
- TestHudCache can be flaky.
- Items 7, 8, 10, P29a/P29b, P33, the "wrote to the main tree" false warnings.

### Token use

5,761,195 total tokens across 83 workers (21 completed builders/fixers/landers).

| provider | workers | tokens | tokens per worker |
|---|---|---|---|
| antigravity | 36 | 3028501 | 84125 |
| opencode | 28 | 2422491 | 86517 |
| pi | 6 | 168773 | 28128 |
| local | 13 | 141430 | 10879 |

## 1.3.5 - 2026-11-05

- **Server split into modules**: monolithic `server.py` is decomposed into modular subsystems (`chat_readers`, `cli_tools`, `hud_server`, `local_models`, `permissions`, `store`, `threads`, `usage`, `workers`, `worker_supervision`) with re-bound interfaces, preserving compatibility and simplifying maintenance.
- **Worktree base includes untracked files**: isolated worker worktrees capture untracked files from the base repo via temporary `GIT_INDEX_FILE` trees so workers never miss newly created files, scripts, or configurations.
- **Live reload and tool dispatcher**: `subway(tool, args)` acts as a single dynamic entry point for MCP clients, enabling live reloads of Python modules without client restarts or lost state.
- **Steering running workers**: `instruct_worker` lets callers recall a worker immediately or stack guidance into its backlog, and `send_note` delivers notes directly to active running workers.
- **Dynamic permission updates**: permission mode and policy changes take effect on running workers immediately; sub-worker permissions are clamped to parent levels, and per-thread overrides are supported.
- **Worker reliability fixes**: Claude CLI stdout deduplication prevents duplicate output noise, answered worker shields prevent race conditions, `big-pickle` is recognized as a free model tier, and `beam-free` handles interactive auth blocks cleanly.
- **Local model discovery**: background discovery automatically detects available models across Ollama, LM Studio, llama.cpp, and OpenCode without accessing protected service credential files.
- **Release safety and PR checks**: GitHub Actions CI workflow (`pr.yml`) runs tests on pull requests, with workflow runtimes updated to Node 24.
- **Threads page**: the HUD adds a dedicated Threads dashboard (`/api/threads?scope=subway|all`) backed by `used_chats.json`, configurable project exclusions, and a responsive mobile layout.
- **Inline Copilot rules**: `copilot_inline.py` restricts completions to implementer roles and active window contexts, enforcing an optional completion allowance floor via `copilot_inline_floor_pct` with monthly and per-worker caps.
- **Dynamic model catalog and ping benchmarks**: `DynamicModelCatalog` persists models to `discovered_models.json` with an autonomous 3-stage micro-benchmark (`model_benchmarks.py`) testing auth reachability, tool protocol adherence, and precision single-line diff edits with token speed telemetry under a 40k token cap.
- **Step capabilities and effort**: chained steps in `then` and `on_worker_done` support explicit capability requirements (`capabilities`) and reasoning effort levels (`effort`).
- **Worker limits and memory guard switches**: `worker_limits: false` turns off concurrency capacity limits, and `local_memory_guard: false` (or `SUBWAY_LOCAL_MEMORY_GUARD="0"`) disables local model memory checks.

## 1.3.4 - 2026-11-03

> [!IMPORTANT]
> A stalled worker is never rerouted to a provider that costs more than the one it started on (order: local, OpenCode free, Antigravity Flash, Copilot, Claude Sonnet, Claude Opus). If nothing the same price or cheaper is available, it stops and says why. Before this, a silent Copilot worker could be rerouted to Claude Opus.

- **Terminals you opt in to**: Subway can read, search and (with your permission) run commands in terminals you share. Capture comes from the VS Code extension (`subway.terminalSharing.enabled` plus the **Subway: Share Terminal**, **Stop Sharing Terminal** and **Allow Terminal Commands** commands) or from a shell hook (`subway-context terminal hook install`). Output is redacted and stripped of colour codes, and `list_terminals`, `read_terminal`, `search_terminal` and `run_terminal` work with a new Terminals page in the HUD; `read_terminal` in errors mode keeps line breaks.
- **Sharing has a master switch**: sharing is off until you turn it on in the HUD, and the shell hook and the extension both follow it. Turning it off mid-run keeps no output. The Terminals page has a switch per terminal for sharing and another for letting agents run commands there.
- **The shell hook works in any terminal app**: it runs in zsh and bash in Terminal.app, iTerm2 and editors such as Antigravity. A terminal opened while sharing was off is picked up when you turn sharing on, and commands and exit codes are recorded with their real durations. After installing the hook, open a new tab in terminals you already have.
- **Agents can type into Terminal.app and iTerm2**: in a terminal where you allowed commands, an agent's command is typed into the tab through macOS Automation, which asks once for permission. Keeping the command's output is a separate choice per terminal: Subway reads the tab's text and keeps only the command's output, redacted. In other hook terminals, such as editor terminals, commands are recorded but agents can't run commands there.
- **Protected commands always ask you**: in every permission mode, including auto-approve, Subway asks before publishing (npm publish, vsce, ovsx and twine publish, cargo, docker push), pushing or destroying with git, release and sign-in commands (gh release, gh auth, npm token and npm login), deleting files recursively, sudo, network tools (curl, wget, ssh, scp, rsync), killing processes, and anything that reads or writes secret files such as `~/.ssh`, `~/.npmrc`, `~/.aws`, shell startup files and keychains. Every part of a joined or piped command is checked, including wrappers (env, nohup, xargs, sh -c), command substitution and text passed to an interpreter. `npm stage publish` is allowed unless you change it. The list and its exceptions can only be edited in the HUD settings.
- **Terminal commands are their own permission**: auto-approving workers no longer auto-approves terminal commands. An agent can't answer its own terminal request; only you can, from the HUD or the chat prompt. Output shown to agents is labelled as data, not instructions.
- **Thread recording**: every spawn, follow-up, review, reroute, approval, permission request and answer, note, terminal run and worker finish is written to a per-thread log, along with the settings each worker started with. Secrets are redacted and prompts and commands are shortened. There is no view yet; one comes in a later release.
- **Closed terminals are kept tidy**: when a terminal's shell is gone (Terminal.app, iTerm2 or an editor terminal, detected from the shell's process), its controls are dropped and its log moves to a collapsed "Closed" group on the Terminals page, where you can archive or delete it, one or all. Closed logs are archived automatically after 7 days. Agents can't delete or archive logs.
- **Releases are run by hand from GitHub Actions**: a preview channel (npm `next` tag, GitHub pre-release, version `X.Y.Z-rc.N`) and a production channel that waits for approval in the `release` environment. See `docs/RELEASING.md`.
- **More local models fit**: the memory estimate uses the parent model's measured size, or its parameters times quantization, so it no longer overestimates.
- **Copilot workers keep going**: the stall watchdog counts Copilot session events and a running tool as activity, and gives silent Copilot turns 10 extra minutes. In auto_safe mode Copilot workers run in Copilot's OS sandbox: they can edit files only inside their folder and may run `python3`, so they can run their own tests. The file-write rule now actually covers files inside the folder.
- **Copilot chats are readable**: VS Code Copilot Chat transcripts are read, so `read_chat` shows tool activity and a "Last tool" line, and "Last active" is correct. Copilot CLI `read_chat` shows last active and last tool.
- **Per-chat permission modes**: the HUD sets a permission mode per chat, and workers take their chat's mode. Sub-workers inherit their parent's approval and permission mode, never broader, and a refused command no longer loses a worker's edits.
- **More is visible in worker logs**: Antigravity reads are guarded, Copilot tool inputs, results, permission and subagent events appear in worker logs, logs can be read by range, and older Copilot usage is marked as an estimate.
- **Cut-off workers carry on**: OpenCode and local workers cut off by the output limit are told to continue where they stopped, with their task restated, and the final report keeps both parts. Read-only workers are never told to "make the edits".
- **Threads**: OpenCode dates are fixed (they were shown in the year 58808), and running worker sessions are hidden from Threads.
- **Fixes**: a lead waiting on its own sub-workers is not marked stalled; local models are no longer blamed for OpenCode free-limit errors; the memory guard counts memory macOS can free; HUD dialogs close on a backdrop click; and packaging adds a repository field and keeps assets out of the npm package.

## 1.3.3 - 2026-11-02

> [!IMPORTANT]
> Copilot input tokens counted cache reads and writes again, inflating a long worker's input about 30-fold (credits were always right). Each token is now counted once; older records are corrected for display.

- **Notifications you can trust**: each alert is keyed to its event, so one event alerts once, and each opens the HUD page it is about. Failures are written to `notify.log` (in your Subway folder) instead of vanishing; `subway-context notify test` sends a test alert. terminal-notifier is found in Homebrew folders even when it isn't on PATH. Tests no longer write your real `notify.log`, and every test file runs in its own sandbox, so a single test file can't add workers to your real Subway folder or reach your desktop.
- **No false stalls**: a worker is stalled only when every activity signal it has has been quiet for the stall timeout: its output, its Copilot session events, its OpenCode session, changes in its folder, and its CPU time. Copilot and Antigravity get 5 minutes, others 2. A stall stops and re-routes the worker and gives the model a 15-minute cool-down; only a real quota or usage-limit error marks a model exhausted, until its reset. A successful run clears the cool-down.
- **Quota errors are recognised by shape**, including OpenCode's silent free-limit retries, which are read from its database; one exhausted free model marks the account-wide free limit.
- **Copilot permissions follow your setting**: ask mode runs Copilot over ACP so each tool asks in the HUD (falling back to safe-command approval, with a note, if ACP can't start); safe mode allows only workspace writes and safe commands; `--allow-all-tools` is passed only when permissions are skipped. Worker sessions no longer sync into VS Code, so VS Code shows no approval prompts for them. A USD cap becomes `--max-ai-credits` (1 credit = $0.01).
- **Copilot chats are readable**: `read_chat`, `search_chat` and `/api/chats` cover Copilot CLI sessions and VS Code Copilot chats (workspace, empty-window and Agent Host).
- **Workers work efficiently**: every edit worker's brief carries Subway's standing rules (reading limits, edge cases, test isolation, run the suite twice); reviewers get the reading rule. OpenCode and local workers are refused whole-file reads of files over 1,500 lines (`large_file_read_lines`; `large_file_reads: "allow"` turns it off). Antigravity reads can't be intercepted, so for Gemini the rules are advisory.
- **Edit workers that change nothing fail** with "No changes made", and reviewers are given the worker's diff.
- **Ledger posts are signed by the caller Subway detects**; unverifiable posts are marked unverified and HUD posts are credited to you.
- **The HUD's first load is fast**: each source has its own deadline and late values are shown with their age.

## 1.3.2 - 2026-11-02

- **HUD status is fast**: `/api/status` answers from the last snapshot (per project) and refreshes in the background once it is 10 seconds old or after any change made from the HUD. Only the first request after a restart waits for a full read. Routing, budgets and workers still read live data.
- **No more broken-pipe traces**: a browser that reloads or closes mid-response is not an error, so the HUD no longer prints a traceback for it.

## 1.3.1 - 2026-11-02

> [!IMPORTANT]
> Chat budgets were one global value: a budget set in one chat blocked every other chat whose own total passed it. Budgets now belong to the chat that sets them.

- **Per-chat budgets**: `subway-context budget chat <tokens>` typed in a chat sets that chat's own budget. Run in a terminal it sets a universal default (off unless you set it); `--chat <id>` sets one chat. A chat's budget never blocks another chat. Block and warning messages say whose budget it is and give the exact command. An old global chat budget becomes the universal default. The HUD settings card lists the universal default and each chat's own budget.
- **GitHub Copilot as a provider**:
  - Models come from Copilot Chat's own list on this Mac, one entry per model name, with other ids kept as aliases.
  - Workers run through the Copilot CLI (`npm i -g @github/copilot`, then run `copilot` once to sign in; the VS Code sign-in isn't shared).
  - Each worker records tokens (cached input included), AIC credits, requests, and the model `auto` picked.
  - The CLI can refuse specific models on a plan (Copilot Free accepts only `auto`). Subway learns which, retries once with `auto`, and stops routing refused models to workers; it relearns after 7 days, so a plan upgrade is picked up.
  - Routing follows the live chat allowance and the shared low-allowance rule; an empty chat allowance excludes Copilot. Inline completions are shown, never spent.
  - Copilot appears in model rankings, the usage graph (from Copilot CLI sessions) and every HUD endpoint.
- **Reviews**: Reviewers now receive the builder's task, so they judge the patch against it.
- **Guard**: Workers can edit files in their own worktrees under `~/.subway-context/worktrees`; the rest of Subway's state stays off limits.
- **HUD**: Rankings filters and graph colours come from the providers present; any new provider gets its own colour.
- **Not yet**: Copilot Chat sessions can't be read or handed off from (next release). Subway still can't see OpenCode free-model quota, so a worker on an exhausted free model can stall.

## 1.3.0 - 2026-11-02

> [!IMPORTANT]
> Fixes an unauthenticated budget route in 1.2.0. Users on 1.2.0 should upgrade immediately.

- **Budgets**: `subway-context budget` shows them; `subway-context budget <chat|provider> <tokens>` sets them; typing that command as your own chat message in Claude Code works, including 3.5M and even when blocked. The HUD sets them too. Agents can't: the guard is best effort for shell commands.
- **Budget enforcement**: Warnings at 80%, block at 100%. Claude Code blocks the prompt and tools through hooks. Antigravity and OpenCode have no such hook, so there the block lands on the next Subway tool call.
- **Worker token caps**: Told to wrap up at two thirds, and no call is started that would pass the cap. A call already running can still overrun. Counts thinking and Claude cache creation. Set Claude caps above about 100K: one call can cost 35K.
- **Custom providers**: Report no tokens, so they use max_seconds.
- **Worker pings**: Finished, failed or stopped workers report to the launching chat. Every client gets a line on its next tool result; Claude Code also gets it on your next message.
- **Worker priority**: Urgent, normal, low. Waits return as soon as an urgent worker needs attention.
- **God mode**: No worker count, only free memory limits it. `auto` spreads work across providers with Claude only as a last resort. It may still pick a model with no quota left.
- **OpenCode and local workers**: Run through ACP, so they ask permission and can be denied. `auto_safe` lets them read and edit inside their own folder.
- **Model sign-in**: Models that fail sign-in are skipped for 24 hours.
- **HUD**: Budget rows with real usage, reset card cycles through providers, spend chart coloured per provider, worker history pages with Load more.
- **Worktrees**: Removed after each worker. Windows is written but untested.
- Fail-closed patch verifier.
- Routing by each model's real results.
- Models out of quota skipped.
- Exact code briefs for workers.
- Leases claimed on first edit.
- Compaction warnings and readable history by segment.
- Desktop alerts only for what is still waiting, click to open the HUD.
- The HUD starts with Subway and opens in the editor on first use.
- A Subway: Open HUD editor command.
- Auto-approve never asks.
- OpenCode workers continue when they stop early.
- Copilot usage gauges.
- Kimi usage gauges (experimental, from an undocumented endpoint).
- Compaction markers name the right segment.
- Help page.
- Recommended companion tools in the README.
- Known gaps: Windows untested; no OpenCode Go gauges; a call already running can pass a token cap.

## 1.2.0 - 2026-11-02

- Initial release of budget routes and multi-window transit features.
