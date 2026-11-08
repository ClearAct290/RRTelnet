# Retro Recoil

A fully 3D, headless-server-rendered telnet shooter for regular ASCII
terminals and Commodore 64 PETSCII clients. Every connection gets its own
independent session; the server rasterizes the 3D scene per client (your
`.obj` map, raycast, shaded, sprite-projected stick figures and all) and
streams only the terminal cells that actually changed each frame.

Rendering, the PETSCII/ASCII delta-transport approach, and the general
server shape are adapted from the `TelTube` project (GPLv2+, see
`LICENSE`), with a different (exact cell-diff, no fade/ghost-line) delta
algorithm and a server config of its own - see `imp-meta.md` for details on
what differs and why.

## Requirements

```
pip install numpy pillow
```

Python 3.9+. Works on Windows and Linux (plain sockets via asyncio - no
platform-specific calls).

## Running

```bash
python make_map.py        # (re)generates retro_recoil_map.obj - only needed once, or after editing the map
python rr_server.py        # reads/writes rr_config.ini next to it; default port 2300
```

Connect with any telnet client:

```bash
telnet localhost 2300
```

Port 2300 avoids needing root (unlike port 23). Change `[server] port` in
`rr_config.ini` if you want something else, then restart.

## Files

| File | What it is |
|---|---|
| `rr_server.py` | entry point, `App` (shared state), lobby/matchmaking, accept loop |
| `rr_session.py` | one connection: telnet/PETSCII IO, key parsing, the screen state machine |
| `rr_screens.py` | every screen - terminal choice, login/create account, menus, lobbies, the live game HUD |
| `rr_match.py` | the live simulation: movement, hitscan shooting, reload, respawn |
| `rr_bots.py` | bot AI (perception, pathing via the nav grid, aiming, firing) |
| `rr_render.py` | `Grid`/`Presenter` (ANSI + PETSCII delta transport), the headless 3D raycaster, HUD art |
| `rr_world.py` | `.obj` map loader, collision, line-of-sight, A* nav grids (standing + crouched) |
| `rr_intro.py` | converts `start.gif` into `ascii.bin`/`petscii.bin` and plays them back |
| `rr_db.py` | `online.db` (sqlite3) - accounts and lifetime stats |
| `rr_config.py` | `rr_config.ini` loader (written with defaults on first run) |
| `make_map.py` | generates `retro_recoil_map.obj` (the two courtyards, hallway ring, hide holes) |
| `imp-meta.md` | the in-game Controls screen text, plus map-format/account/intro notes |

## Intro animation

If `start.gif` is present next to the server at startup, it's converted
once into `ascii.bin` and `petscii.bin` (regenerated automatically if
either goes missing while the gif is still there) and played once right
after login or account creation, holding the last frame for 2 seconds
before the main menu. No `start.gif` -> no intro, silently.

## Known simplifications vs. a shipped game

This is a complete, playable first version, not a hardened production
server - in particular: the delta-compressed transport, nav-grid bot AI,
hide-hole crouch-lock behaviour, and the full account/login/menu/lobby flow
described in the spec are all implemented and exercised end-to-end, but a
few things are intentionally simple for now and worth hardening before
exposing this publicly:

* no TLS (telnet is unencrypted, like real BBS telnet always was)
* no persistent reconnect-to-in-progress-match support if a client drops mid-game
* public multiplayer matchmaking is one in-process lobby table (fine for one
  server instance; would need shared state to run behind multiple processes)
