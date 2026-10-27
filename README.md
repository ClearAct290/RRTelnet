Address to connect: Coming Soon! (Development still somewhat in the works, but pretty close to final)


<img width="244" height="188" alt="image" src="https://github.com/ClearAct290/RRTelnet/blob/main/start.gif?raw=true" />


A fully 3D, headless-server-rendered telnet shooter for regular ASCII terminals
*and* real Commodore 64 hardware. Connect with nothing but `telnet`, or a C64
and a modem/WiFi modem, and get a raycast 3D view, a live HUD, and real-time
multiplayer - no client install, no graphics card, no mercy.

>Source code is not included in this repository. This repo exists to
> document the project, the controls, and what to expect if you connect -
> not to publish the server implementation. See [Why no code?](#why-no-code)
> below.
<video controls>
  <source src="https://github.com/ClearAct290/RRTelnet/raw/refs/heads/main/preview.mp4" type="video/mp4">
  Your browser does not support the video element.
</video>

## What this is

Every player who connects gets their own independent session. The server
does all the work: it rasterizes the 3D scene (walls, floors, ceilings,
other players as sprites) into plain text and colour codes, and streams
*only the characters that changed* since the last frame - frame by frame,
over a plain telnet socket. Your terminal (or your C64) never runs any game
logic; it just draws what it's told.

* Real 3D raycasting, server-side, per client, every frame
* Two output modes: full-colour ANSI for a normal terminal, and native
PETSCII for an actual Commodore 64
* Bot Practice, public multiplayer lobbies, and private team matches with a
join code
* Persistent accounts with lifetime stats (kills, deaths, KDR, games played)
* An intro animation on login if the server has one configured

## What to expect on hardware

**On a modern terminal (Linux/macOS/Windows, any `telnet` client):**

* Full 16-colour (Commodore-palette) ANSI output, rendered in truecolor,
256-colour, or 16-colour mode depending on what your terminal supports
* Recommended window size: 80x24 minimum; bigger windows get a bigger 3D
viewport, up to a server-side cap, so more terminal = more to see
* A wired connection or stable WiFi is plenty - the protocol is built to
be light, not just tolerant
* Arrow keys and letter keys both work for every control (see below)

**On a real Commodore 64 (or an emulator in "real hardware" mode):**

* Connect via a WiFi modem / cartridge (WiModem, 1541 Ultimate, etc.) or
any serial-to-telnet bridge you already use for BBSing
* The server detects a native C64 connection and switches to PETSCII mode
automatically: 40x25 characters, the real C64 colour set, reverse-video
cells instead of ANSI escape codes
* Expect a noticeably choppier frame rate than on a PC terminal - this is
expected and tuned for, not a bug. The C64 link runs under its own,
much tighter bandwidth budget than a PC terminal does, and frames are
dropped and caught up smoothly rather than queued and lagged
* Because a C64 has no way to tell the server when a key is *released*,
held-down movement (walking, turning) is simulated from how fast your
keyboard auto-repeats. This works well in practice but means your first
tap of a direction is always a short, deliberate step, not an instant
dash
* No special software needed beyond whatever gets you a telnet connection
out of your C64 - the server does not care whether you're on real
silicon or an emulator, it only sees "a C64 says hello" at the start of
the connection

**On both:** the server never sends more than it has to. If your link is
slow (a C64 on a modem, a flaky WiFi connection), the view will update
less of the screen per frame rather than getting bigger and bigger behind
on a queue - so movement always feels responsive, even if detail briefly
lags.

\---

## Controls

|Key|Action|
|-|-|
|`W` / `UP`|Walk forward (hold it down)|
|`S` / `DOWN`|Walk backward|
|`A`|Shuffle left|
|`D`|Shuffle right|
|`LEFT` / `RIGHT`|Turn left / right|
|`E`|Shoot|
|`Q`|Reload|
|`C`|Crouch / stand back up|
|`SPACE`|Stop moving immediately|
|`ESC`|Leave the match (confirms first)|

Movement over telnet works by auto-repeat: a quick tap of `W` is a short
step, and holding it down (so the key keeps repeating) is a sustained walk.
Because there's no "key released" event in telnet, you'll coast for a
moment after letting go - `SPACE` cancels that instantly if you need to
stop on a dime.

### HUD

|Shown as|Meaning|
|-|-|
|`K-n`|Your kills this match|
|`D-n`|Your deaths this match|
|`A-n`|Rounds left in your magazine (15 per reload)|
|orange `n.n`|Seconds until you can fire again (only shown with an empty gun)|
|`a>b`|Kill feed, top right: player `a` shot player `b`|
|red `!`|An enemy you can't see has you in their sights. Position on screen shows direction: bottom middle = behind you, left/right = off to that side, sliding toward the edge as they come into view. Two stacked marks = they're close.|
|`+`|Crosshair - line it up on a target, then press `E`|
|`m:ss`|Time left in the match (turns red in the final 15 seconds)|
|`YOU DIED`|You're out until the respawn countdown finishes|

### How to play

* **Goal:** rack up kills, avoid deaths. Ranking is by KDR (kills ÷
deaths) first, then total kills.
* **Match length:** 5 minutes. You can't be hurt for the first 5 seconds of
a match, or for 3 seconds after you respawn.
* **Aiming:** turn with `LEFT`/`RIGHT` until the target sits on the `+`,
then press `E`. Shots are hitscan (instant) with a little forgiveness
around the crosshair. One round per press of `E`.
* **Ammo:** 15 rounds per magazine. `Q` reloads in 1.5 seconds, but only
starts 3 seconds after your last shot - an empty gun means a short wait
before you can reload.
* **Cover:** walls and crates block shots. Crouch (`C`) to fit through low
holes and hide pockets. A crouched player can only be hit from within 9
metres unless you're crouched too.
* **Watch your back:** the red `!` HUD marks mean exactly what they say -
bots and players alike will shoot you from behind if you let them.
* **Modes:**

  * **Bot Practice** - you against a handful of AI opponents, good for
warming up or learning the maps
  * **Multiplayer** - joins a public lobby that fills up and starts
automatically
  * **Private Team** - hosts a match with a short join code to share with
friends

\---

## Why no code?

This repo is here to document the project and let people see what it does
and how to play it - not to hand out a working copy of the server. Keeping
the implementation private keeps the account system, matchmaking, and bot
AI harder to reverse-engineer for cheating, scripting, or griefing than a
public source dump would. If that changes down the line, this README will
say so.

\---

## Status

Playable, actively maintained. Known rough edges and anything intentionally
left simple for now are tracked privately rather than listed here, since
that list would itself be a roadmap for exactly the kind of poking at the
server this repo is trying not to invite.

