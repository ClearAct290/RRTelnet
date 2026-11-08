# -*- coding: utf-8 -*-
"""
Retro Recoil - per-connection session: telnet IO, key parsing, screens.

The telnet negotiation / key-parsing shape (IAC handling, NAWS, PETSCII
cursor-key bytes, the lone-ESC-vs-arrow-sequence timeout trick) follows the
same general approach as the user's TelTube project, adapted for a
real-time shooter: game keys need to behave as "held" for a short window
after each repeat rather than as one-shot menu presses.
"""
import asyncio
import re
import time
from collections import deque

from rr_render import Grid, Presenter, WHITE, BLACK, YELLOW, RED, LRED, GREEN, CYAN, DGREY, MGREY, LGREY, ORANGE
import rr_render as rr

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
OPT_BINARY, OPT_ECHO, OPT_SGA, OPT_TTYPE, OPT_NAWS = 0, 1, 3, 24, 31

PET_DOWN, PET_UP, PET_RIGHT, PET_LEFT, PET_DEL = 0x11, 0x91, 0x1D, 0x9D, 0x14
PET_CRSR = {PET_DOWN: "DOWN", PET_UP: "UP", PET_RIGHT: "RIGHT", PET_LEFT: "LEFT", PET_DEL: "BACKSPACE"}


class Nav(Exception):
    """Raised to jump to a different screen/state, like TelTube's Nav."""
    def __init__(self, state):
        self.state = state


class Session:
    def __init__(self, app, reader, writer, ip):
        self.app = app
        self.cfg = app.cfg
        self.reader, self.writer, self.ip = reader, writer, ip
        self.cols = int(app.cfg.server_default_cols)
        self.rows = int(app.cfg.server_default_rows)
        self.term_mode = "ansi"
        self.petscii = False
        self.skipped = 0
        self.chosen = False
        self.color_mode = "truecolor"
        self.presenter = Presenter(False, "truecolor", rate=app.cfg.video_rate_limit_bytes,
                                    gap=app.cfg.video_delta_gap_merge, full_refresh=app.cfg.video_full_refresh_seconds)
        self.keys = deque()
        self.key_ev = asyncio.Event()
        self.closed = False
        self.inbuf = b""
        self.after_cr = False
        self.refused = set()
        self.username = None
        self.rtask = None
        self.lobby = None
        self.combatant = None

    # ---------------------------------------------------------- low level io
    def write_raw(self, b):
        try:
            self.writer.write(bytes(b))
        except Exception:
            self.closed = True

    async def flush(self):
        try:
            await asyncio.wait_for(self.writer.drain(), 30)
        except (asyncio.TimeoutError, Exception):
            self.closed = True

    def new_grid(self):
        return Grid(self.rows, self.cols, WHITE, BLACK)

    async def present(self, g, force=False):
        if self.closed:
            return
        if not force:
            # Frame skip: if the client (a C64 on a slow link, say) has not yet taken what we
            # already queued, sending more only adds lag.  Skip this frame; the next one is a
            # delta against what the client really has, so nothing gets out of sync.
            try:
                pending = self.writer.transport.get_write_buffer_size()
            except Exception:
                pending = 0
            limit = int((self.cfg.video_commodore_rate_limit_bytes if self.petscii
                         else self.cfg.video_rate_limit_bytes) * 0.25)
            if pending > limit and self.skipped < 4:
                self.skipped += 1
                return
        self.skipped = 0
        data = self.presenter.encode(g, force=force)
        if data:
            self.write_raw(data)
            await self.flush()

    def set_petscii(self):
        self.term_mode = "petscii"
        self.petscii = True
        self.cols = int(self.cfg.server_commodore_cols)
        self.rows = int(self.cfg.server_commodore_rows)
        self.presenter = Presenter(True, "16", rate=self.cfg.video_rate_limit_bytes,
                                    gap=self.cfg.video_delta_gap_merge, full_refresh=self.cfg.video_full_refresh_seconds)

    def set_ansi(self):
        self.term_mode = "ansi"
        self.petscii = False
        self.presenter = Presenter(False, "truecolor", rate=self.cfg.video_rate_limit_bytes,
                                    gap=self.cfg.video_delta_gap_merge, full_refresh=self.cfg.video_full_refresh_seconds)

    def clear_raw(self):
        if self.petscii:
            self.write_raw(bytes([0x93]))
        else:
            self.write_raw(b"\x1b[0m\x1b[2J\x1b[H")

    # ---------------------------------------------------------- telnet / keys
    def on_negotiate(self, cmd, opt):
        offered = (OPT_BINARY, OPT_ECHO, OPT_SGA, OPT_TTYPE, OPT_NAWS)
        if opt in offered:
            return
        if opt in self.refused:
            return
        self.refused.add(opt)
        if cmd == DO:
            self.write_raw([IAC, WONT, opt])
        elif cmd == WILL:
            self.write_raw([IAC, DONT, opt])

    def on_sb(self, d):
        if d and d[0] == OPT_NAWS and len(d) >= 5 and self.term_mode != "petscii":
            w = (d[1] << 8) | d[2]
            h = (d[3] << 8) | d[4]
            if w > 0 and h > 0:
                self.cols, self.rows = max(40, min(w - 1, 240)), max(14, min(h - 1, 100))   # spare last column/row: some clients scroll or reserve them
                self.presenter.reset()

    def emit_key(self, k):
        if len(self.keys) < 128:
            self.keys.append((k, time.monotonic()))
            self.key_ev.set()

    def parse(self, data):
        buf = self.inbuf + data
        i, n = 0, len(buf)
        while i < n:
            b = buf[i]
            if b == IAC:
                if i + 1 >= n:
                    break
                c = buf[i + 1]
                if c in (WILL, WONT, DO, DONT):
                    if i + 2 >= n:
                        break
                    self.on_negotiate(c, buf[i + 2])
                    i += 3
                elif c == SB:
                    j = buf.find(bytes([IAC, SE]), i + 2)
                    if j < 0:
                        break
                    self.on_sb(buf[i + 2:j])
                    i = j + 2
                else:
                    i += 2
                continue
            if self.term_mode == "petscii":
                if b in PET_CRSR:
                    self.emit_key(PET_CRSR[b])
                    i += 1
                    self.after_cr = False
                    continue
                if 0xC1 <= b <= 0xDA:
                    self.emit_key(chr(b - 0x80))
                    i += 1
                    self.after_cr = False
                    continue
            if b == 27:
                if i + 1 >= n:
                    break
                c = buf[i + 1]
                if c in (ord("["), ord("O")):
                    j = i + 2
                    while j < n and not (0x40 <= buf[j] <= 0x7E):
                        j += 1
                    if j >= n:
                        break
                    fin = chr(buf[j])
                    k = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}.get(fin)
                    if k:
                        self.emit_key(k)
                    i = j + 1
                else:
                    self.emit_key("ESC")
                    i += 1
                self.after_cr = False
                continue
            i += 1
            if b in (13, 10):
                if b == 10 and self.after_cr:
                    self.after_cr = False
                    continue
                self.after_cr = (b == 13)
                self.emit_key("ENTER")
                continue
            if b == 0 and self.after_cr:
                self.after_cr = False
                continue
            self.after_cr = False
            if b in (8, 127):
                self.emit_key("BACKSPACE")
            elif b == 9:
                self.emit_key("TAB")
            elif b == 32:
                self.emit_key("SPACE")
            elif 32 < b < 127:
                self.emit_key(chr(b))
        self.inbuf = buf[i:]

    async def _read_loop(self):
        try:
            while True:
                lone_esc = self.inbuf == b"\x1b"
                try:
                    data = await asyncio.wait_for(self.reader.read(4096), 0.06 if lone_esc else None)
                except asyncio.TimeoutError:
                    self.inbuf = b""
                    self.emit_key("ESC")
                    continue
                if not data:
                    break
                self.parse(data)
        except (ConnectionError, OSError):
            pass
        finally:
            self.closed = True
            self.key_ev.set()

    def try_key(self):
        """Non-blocking: pop one (key, t_received) or None. Used by the game loop."""
        if self.keys:
            return self.keys.popleft()
        return None

    async def get_key(self, timeout=None):
        """Blocking (menu use): returns a bare key string, or None on timeout."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.keys:
                return self.keys.popleft()[0]
            if self.closed:
                raise ConnectionError("closed")
            rem = None if end is None else end - time.monotonic()
            if rem is not None and rem <= 0:
                return None
            self.key_ev.clear()
            try:
                await asyncio.wait_for(self.key_ev.wait(), 5 if rem is None else min(rem, 5))
            except asyncio.TimeoutError:
                pass

    async def drain_keys_nonblocking(self):
        out = []
        while self.keys:
            out.append(self.keys.popleft())
        return out

    # ---------------------------------------------------------- lifecycle
    async def run(self):
        self.write_raw([IAC, WILL, OPT_ECHO, IAC, WILL, OPT_SGA, IAC, WILL, OPT_BINARY, IAC, DO, OPT_BINARY,
                         IAC, DO, OPT_NAWS, IAC, DO, OPT_TTYPE])
        await self.flush()
        self.rtask = asyncio.ensure_future(self._read_loop())
        await asyncio.sleep(0.2)
        state = ("choose_terminal",)
        while True:
            try:
                fn = STATE_TABLE[state[0]]
                state = await fn(self, *state[1:])
            except Nav as nv:
                state = nv.state
            except ConnectionError:
                raise
            except Exception:
                import logging
                logging.getLogger("retrorecoil").exception("[%s] session error", self.ip)
                state = ("choose_terminal",) if not self.chosen else ("main_menu",)

    async def close(self):
        if self.lobby:
            try:
                self.app.leave_lobby(self, self.lobby)
            except Exception:
                pass
        if self.rtask:
            self.rtask.cancel()
        try:
            self.clear_raw()
            await asyncio.wait_for(self.writer.drain(), 2)
        except Exception:
            pass
        try:
            self.writer.close()
        except Exception:
            pass


# STATE_TABLE is filled in by rr_screens (avoids a circular import at module load)
STATE_TABLE = {}
