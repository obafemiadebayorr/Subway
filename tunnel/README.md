# Subway Tunnel

Lets a phone or laptop reach a Subway station from anywhere, without Tailscale or open ports.
The relay only forwards encrypted frames: stations and devices run Noise IK end to end, so the
relay never sees requests, code, or tokens.

- `src/core.ts`: routing, limits and station identity. Pure, tested with `npm test`.
- `src/worker.ts`: the Cloudflare Worker and its Durable Object (one per station, hibernating sockets).

## Run your own (free)
1. A free Cloudflare account.
2. `npx wrangler@4 login`, then `npm run deploy` in this folder.
3. Point the station at it: `subway tunnel --url wss://subway-tunnel.<you>.workers.dev`.

## Protocol
- `GET /v1/station/<sid>` (WebSocket): the relay sends `{"t":"challenge","n"}`. The station replies
  `{"t":"auth","pub","sig"}`, where `sig = Ed25519(n || sid)` and `sid = base32(sha256(pub))[:26]`.
- `GET /v1/client/<sid>` (WebSocket): binary frames go to the station as `[u32 channel][payload]`.
- Control frames (JSON text): `open`, `close`, `station-offline`, `ready`. `ping` is answered with `pong`.
- Limits: 16 clients per station, 1 MiB frames, 64 frames/s per client (burst 128), 30 connects/min per IP.
