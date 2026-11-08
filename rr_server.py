# -*- coding: utf-8 -*-
"""Retro Recoil - server entry point: App (shared state), lobbies/matchmaking, accept loop."""
import asyncio
import logging
import os
import random
import string
import sys
import time

from rr_config import Config
from rr_db import Db
from rr_world import WorldMap
from rr_match import Match, Bot
import rr_bots
import rr_intro
import rr_screens  # noqa: F401  (populates rr_session.STATE_TABLE on import)

log = logging.getLogger("retrorecoil")
VERSION = "1.0"

BOT_NAMES = ["Bot1", "Bot2", "Bot3", "Bot4", "Bot5"]


def gen_code(n=5):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion
    return "".join(random.choice(alphabet) for _ in range(n))


class Lobby:
    def __init__(self, lid, mode, cfg, host=None):
        self.id = lid
        self.mode = mode           # "bot" | "public" | "private"
        self.cfg = cfg
        self.host = host
        self.members = []          # Session list (humans only)
        self.code = gen_code() if mode == "private" else None
        self.match = None
        self.state = "waiting"     # waiting -> countdown -> playing -> done
        self.countdown_task = None
        self.fill_task = None
        self.created_at = time.monotonic()
        self.max_players = (int(cfg.game_private_max_players) if mode == "private"
                            else int(cfg.game_public_max_lobby) if mode == "public" else 1)

    def is_full(self):
        return len(self.members) >= self.max_players


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db = Db(cfg.paths_database)
        self.world = WorldMap(cfg.paths_map_obj)
        self.sessions = set()
        self.lobbies = {}
        self._next_lobby_id = 1
        self.accent = rr_accent()
        self.have_intro = rr_intro.ensure_bins(cfg, log=log.info)
        self._fails = {}           # ("ip", addr) / ("user", name) -> [count, locked_until]

    # ---- login throttling (per IP and per account)
    def login_locked(self, ip, username):
        now = time.monotonic()
        left = 0.0
        for key in (("ip", str(ip)), ("user", username.lower())):
            rec = self._fails.get(key)
            if rec and rec[1] > now:
                left = max(left, rec[1] - now)
        return left

    def login_failed(self, ip, username):
        now = time.monotonic()
        limit = int(self.cfg.security_login_max_attempts)
        for key in (("ip", str(ip)), ("user", username.lower())):
            rec = self._fails.setdefault(key, [0, 0.0])
            if rec[1] and rec[1] <= now:
                rec[0], rec[1] = 0, 0.0
            rec[0] += 1
            if rec[0] >= limit:
                rec[1] = now + float(self.cfg.security_login_lockout_seconds)
                rec[0] = 0

    def login_ok(self, ip, username):
        self._fails.pop(("user", username.lower()), None)

    # ---------------------------------------------------------------- connections
    def ip_count(self, ip):
        return sum(1 for s in self.sessions if s.ip == ip)

    async def handle(self, reader, writer):
        ip = writer.get_extra_info("peername")
        ip = ip[0] if ip else "?"
        try:                                  # no Nagle: tiny key echoes / frames must not wait for an ACK
            import socket
            sock = writer.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        if len(self.sessions) >= int(self.cfg.server_max_clients) or \
                self.ip_count(ip) >= int(self.cfg.server_max_clients_per_ip):
            writer.close()
            return
        from rr_session import Session
        sess = Session(self, reader, writer, ip)
        self.sessions.add(sess)
        log.info("[%s] connected (%d online)", ip, len(self.sessions))
        try:
            await sess.run()
        except ConnectionError:
            pass
        except Exception:
            log.exception("[%s] fatal session error", ip)
        finally:
            self.sessions.discard(sess)
            await sess.close()
            log.info("[%s] disconnected (%d online)", ip, len(self.sessions))

    # ---------------------------------------------------------------- bot practice
    def new_bot_lobby(self, session):
        lid = self._next_lobby_id
        self._next_lobby_id += 1
        lob = Lobby(lid, "bot", self.cfg, host=session)
        lob.members.append(session)
        self.lobbies[lid] = lob
        return lob

    # ---------------------------------------------------------------- public matchmaking
    def find_or_create_public_lobby(self, session):
        for lob in self.lobbies.values():
            if lob.mode == "public" and lob.state == "waiting" and not lob.is_full():
                lob.members.append(session)
                self._check_public_fill(lob)
                return lob
        lid = self._next_lobby_id
        self._next_lobby_id += 1
        lob = Lobby(lid, "public", self.cfg)
        lob.members.append(session)
        self.lobbies[lid] = lob
        self._check_public_fill(lob)
        return lob

    def _check_public_fill(self, lob):
        if lob.state != "waiting":
            return
        near_full = len(lob.members) >= max(1, lob.max_players - 2)
        if lob.is_full():
            if lob.fill_task:
                lob.fill_task.cancel()
                lob.fill_task = None
            self._start_countdown(lob, int(self.cfg.game_start_countdown_seconds))
        elif near_full and lob.fill_task is None and len(lob.members) >= int(self.cfg.game_public_min_lobby):
            lob.fill_task = asyncio.ensure_future(self._fill_timer(lob))

    async def _fill_timer(self, lob):
        try:
            await asyncio.sleep(float(self.cfg.game_fill_wait_seconds))
        except asyncio.CancelledError:
            return
        if lob.state == "waiting" and len(lob.members) >= int(self.cfg.game_public_min_lobby):
            self._start_countdown(lob, int(self.cfg.game_start_countdown_seconds))

    # ---------------------------------------------------------------- private team
    def create_private_lobby(self, session):
        lid = self._next_lobby_id
        self._next_lobby_id += 1
        lob = Lobby(lid, "private", self.cfg, host=session)
        lob.members.append(session)
        self.lobbies[lid] = lob
        return lob

    def join_private_lobby(self, session, code):
        code = (code or "").strip().upper()
        for lob in self.lobbies.values():
            if lob.mode == "private" and lob.code == code and lob.state == "waiting":
                if lob.is_full():
                    return None
                lob.members.append(session)
                return lob
        return None

    def host_start_private(self, lob):
        self._start_countdown(lob, int(self.cfg.game_start_countdown_seconds))

    # ---------------------------------------------------------------- shared
    def leave_lobby(self, session, lob):
        if lob.id not in self.lobbies:
            return
        if session in lob.members:
            lob.members.remove(session)
        session.lobby = None
        if lob.mode == "private" and session is lob.host and lob.state != "playing":
            for m in list(lob.members):
                m.lobby = None
                try:
                    m.emit_key("__lobby_closed__")
                except Exception:
                    pass
            lob.members.clear()
            lob.state = "done"
            self.lobbies.pop(lob.id, None)
            return
        if not lob.members and lob.state in ("waiting",):
            if lob.fill_task:
                lob.fill_task.cancel()
            self.lobbies.pop(lob.id, None)

    def _start_countdown(self, lob, seconds):
        lob.state = "countdown"
        lob.countdown_started = time.monotonic()
        lob.countdown_seconds = seconds
        if lob.fill_task:
            lob.fill_task.cancel()
            lob.fill_task = None
        lob.countdown_task = asyncio.ensure_future(self._run_countdown(lob, seconds))

    async def _run_countdown(self, lob, seconds):
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        self.begin_match(lob)

    def begin_match(self, lob):
        if lob.state == "playing":
            return
        lob.state = "playing"
        match = Match(self.world, self.cfg, lob)
        for sess in lob.members:
            from rr_match import Player
            c = Player(sess)
            match.add(c)
            sess.combatant = c
        if lob.mode == "bot":
            for name in BOT_NAMES[:int(self.cfg.game_bot_count)]:
                b = Bot(name)
                match.add(b)
        match.start()
        lob.match = match
        asyncio.ensure_future(self._match_loop(lob, match))

    async def _match_loop(self, lob, match):
        """The one authoritative tick loop for this match: bots think, everyone moves,
        shots/reloads already applied directly by session input handlers and bot AI.
        Every human session's render loop just reads this state at its own frame rate."""
        hz = max(5, int(self.cfg.game_tickrate))
        dt = 1.0 / hz
        last = time.monotonic()
        while not match.is_over():
            if not match.humans():                  # everybody left: nothing to simulate
                match.force_over = True
                break
            now = time.monotonic()
            real_dt = min(0.1, max(0.001, now - last))
            last = now
            for c in match.players:
                if c.is_bot:
                    rr_bots.bot_think(c, match, now, real_dt)
            match.tick(real_dt)
            await asyncio.sleep(max(0.0, dt - (time.monotonic() - now)))
        match.finalize()
        for sess in lob.members:
            try:
                sess.emit_key("__match_over__")
            except Exception:
                pass


def rr_accent():
    return (255, 59, 59)


def setup_logging(cfg):
    handlers = [logging.StreamHandler()]
    if cfg.logging_file:
        handlers.append(logging.FileHandler(str(cfg.logging_file), encoding="utf-8"))
    logging.basicConfig(level=getattr(logging, str(cfg.logging_level).upper(), logging.INFO),
                         handlers=handlers, format="%(asctime)s %(levelname)s %(message)s")


async def serve(cfg):
    app = App(cfg)
    server = await asyncio.start_server(app.handle, cfg.server_host, cfg.server_port)
    log.info("Retro Recoil %s listening on %s:%s", VERSION, cfg.server_host, cfg.server_port)
    print(f"Retro Recoil {VERSION} listening on telnet://{cfg.server_host}:{cfg.server_port} (Ctrl+C to stop)")
    async with server:
        await server.serve_forever()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Retro Recoil - telnet 3D shooter")
    ap.add_argument("-c", "--config", default="rr_config.ini")
    args = ap.parse_args()
    cfg = Config(args.config)
    setup_logging(cfg)
    if not os.path.isfile(cfg.paths_map_obj):
        sys.exit(f"map not found: {cfg.paths_map_obj} (run make_map.py first)")
    try:
        asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
