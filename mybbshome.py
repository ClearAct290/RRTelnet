#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MyBBSHome - a tiny telnet "home page" / launcher for your BBS.

Copyright (C) 2026 ClearAct290-Reddit
Licensed under the GNU General Public License, version 2 (see LICENSE).

Visitors telnet in, see your logo and a short description, pick a BBS from an
arrow-key list (list.txt) and MyBBSHome tunnels them straight through to it.
Plain black & white text: works with any telnet client.

    python mybbshome.py [path/to/mybbshome.ini]

Files (created with defaults on first run, next to this script / the ini):
    mybbshome.ini   settings        (see comments inside)
    logo.txt        your ASCII logo
    list.txt        the menu:  TLE="Label" ADS="host:port" [HIDEIP]   (# hides a line)

Logo, text and list.txt are re-read for every visitor, so edits show up
without a restart.  Only the [server] settings need a restart.
"""
import asyncio
import configparser
import ipaddress
import logging
import os
import re
import socket
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("mybbshome")

# --------------------------------------------------------------------------- defaults

DEFAULT_LOGO = r""" __  __       ____  ____ ____  _   _
|  \/  |_   _| __ )| __ ) ___|| | | | ___  _ __ ___   ___
| |\/| | | | |  _ \|  _ \___ \| |_| |/ _ \| '_ ` _ \ / _ \
| |  | | |_| | |_) | |_) |__) |  _  | (_) | | | | | |  __/
|_|  |_|\__, |____/|____/____/|_| |_|\___/|_| |_| |_|\___|
        |___/
"""

DEFAULT_LIST = """\
# MyBBSHome list - one entry per line:
#
#     TLE="Label shown on the menu" ADS="host:port"
#
# The port is optional (default 23).  Put a # in front of a line to hide it.
# This file is re-read for every visitor, no restart needed.
#
# Local addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x, 127.x.x.x, *.local/.lan
# names ...) work too: MyBBSHome connects to them from the machine it runs on, so
# visitors from the internet can reach things on your home network.  Their
# address is ALWAYS hidden from visitors (this cannot be turned off).
#
# Add HIDEIP at the end of a line to also hide the address of a public host:
#
#     TLE="Secret BBS" ADS="bbs.example.net:2323" HIDEIP

TLE="EXAMPLE" ADS="example.com:23"
#TLE="Another BBS" ADS="bbs.example.net:2323" HIDEIP
#TLE="My C64" ADS="192.168.1.50:6400"
"""

DEFAULT_INI = r"""; MyBBSHome configuration.  Missing keys fall back to these defaults.
; Only the [server] section needs a restart; everything else is re-read per visitor.

[server]
host = 0.0.0.0
; port 2300 needs no root/admin rights.  Use 23 (or forward 23 -> 2300) for the standard telnet port.
port = 2300
max_clients = 100
max_clients_per_ip = 5
; seconds without a key press on the home page before disconnect, 0 = never
idle_timeout = 900
; comma separated IPs / CIDR ranges. Empty allow_ips = everybody allowed.
allow_ips =
deny_ips =
; used until the client reports its window size (telnet NAWS)
default_cols = 80
default_rows = 24
; seconds to wait for telnet option negotiation before the first screen
negotiate_wait = 0.5

[home]
; shown above the logo (leave empty to hide)
created_text = Created with MyBBSHome
; shown under the logo (use \n for a line break, empty to hide)
description = Configure your MyBBSHome to make it yours!
show_logo = true
center = true
; marker = "> Label <"   reverse = inverse video   both = marker + inverse video
highlight = both
show_help = true
help_text = Up/Down: select   Enter: connect   Q: quit
goodbye_text = Goodbye!

[login]
; true = ask for a username + password on connect and pass them on to the BBS you pick.
; (nothing is stored or checked here; blank username = skip)
prompt = false
hint = Your sign-in is passed along to the BBS you pick. Leave blank to skip.
; case-insensitive regexes matched against the end of what the remote BBS sends
username_prompt = (login|user ?name|user ?id|handle|your name|name)\s*[:>?]\s*$
password_prompt = pass(word|code|phrase)?\s*[:>?]\s*$
; seconds after connecting during which those prompts are watched for
wait_seconds = 20
; line ending sent after username/password: crlf | cr | crnul | lf
newline = crlf

[connect]
; seconds to wait for the remote BBS to answer
timeout = 10
; true = when the remote BBS hangs up you land back on the home page
return_after_disconnect = true

[paths]
list = list.txt
logo = logo.txt

[logging]
level = INFO
file =
"""


# --------------------------------------------------------------------------- config

class Config:
    """cfg.section_key attribute access; missing keys fall back to DEFAULT_INI."""

    def __init__(self, path=None):
        self.path = os.path.abspath(path or os.path.join(BASE_DIR, "mybbshome.ini"))
        self.base = os.path.dirname(self.path)
        if not os.path.exists(self.path):
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(DEFAULT_INI)
                print(f"Wrote default config to {self.path}")
            except OSError:
                pass
        self.reload()

    def reload(self):
        cp = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
        cp.read_string(DEFAULT_INI)
        if os.path.exists(self.path):
            try:
                cp.read(self.path, encoding="utf-8-sig")
            except configparser.Error as e:
                log.warning("config error in %s: %s (using defaults for the rest)", self.path, e)
        self._cp = cp

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


def ensure_file(path, text):
    if path and not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            print(f"Wrote default {os.path.basename(path)}")
        except OSError as e:
            log.warning("could not write %s: %s", path, e)


# --------------------------------------------------------------------------- home page data

class Entry:
    def __init__(self, label, host, port, hide=False):
        self.label, self.host, self.port = label, host, port
        self.local = is_local_host(host)
        self.hide = bool(hide) or self.local     # local addresses are always hidden


_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".localdomain", ".localhost", ".intranet")


def is_local_ip(text):
    """True for loopback / private / link-local / CGNAT / unspecified addresses."""
    try:
        a = ipaddress.ip_address(str(text).split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_unspecified
            or (a.version == 4 and a in _CGNAT))


def is_local_host(host):
    """Local by its written form: a local IP, localhost, a *.local style name, or a bare name."""
    h = host.strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(h.split("%")[0])
        return is_local_ip(h)
    except ValueError:
        pass
    return h == "localhost" or h.endswith(_LOCAL_SUFFIXES) or "." not in h


ENTRY_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def parse_addr(s):
    s = s.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.rstrip("/")
    port = ""
    if s.startswith("["):                       # [ipv6]:port
        host, _, rest = s[1:].partition("]")
        port = rest.lstrip(":")
    elif s.count(":") == 1:
        host, port = s.split(":")
    else:
        host = s
    host = host.strip()
    port = int(port) if port.strip().isdigit() else 23
    if not host or not (1 <= port <= 65535):
        raise ValueError(f"bad address {s!r}")
    return host, port


def load_entries(path):
    entries = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except OSError:
        return entries
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        d = {k.upper(): v for k, v in ENTRY_RE.findall(s)}
        if "TLE" not in d or "ADS" not in d:
            log.warning("%s line %d ignored (need TLE=\"...\" ADS=\"...\")", os.path.basename(path), n)
            continue
        try:
            host, port = parse_addr(d["ADS"])
        except ValueError as e:
            log.warning("%s line %d ignored: %s", os.path.basename(path), n, e)
            continue
        rest = ENTRY_RE.sub(" ", s)
        hide = bool(re.search(r"(?<![\w-])HIDEIP(?![\w-])", rest, re.I)) or \
            d.get("HIDEIP", "").strip().lower() in ("1", "yes", "true", "on")
        entries.append(Entry(d["TLE"].strip() or d["ADS"], host, port, hide))
    return entries


def load_logo(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        text = DEFAULT_LOGO
    lines = [l.rstrip().expandtabs(4) for l in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


class Home:
    def __init__(self, cfg):
        self.cfg = cfg
        self.created = str(cfg.home_created_text)
        self.logo = load_logo(cfg.paths_logo) if cfg.home_show_logo else []
        desc = str(cfg.home_description).replace("\\n", "\n")
        self.desc = desc.split("\n") if desc.strip() else []
        self.center = bool(cfg.home_center)
        self.entries = load_entries(cfg.paths_list)


# --------------------------------------------------------------------------- telnet

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ECHO, SGA, NAWS = 1, 3, 31


class Disconnected(Exception):
    pass


class IdleTimeout(Exception):
    pass


class Term:
    """One visitor: telnet option handling + key reading + simple output."""

    def __init__(self, reader, writer, cfg):
        self.r, self.w, self.cfg = reader, writer, cfg
        self.cols = int(cfg.server_default_cols)
        self.rows = int(cfg.server_default_rows)
        self.plain = bytearray()
        self._st = 0
        self._cmd = 0
        self._sb = bytearray()
        self._refused = set()
        self._cr = False
        self.eof = False
        self.resized = False
        self.got_naws = False
        self.naws_refused = False

    # ---- output
    def send(self, data):
        if isinstance(data, str):
            data = data.encode("latin-1", "replace")
        try:
            self.w.write(data)
        except (ConnectionError, OSError, RuntimeError):
            raise Disconnected()

    async def flush(self):
        try:
            await self.w.drain()
        except (ConnectionError, OSError):
            raise Disconnected()

    # ---- telnet parsing
    def _feed(self, data):
        for b in data:
            st = self._st
            if st == 0:
                if b == IAC:
                    self._st = 1
                else:
                    self.plain.append(b)
            elif st == 1:
                if b == IAC:
                    self.plain.append(255)
                    self._st = 0
                elif b in (WILL, WONT, DO, DONT):
                    self._cmd = b
                    self._st = 2
                elif b == SB:
                    self._sb = bytearray()
                    self._st = 3
                else:
                    self._st = 0
            elif st == 2:
                self._option(self._cmd, b)
                self._st = 0
            elif st == 3:
                if b == IAC:
                    self._st = 4
                else:
                    self._sb.append(b)
            elif st == 4:
                if b == SE:
                    self._sub(bytes(self._sb))
                    self._st = 0
                elif b == IAC:
                    self._sb.append(255)
                    self._st = 3
                else:
                    self._st = 3

    def _option(self, cmd, opt):
        if cmd == DO:
            if opt in (ECHO, SGA):
                return                           # client accepted our WILL
            self._refuse(WONT, opt)
        elif cmd == WILL:
            if opt == NAWS:
                return                           # client accepted our DO
            self._refuse(DONT, opt)
        elif cmd == WONT and opt == NAWS:
            self.naws_refused = True

    def _refuse(self, reply, opt):
        if (reply, opt) not in self._refused:
            self._refused.add((reply, opt))
            self.send(bytes([IAC, reply, opt]))

    def _sub(self, sb):
        if len(sb) >= 5 and sb[0] == NAWS:
            cols = (sb[1] << 8) | sb[2]
            rows = (sb[3] << 8) | sb[4]
            self.got_naws = True
            if cols > 0 and rows > 0 and (cols, rows) != (self.cols, self.rows):
                self.cols, self.rows = cols, rows
                self.resized = True

    async def _recv(self, timeout=None):
        data = await asyncio.wait_for(self.r.read(4096), timeout)
        if not data:
            self.eof = True
            return False
        self._feed(data)
        return True

    async def negotiate(self):
        """Character-at-a-time mode (we echo), ask for window size."""
        self.send(bytes([IAC, WILL, ECHO, IAC, WILL, SGA, IAC, DO, NAWS]))
        await self.flush()
        loop = asyncio.get_running_loop()
        end = loop.time() + float(self.cfg.server_negotiate_wait)
        while not self.got_naws and not self.naws_refused and not self.eof:
            left = end - loop.time()
            if left <= 0:
                break
            try:
                await self._recv(left)
            except asyncio.TimeoutError:
                break
        if self.eof:
            raise Disconnected()
        self.resized = False

    # ---- keys
    def _take(self):
        p = self.plain
        while p:
            b = p[0]
            if self._cr and b in (0, 10):
                del p[0]
                self._cr = False
                continue
            self._cr = False
            if b == 13:
                del p[0]
                self._cr = True
                return "ENTER"
            if b == 10:
                del p[0]
                return "ENTER"
            if b in (8, 127):
                del p[0]
                return "BACKSPACE"
            if b in (3, 4):
                del p[0]
                return "CTRL_C"
            if b == 27:
                if len(p) < 2:
                    return None
                if p[1] == 79:                   # ESC O x
                    if len(p) < 3:
                        return None
                    final = chr(p[2])
                    del p[:3]
                    key = {"A": "UP", "B": "DOWN", "H": "HOME", "F": "END"}.get(final)
                    if key:
                        return key
                    continue
                if p[1] == 91:                   # ESC [ params final
                    i = 2
                    while i < len(p) and not (0x40 <= p[i] <= 0x7E):
                        i += 1
                    if i >= len(p):
                        return None
                    params = bytes(p[2:i]).decode("latin-1")
                    final = chr(p[i])
                    del p[:i + 1]
                    key = {"A": "UP", "B": "DOWN", "H": "HOME", "F": "END"}.get(final)
                    if final == "~":
                        key = {"1": "HOME", "7": "HOME", "4": "END", "8": "END",
                               "5": "PGUP", "6": "PGDN"}.get(params.split(";")[0])
                    if key:
                        return key
                    continue
                del p[0]                         # ESC + something else: drop the ESC
                continue
            del p[0]
            if 32 <= b < 127:
                return chr(b)
        return None

    async def read_key(self, timeout=None):
        while True:
            if self.resized:
                self.resized = False
                return "RESIZE"
            k = self._take()
            if k:
                return k
            if self.eof:
                raise Disconnected()
            lone = len(self.plain) == 1 and self.plain[0] == 27
            try:
                await self._recv(0.12 if lone else timeout)
            except asyncio.TimeoutError:
                if lone:
                    self.plain.clear()
                    continue
                raise IdleTimeout()
            except (ConnectionError, OSError):
                raise Disconnected()

    async def read_line(self, prompt, mask=False, maxlen=64, timeout=None):
        self.send(prompt)
        await self.flush()
        buf = []
        while True:
            k = await self.read_key(timeout)
            if k == "ENTER":
                self.send("\r\n")
                await self.flush()
                return "".join(buf)
            if k == "CTRL_C":
                raise Disconnected()
            if k == "BACKSPACE":
                if buf:
                    buf.pop()
                    self.send("\b \b")
            elif len(k) == 1 and len(buf) < maxlen:
                buf.append(k)
                self.send("*" if mask else k)
            await self.flush()

    def reset_input(self):
        self.plain.clear()
        self._st = 0
        self._cr = False
        self._refused.clear()
        self.resized = False


# --------------------------------------------------------------------------- drawing

def _center_each(lines, cols, center):
    out = []
    for l in lines:
        l = l[:cols - 1]
        out.append(l.center(cols - 1).rstrip() if center else l)
    return out


def _center_block(lines, cols, center):
    w = max((len(l) for l in lines), default=0)
    pad = max(0, (cols - 1 - w) // 2) if center else 0
    return [(" " * pad + l)[:cols - 1] for l in lines]


def _wrap(lines, cols):
    import textwrap
    out = []
    for l in lines:
        out += textwrap.wrap(l, max(10, cols - 4)) or [""]
    return out


def build_header(home, cols, rows, need, footer):
    h = []
    for use_logo, use_desc in ((True, True), (False, True), (False, False)):
        h = []
        if home.created.strip():
            h += _center_each([home.created], cols, home.center)
            h.append("")
        if use_logo and home.logo:
            h += _center_block(home.logo, cols, home.center)
            h.append("")
        if use_desc and home.desc:
            h += _center_each(_wrap(home.desc, cols), cols, home.center)
            h.append("")
        if rows - 1 - len(h) - footer >= need:
            return h
    return h


def draw_menu(home, term, sel, top):
    """Return (screen_text, new_top)."""
    cols, rows = term.cols, term.rows
    cfg = home.cfg
    n = len(home.entries)
    footer = 2 if cfg.home_show_help else 0
    header = build_header(home, cols, rows, min(max(n, 1), 3), footer)
    avail = max(1, rows - 1 - len(header) - footer)

    if sel < top:
        top = sel
    if sel >= top + avail:
        top = sel - avail + 1
    top = max(0, min(top, max(0, n - avail)))

    lines = list(header)
    mode = str(cfg.home_highlight).lower()
    if n == 0:
        lines += _center_each(['(no entries - add some to list.txt)'], cols, home.center)
    else:
        maxl = min(max(len(e.label) for e in home.entries), max(1, cols - 8))
        bw = maxl + 4
        pad = " " * (max(0, (cols - 1 - bw) // 2) if home.center else 0)
        for i in range(top, min(n, top + avail)):
            label = home.entries[i].label[:maxl].ljust(maxl)
            if i == sel:
                if mode in ("marker", "both"):
                    text = "> " + label + " <"
                else:
                    text = "  " + label + "  "
                if mode in ("reverse", "both"):
                    text = "\x1b[7m" + text + "\x1b[0m"
            else:
                text = "  " + label + "  "
            lines.append(pad + text)
    if footer:
        while len(lines) < len(header) + avail:
            lines.append("")
        lines.append("")
        help_text = str(cfg.home_help_text)
        if n > avail:
            help_text += f"  ({sel + 1}/{n})"
        lines += _center_each([help_text], cols, home.center)

    body = "\r\n".join(l + "\x1b[K" for l in lines)
    return "\x1b[H" + body + "\x1b[J", top


async def home_menu(home, term):
    """Show the list; return the chosen Entry, or None to quit."""
    idle = float(home.cfg.server_idle_timeout) or None
    n = len(home.entries)
    sel = top = 0
    term.send("\x1b[2J\x1b[?25l")
    while True:
        screen, top = draw_menu(home, term, sel, top)
        term.send(screen)
        await term.flush()
        k = await term.read_key(idle)
        page = max(1, term.rows - 8)
        if k in ("UP", "k", "w"):
            sel = max(0, sel - 1)
        elif k in ("DOWN", "j", "s"):
            sel = min(max(0, n - 1), sel + 1)
        elif k == "HOME":
            sel = 0
        elif k == "END":
            sel = max(0, n - 1)
        elif k == "PGUP":
            sel = max(0, sel - page)
        elif k == "PGDN":
            sel = min(max(0, n - 1), sel + page)
        elif k == "RESIZE":
            term.send("\x1b[2J")
        elif k == "ENTER":
            if n:
                return home.entries[sel]
        elif k in ("q", "Q", "CTRL_C"):
            return None


async def login_screen(home, term):
    """Ask for username/password (kept in memory only). Returns (user, pw)."""
    cfg = home.cfg
    header = build_header(home, term.cols, term.rows, 0, 0)
    term.send("\x1b[2J\x1b[H" + "\r\n".join(header) + "\r\n")
    hint = str(cfg.login_hint)
    if hint:
        term.send("\r\n".join(_center_each(_wrap([hint], term.cols), term.cols, home.center)) + "\r\n\r\n")
    idle = float(cfg.server_idle_timeout) or None
    user = (await term.read_line("Username: ", timeout=idle)).strip()
    pw = ""
    if user:
        pw = await term.read_line("Password: ", mask=True, timeout=idle)
    return user, pw


# --------------------------------------------------------------------------- tunnel

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
IAC_RE = re.compile(r"\xff[\xfb-\xfe].|\xff\xfa.*?\xff\xf0|\xff.", re.S)
NEWLINES = {"crlf": b"\r\n", "cr": b"\r", "crnul": b"\r\x00", "lf": b"\n"}


def strip_replies(data, swallow):
    """Drop the client's answers to the options we switched off (IAC cmd opt)."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b != IAC or not swallow:
            out.append(b)
            i += 1
            continue
        if i + 1 >= n:
            return bytes(out), data[i:]
        c = data[i + 1]
        if c == IAC:
            out += b"\xff\xff"
            i += 2
        elif c in (WILL, WONT, DO, DONT):
            if i + 2 >= n:
                return bytes(out), data[i:]
            key = (c, data[i + 2])
            if key in swallow:
                swallow.discard(key)
            else:
                out += data[i:i + 3]
            i += 3
        else:
            out += data[i:i + 2]
            i += 2
    return bytes(out), b""


async def tunnel(term, rr, rw, cfg, creds):
    """Pipe bytes both ways, untouched. Optionally answer the BBS's login prompts.
    Returns "client" or "remote" = which side hung up."""
    loop = asyncio.get_running_loop()
    swallow = {(DONT, ECHO), (DONT, SGA), (WONT, NAWS)}
    nl = NEWLINES.get(str(cfg.login_newline).lower(), b"\r\n")
    try:
        user_re = re.compile(str(cfg.login_username_prompt), re.I)
        pass_re = re.compile(str(cfg.login_password_prompt), re.I)
    except re.error as e:
        log.warning("bad login regex: %s", e)
        creds = None
    state = {"user": not (creds and creds[0]), "pw": not (creds and creds[0] and creds[1])}
    deadline = loop.time() + float(cfg.login_wait_seconds)

    async def c2r():
        carry = b""
        try:
            while True:
                data = await term.r.read(4096)
                if not data:
                    return "client"
                data, carry = strip_replies(carry + data, swallow)
                if data:
                    rw.write(data)
                    await rw.drain()
        except (ConnectionError, OSError):
            return "client"

    async def r2c():
        tail = ""
        try:
            while True:
                data = await rr.read(4096)
                if not data:
                    return "remote"
                term.w.write(data)
                await term.w.drain()
                if (state["user"] and state["pw"]) or loop.time() > deadline:
                    continue
                tail = (tail + IAC_RE.sub("", ANSI_RE.sub("", data.decode("latin-1")))
                        .replace("\r", "").replace("\x00", ""))[-200:]
                if not state["user"] and user_re.search(tail):
                    state["user"] = True
                    tail = ""
                    await asyncio.sleep(0.15)
                    rw.write(creds[0].encode("latin-1", "replace") + nl)
                    await rw.drain()
                elif state["user"] and not state["pw"] and pass_re.search(tail):
                    state["pw"] = True
                    tail = ""
                    await asyncio.sleep(0.15)
                    rw.write(creds[1].encode("latin-1", "replace") + nl)
                    await rw.drain()
        except (ConnectionError, OSError):
            return "remote"

    t1, t2 = asyncio.create_task(c2r()), asyncio.create_task(r2c())
    done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for p in pending:
        p.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    side = "client"
    for t in done:
        if not t.cancelled() and t.exception() is None:
            side = t.result()
    try:
        rw.close()
    except Exception:
        pass
    return side


async def launch(term, entry, cfg, creds, ip):
    """Connect to the entry and tunnel. Returns True if we should go back to the home page."""
    hide = entry.hide
    timeout = float(cfg.connect_timeout)
    err = None
    term.send("\x1b[2J\x1b[H\x1b[?25h\x1b[0m")
    try:
        # resolve first: a name that points at a local address is hidden too, and
        # the "Connecting" line must not leak the address before we know that
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(entry.host, entry.port, type=socket.SOCK_STREAM), timeout)
        if any(is_local_ip(i[4][0]) for i in infos):
            hide = True
        where = "" if hide else f" ({entry.host}:{entry.port})"
        term.send(f"Connecting to {entry.label}{where} ...\r\n")
        await term.flush()
        rr, rw = await asyncio.wait_for(asyncio.open_connection(entry.host, entry.port), timeout)
    except asyncio.TimeoutError:
        err, detail = "timed out", "timed out"
    except socket.gaierror as e:
        err, detail = "could not find that host", str(e)
    except ConnectionRefusedError as e:
        err, detail = "connection refused", str(e)
    except (OSError, UnicodeError) as e:
        err, detail = "connection failed", str(e)
    if err:
        log.info("%s -> %s:%s failed: %s", ip, entry.host, entry.port, detail)
        term.send(f"\r\nCould not connect: {err}\r\n\r\nPress any key to go back.")
        await term.flush()
        await term.read_key(60)
        return True

    log.info("%s -> %s (%s:%s)%s", ip, entry.label, entry.host, entry.port, " [hidden]" if hide else "")
    # hand the terminal over: undo the options we set so the BBS can negotiate its own
    term.send(bytes([IAC, WONT, ECHO, IAC, WONT, SGA, IAC, DONT, NAWS]) + b"\x1b[2J\x1b[H")
    await term.flush()
    side = await tunnel(term, rr, rw, cfg, creds)
    log.info("%s <- %s closed by %s", ip, entry.label, side)
    if side == "client" or not cfg.connect_return_after_disconnect:
        return False

    # remote hung up: take the terminal back
    term.reset_input()
    term.send("\x1b[0m\x1b[?25h\r\n\r\n[ Connection closed - press any key to return ]")
    term.send(bytes([IAC, WILL, ECHO, IAC, WILL, SGA, IAC, DO, NAWS]))
    await term.flush()
    await term.read_key(300)
    return True


# --------------------------------------------------------------------------- server

class Limits:
    total = 0
    per_ip = {}


def parse_nets(text):
    nets = []
    for part in str(text).split(","):
        part = part.strip()
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                log.warning("bad IP/CIDR in config: %r", part)
    return nets


def ip_in(ip, nets):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return any(a.version == n.version and a in n for n in nets)


async def handle(reader, writer, cfg):
    peer = writer.get_extra_info("peername") or ("?", 0)
    ip = peer[0]
    cfg.reload()
    allow, deny = parse_nets(cfg.server_allow_ips), parse_nets(cfg.server_deny_ips)
    refuse = None
    if (allow and not ip_in(ip, allow)) or ip_in(ip, deny):
        refuse = "Access denied."
    elif Limits.total >= int(cfg.server_max_clients) or \
            Limits.per_ip.get(ip, 0) >= int(cfg.server_max_clients_per_ip):
        refuse = "Sorry, too many connections right now. Try again later."
    if refuse:
        log.info("%s refused: %s", ip, refuse)
        try:
            writer.write((refuse + "\r\n").encode())
            await writer.drain()
        except OSError:
            pass
        writer.close()
        return

    Limits.total += 1
    Limits.per_ip[ip] = Limits.per_ip.get(ip, 0) + 1
    log.info("%s connected", ip)
    term = Term(reader, writer, cfg)
    try:
        await term.negotiate()
        creds = None
        if cfg.login_prompt:
            creds = await login_screen(Home(cfg), term)
        while True:
            home = Home(cfg)                     # re-read logo/text/list.txt every time
            entry = await home_menu(home, term)
            if entry is None:
                term.send("\x1b[2J\x1b[H\x1b[?25h" + str(cfg.home_goodbye_text) + "\r\n")
                await term.flush()
                break
            if not await launch(term, entry, cfg, creds, ip):
                break
    except IdleTimeout:
        try:
            term.send("\x1b[?25h\r\n\r\nIdle too long - goodbye.\r\n")
            await term.flush()
        except Disconnected:
            pass
    except (Disconnected, ConnectionError, OSError):
        pass
    except Exception:
        log.exception("%s: session error", ip)
    finally:
        Limits.total -= 1
        Limits.per_ip[ip] = Limits.per_ip.get(ip, 1) - 1
        if Limits.per_ip[ip] <= 0:
            Limits.per_ip.pop(ip, None)
        try:
            writer.close()
        except Exception:
            pass
        log.info("%s disconnected", ip)


async def serve(cfg):
    server = await asyncio.start_server(lambda r, w: handle(r, w, cfg),
                                        cfg.server_host, int(cfg.server_port))
    addrs = ", ".join(str(s.getsockname()[1]) for s in server.sockets)
    log.info("MyBBSHome listening on %s port %s", cfg.server_host, addrs)
    async with server:
        await server.serve_forever()


def main():
    cfg = Config(sys.argv[1] if len(sys.argv) > 1 else None)
    ensure_file(cfg.paths_list, DEFAULT_LIST)
    ensure_file(cfg.paths_logo, DEFAULT_LOGO)
    handlers = [logging.StreamHandler()]
    if cfg.logging_file:
        handlers = [logging.FileHandler(cfg.logging_file, encoding="utf-8")]
    logging.basicConfig(level=getattr(logging, str(cfg.logging_level).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    try:
        asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
