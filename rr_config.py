# -*- coding: utf-8 -*-
"""Retro Recoil - config (rr_config.ini).

Same idea as TelTube's teltube.ini: missing keys fall back to the defaults below,
``cfg.section_key`` attribute access, and the file is written on first run.

Relative paths in the [paths] section are resolved against the folder that holds
the config file (the game folder), NOT the shell's current directory, so
``python /somewhere/rr_server.py`` works from anywhere on Windows and Linux.
"""
import configparser
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_INI = """\
; Retro Recoil server configuration.  Missing keys fall back to these defaults.
; Restart the server after editing.

[server]
host = 0.0.0.0
; port 2300 needs no root/admin rights.  Forward 23 -> 2300 on your router if you like.
port = 2300
name = Retro Recoil
max_clients = 60
max_clients_per_ip = 4
; seconds without a key press in the menus before disconnect, 0 = never
idle_timeout = 900
; comma separated IPs / CIDR ranges. Empty allow_ips = everybody allowed.
allow_ips =
deny_ips =
; used until the client reports its window size (telnet NAWS)
default_cols = 80
default_rows = 24
; seconds to wait for telnet option negotiation before the first screen
negotiate_wait = 0.5
; fixed screen size for the "C64 PETSCII" choice (real Commodores have no NAWS)
commodore_cols = 40
commodore_rows = 25

[display]
; truecolor | 256 | 16 | auto.  Players can cycle it with T in the menus.
; Retro Recoil only ever uses the 16 Commodore 64 colours, whatever the mode.
color_mode = truecolor

[game]
match_seconds = 300
respawn_seconds = 5
reload_seconds = 1.5
reload_cooldown_seconds = 3.0
fire_cooldown_seconds = 0.08
magazine_size = 15
bot_count = 5
private_max_players = 5
public_min_lobby = 2
public_max_lobby = 6
fill_wait_seconds = 60
start_countdown_seconds = 3
tickrate = 40
; seconds of invulnerability at the start of a match and after every respawn
start_grace_seconds = 5.0
spawn_protection_seconds = 3.0
; crouch_mode = toggle (press C to crouch, press C again to stand - best over telnet,
; which cannot tell when a key is released) or hold (crouched while C keeps repeating)
crouch_mode = toggle
; telnet has no key-up events, so a movement key counts as "held" for a short window after
; each key event: first press = a short step, auto-repeat keeps it going.
; first press holds long enough to bridge the keyboard's typematic delay (~0.5 s), otherwise W
; stops for a moment before auto-repeat starts
key_first_hold = 0.60       ; upper limit; the server learns your keyboard's real typematic delay
key_repeat_hold = 0.30
; when another key (E, Q, arrows) is pressed, running movement keeps going at least this long
key_carry_hold = 0.45
turn_first_hold = 0.15
turn_repeat_hold = 0.12
turn_rate = 3.0
; player speeds (metres per second) and how long it takes to reach / lose full speed
walk_speed = 7.5
crouch_speed = 3.8
accel_seconds = 0.06
decel_seconds = 0.14
; invisible aim assist for human shots (bots never get it): cone half-angle, extra hit width
; per metre of distance, and base hit radius.  Raise for easier shooting.
assist_cone_deg = 22.0
assist_lateral_slope = 0.22
assist_radius = 0.60
; bot skill: seconds between first seeing you and the first shot, aim error (degrees),
; shots per second.  Raise the first two / lower the last for easier bots.
bot_reaction_min = 4.2
bot_reaction_max = 7.0
bot_aim_error_deg = 44.0
bot_fire_rate = 0.3
; extra seconds before a bot may shoot somebody who has their back to it, and how much worse
; its aim is in that case (multiplier)
bot_behind_delay = 2.2
bot_behind_aim_mult = 2.5

[video]
fps = 20
commodore_fps = 8
; the 3D view is never rendered bigger than this; a bigger terminal gets it scaled up
render_max_cols = 120
render_max_rows = 45
delta_gap_merge = 3
full_refresh_seconds = 15.0
; bytes per second the server will push to one client (ANSI / Commodore)
rate_limit_bytes = 250000
commodore_rate_limit_bytes = 4500

[intro]
; play start.gif (as ascii.bin / petscii.bin) after login.  Any key skips it.
enabled = true
hold_seconds = 2.0
skip_on_key = true
; stretch = fill the whole screen (as asked), contain = keep the picture's proportions
fit = stretch
; playback speed cap on Commodore links (frames are skipped by time, never slowed down)
petscii_fps = 8

[security]
; failed logins allowed per IP / per account before a temporary lock-out
login_max_attempts = 6
login_lockout_seconds = 60

[paths]
map_obj = retro_recoil_map.obj
meta_md = imp-meta.md
database = online.db
intro_gif = start.gif
intro_ascii_bin = ascii.bin
intro_petscii_bin = petscii.bin

[logging]
level = INFO
file =
"""


class Config:
    def __init__(self, path=None):
        self.path = os.path.abspath(path or os.path.join(BASE_DIR, "rr_config.ini"))
        self.base = os.path.dirname(self.path)
        self._cp = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
        self._cp.read_string(DEFAULT_INI)
        if os.path.exists(self.path):
            self._cp.read(self.path, encoding="utf-8")
        else:
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(DEFAULT_INI)
                print(f"Wrote default config to {self.path}")
            except OSError:
                pass

    def __getattr__(self, name):
        if name.startswith("_") or "_" not in name:
            raise AttributeError(name)
        section, key = name.split("_", 1)
        if not self._cp.has_option(section, key):
            raise AttributeError(name)
        raw = self._cp.get(section, key).strip()
        if section == "paths":
            return raw if (not raw or os.path.isabs(raw)) else os.path.join(self.base, raw)
        low = raw.lower()
        if low in ("true", "yes", "on"):
            return True
        if low in ("false", "no", "off"):
            return False
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            return raw
