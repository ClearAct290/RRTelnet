# -*- coding: utf-8 -*-
"""Retro Recoil - screens (telnet "pages"). Each page_* returns the next state tuple."""
import asyncio
import math
import os
import re
import time

import numpy as np

from rr_session import Nav, STATE_TABLE
from rr_render import (Grid, Scene3D, draw_gun, draw_crosshair, shorten,
                        WHITE, BLACK, RED, LRED, YELLOW, GREEN, CYAN, ORANGE, DGREY, MGREY, LGREY,
                        LGREEN, BLUE, PURPLE, BROWN)
from rr_db import USERNAME_RE
import rr_intro

NAME = "RETRO RECOIL"


# ==================================================================== helpers
def center_x(cols, w):
    return max(0, (cols - w) // 2)


def draw_logo(g, row=1):
    """Framed title banner; returns the first free row below it."""
    if g.cols >= 34 and g.rows >= 14:
        w = 30
        x = center_x(g.cols, w)
        g.text(row, x, "+" + "=" * (w - 2) + "+", YELLOW, BLACK)
        g.text(row + 1, x, "|" + " " * (w - 2) + "|", YELLOW, BLACK)
        g.text(row + 1, x + 7, "* RETRO RECOIL *", RED, BLACK)
        g.text(row + 2, x, "+" + "=" * (w - 2) + "+", YELLOW, BLACK)
        g.center(row + 3, "TELNET 3D SHOOTER", DGREY, BLACK)
        return row + 4
    g.center(row, NAME, RED, BLACK)
    g.center(row + 1, "-" * len(NAME), DGREY, BLACK)
    return row + 2


def panel(g, r, c, h, w, title="", fg=DGREY, tfg=YELLOW):
    g.box(r, c, h, w, fg, BLACK)
    if title and w > len(title) + 6:
        g.text(r, c + 2, f" {title} ", tfg, BLACK)


def draw_header(g, title, back=True):
    """Top bar used by every sub-screen: BACK on the left, title centred, rule below."""
    g.fill(0, 0, 1, g.cols, bg=BLACK)
    if back:
        g.text(0, 1, "< BACK", YELLOW, BLACK)
    g.center(0, f"[ {title} ]", RED, BLACK)
    g.text(1, 1, "-" * max(0, g.cols - 2), DGREY, BLACK)


def draw_menu(g, r, c, w, items, sel, spacing=2):
    """Vertical menu; the selected entry is a solid highlighted bar with > < markers."""
    for i, label in enumerate(items):
        rr = r + i * spacing
        if i == sel:
            g.fill(rr, c, 1, w, bg=YELLOW, fg=BLACK)
            g.text(rr, c, ">", BLACK, YELLOW)
            g.text(rr, c + 2, label.upper(), BLACK, YELLOW)
            g.text(rr, c + w - 1, "<", BLACK, YELLOW)
        else:
            g.text(rr, c + 2, label.upper(), CYAN, BLACK)


def draw_field_box(g, r, c, w, label, value, focused, masked=False, color=WHITE):
    g.text(r, c, label, LGREY if not focused else YELLOW, BLACK)
    bx = c
    by = r + 1
    fg = YELLOW if focused else DGREY
    g.box(by, bx, 3, w, fg, BLACK)
    shown = ("*" * len(value)) if masked else value
    g.text(by + 1, bx + 2, shown[-(w - 4):], color, BLACK)
    if focused:
        g.put(by + 1, bx + 2 + min(len(shown), w - 4), "_", YELLOW, BLACK)


def draw_button(g, r, c, label, focused):
    fg = BLACK if focused else YELLOW
    bg = YELLOW if focused else BLACK
    g.text(r, c, f"[{label}]", fg, bg)


def draw_link(g, r, c, label, focused):
    g.text(r, c, label, BLACK if focused else CYAN, YELLOW if focused else BLACK)


async def get_key_or_idle(session, timeout=None):
    try:
        k = await session.get_key(timeout)
    except ConnectionError:
        raise
    return k


async def confirm_overlay(session, question, seconds=5.0):
    """5-second yes/no overlay, default No. Returns True (leave) / False (stay)."""
    sel = 1  # 0=yes 1=no (no highlighted by default)
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        g = session.new_grid()
        w = max(len(question) + 4, 24)
        r0 = session.rows // 2 - 2
        c0 = center_x(session.cols, w)
        g.box(r0, c0, 5, w, YELLOW, BLACK)
        g.center(r0 + 1, question, WHITE, BLACK, c0 + 1, w - 2)
        yes = "[ YES ]" if sel != 0 else "[>YES<]"
        no = "[ NO ]" if sel != 1 else "[>NO<]"
        g.text(r0 + 3, c0 + w // 2 - len(yes) - 1, yes, GREEN if sel == 0 else LGREY, BLACK)
        g.text(r0 + 3, c0 + w // 2 + 1, no, RED if sel == 1 else LGREY, BLACK)
        await session.present(g)
        k = await get_key_or_idle(session, min(0.5, max(0.05, remaining)))
        if k in ("LEFT", "RIGHT", "UP", "DOWN", "TAB"):
            sel = 1 - sel
        elif k == "ENTER":
            return sel == 0
        elif k == "ESC":
            return False


# ==================================================================== terminal choice
async def page_choose_terminal(session):
    """The very first screen.  Plain 7-bit text + CR/LF only: no escape codes, no colour, no
    cursor movement, no clear-screen, so every client (SyncTERM in any mode, a C64 terminal
    program, raw telnet) shows it correctly BEFORE we know what the client can do.  The
    highlight is a '>' marker; a change just prints the menu again below."""
    session.chosen = True
    sel = 0
    first = True

    def draw():
        lines = ["", NAME, "",
                 ("> " if sel == 0 else "  ") + "1 = Generic ASCII (flexible size)",
                 ("> " if sel == 1 else "  ") + "2 = C64 PETSCII (40x25)",
                 "", "Press 1 or 2, or arrows + ENTER", ""]
        session.write_raw(("\r\n".join(lines) + "\r\n").encode("ascii", "replace"))

    while True:
        draw()
        await session.flush()
        first = False
        while True:
            k = await session.get_key()
            if k in ("1", "2"):
                sel = int(k) - 1
                break
            if k in ("UP", "DOWN", "LEFT", "RIGHT", "TAB"):
                sel = 1 - sel
                break
            if k == "ENTER":
                break
        if k in ("1", "2", "ENTER"):
            if sel == 1:
                session.set_petscii()
            else:
                session.set_ansi()
            session.clear_raw()                 # only now is it safe to send control codes
            return ("login", "")


# ==================================================================== login / create account
async def page_login(session, prefill_user=""):
    db = session.app.db
    username, password = prefill_user, ""
    show_pw = False
    focus = 0
    error = ""
    N_FIELDS = 5  # username, password, showpw, login btn, create link
    while True:
        g = session.new_grid()
        r = draw_logo(g, 0) + 1
        w = min(session.cols - 4, 40)
        c0 = center_x(session.cols, w)
        draw_field_box(g, r, c0, w, "USERNAME", username, focus == 0)
        draw_field_box(g, r + 4, c0, w, "PASSWORD", password, focus == 1, masked=not show_pw)
        box = "[X]" if show_pw else "[ ]"
        g.text(r + 8, c0, box + " show password", YELLOW if focus == 2 else LGREY, BLACK)
        draw_button(g, r + 10, c0, "LOGIN", focus == 3)
        draw_link(g, r + 12, c0, "Don't? Create One", focus == 4)
        if error:
            g.center(r + 14, error, RED, BLACK)
        g.center(session.rows - 1, "ARROWS/TAB move  ENTER select  ESC quit", DGREY, BLACK)
        await session.present(g)
        k = await session.get_key()
        if k is None:
            continue
        if k in ("UP",):
            focus = (focus - 1) % N_FIELDS
        elif k in ("DOWN", "TAB"):
            focus = (focus + 1) % N_FIELDS
        elif k == "BACKSPACE":
            if focus == 0:
                username = username[:-1]
            elif focus == 1:
                password = password[:-1]
        elif k == "SPACE" and focus == 2:
            show_pw = not show_pw
        elif k == "ESC":
            raise ConnectionError("quit at login")
        elif k == "ENTER":
            if focus == 2:
                show_pw = not show_pw
            elif focus == 4:
                return ("create_account", username)
            elif focus == 3 or focus in (0, 1):
                if focus in (0, 1):
                    focus += 1
                    continue
                if not username:
                    error = "Enter a username"
                else:
                    locked = session.app.login_locked(session.ip, username)
                    if locked:
                        error = f"Too many tries - wait {int(locked) + 1}s"
                    else:
                        cands = [password]
                        if session.petscii:        # a C64 types letters in the other case
                            cands += [x for x in (password.swapcase(), password.lower(), password.upper())
                                      if x not in cands]
                        uname = await asyncio.to_thread(db.authenticate, username, cands)
                        if uname is None:
                            session.app.login_failed(session.ip, username)
                            error = "Wrong username or password"
                        else:
                            session.app.login_ok(session.ip, username)
                            session.username = uname
                            await maybe_play_intro(session)
                            return ("main_menu",)
        elif len(k) == 1 and k.isprintable():
            if focus == 0 and len(username) < 12:
                username += k
            elif focus == 1 and len(password) < 32:
                password += k


async def page_create_account(session, prefill_user=""):
    db = session.app.db
    username, password, confirm = prefill_user, "", ""
    show_pw = False
    focus = 0
    error = ""
    N_FIELDS = 7  # username, random-btn, password, confirm, showpw, create btn, login link
    while True:
        g = session.new_grid()
        r = draw_logo(g, 0)
        w = min(session.cols - 4, 40)
        c0 = center_x(session.cols, w)
        draw_field_box(g, r, c0, w - 10, "USERNAME", username, focus == 0)
        draw_button(g, r + 1, c0 + w - 9, "RND", focus == 1)
        draw_field_box(g, r + 4, c0, w, "PASSWORD (8+ chars)", password, focus == 2, masked=not show_pw)
        draw_field_box(g, r + 8, c0, w, "CONFIRM PASSWORD", confirm, focus == 3, masked=not show_pw)
        box = "[X]" if show_pw else "[ ]"
        g.text(r + 12, c0, box + " show password", YELLOW if focus == 4 else LGREY, BLACK)
        draw_button(g, r + 13, c0, "CREATE", focus == 5)
        draw_link(g, r + 14, c0, "Already? Login", focus == 6)
        if error:
            g.center(r + 16, error, RED, BLACK)
        g.center(session.rows - 1, "ARROWS/TAB move  ENTER select  ESC quit", DGREY, BLACK)
        await session.present(g)
        k = await session.get_key()
        if k is None:
            continue
        if k in ("UP",):
            focus = (focus - 1) % N_FIELDS
        elif k in ("DOWN", "TAB"):
            focus = (focus + 1) % N_FIELDS
        elif k == "BACKSPACE":
            if focus == 0:
                username = username[:-1]
            elif focus == 2:
                password = password[:-1]
            elif focus == 3:
                confirm = confirm[:-1]
        elif k == "SPACE" and focus == 4:
            show_pw = not show_pw
        elif k == "ESC":
            raise ConnectionError("quit at create account")
        elif k == "ENTER":
            if focus == 1:
                username = db.unique_random_username()
            elif focus == 4:
                show_pw = not show_pw
            elif focus == 6:
                return ("login", username)
            elif focus == 5 or focus in (0, 2, 3):
                if focus in (0, 2, 3):
                    focus += 1
                    continue
                if password != confirm:
                    error = "Passwords don't match"
                else:
                    ok, msg = await asyncio.to_thread(db.create_account, username, password)
                    if not ok:
                        error = msg
                    else:
                        session.username = username
                        await maybe_play_intro(session)
                        return ("main_menu",)
        elif len(k) == 1 and k.isprintable():
            if focus == 0 and len(username) < 12:
                username += k
            elif focus == 2 and len(password) < 32:
                password += k
            elif focus == 3 and len(confirm) < 32:
                confirm += k


async def maybe_play_intro(session):
    if not session.app.have_intro or not session.cfg.intro_enabled:
        return
    path = session.cfg.paths_intro_petscii_bin if session.petscii else session.cfg.paths_intro_ascii_bin
    try:
        await rr_intro.play_intro(session, path, hold_seconds=float(session.cfg.intro_hold_seconds))
    except ConnectionError:
        raise
    except Exception:
        pass


# ==================================================================== main menu
MENU_ITEMS = ["Play", "My Scores", "Controls", "Disconnect"]
RANK_COLORS = [YELLOW, LGREY, ORANGE, WHITE, WHITE]


def draw_top5(g, r, c, w, top5, h=8):
    panel(g, r, c, h, w, "TOP 5 PLAYERS (KDR)", MGREY, YELLOW)
    for i in range(5):
        rr = r + 1 + i + (1 if h >= 9 else 0)
        if i < len(top5):
            uname, kills, deaths, kdr = top5[i]
            line = f"{i+1}. {shorten(uname, 5):<5} {kdr:5.2f}  {kills}K/{deaths}D"
            g.text(rr, c + 2, line[:w - 4], RANK_COLORS[i], BLACK)
        else:
            g.text(rr, c + 2, f"{i+1}. -----  ----", DGREY, BLACK)


async def page_main_menu(session):
    db = session.app.db
    sel = 0
    while True:
        top5 = db.leaderboard_top(5)
        g = session.new_grid()
        cols, rows = session.cols, session.rows
        nxt = draw_logo(g, 0)
        g.center(nxt, "GiHu:ClearAct290/RRTelnet", DGREY, BLACK)
        nxt += 1
        mh = 2 + len(MENU_ITEMS) * 2
        if cols >= 64 and rows >= nxt + mh + 2:
            total = 24 + 2 + 36
            c0 = center_x(cols, total)
            r0 = nxt + 1
            panel(g, r0, c0, mh + 1, 24, "MENU", MGREY, YELLOW)
            draw_menu(g, r0 + 1, c0 + 1, 22, MENU_ITEMS, sel, 2)
            draw_top5(g, r0, c0 + 26, 36, top5, mh + 1)
        else:
            w = min(cols - 2, 38)
            c0 = center_x(cols, w)
            draw_top5(g, nxt, c0, w, top5, 7)
            r0 = nxt + 7
            avail = rows - 2 - r0 - 2
            sp = 2 if avail >= 2 * len(MENU_ITEMS) else 1
            panel(g, r0, c0, 2 + len(MENU_ITEMS) * sp, w, "MENU", MGREY, YELLOW)
            draw_menu(g, r0 + 1, c0 + 1, w - 2, MENU_ITEMS, sel, sp)
        g.fill(rows - 1, 0, 1, cols, bg=BLACK)
        g.text(rows - 1, 1, f"PLAYER: {session.username}", CYAN, BLACK)
        hint = "UP/DOWN + ENTER"
        if cols >= 56:
            g.text(rows - 1, cols - len(hint) - 2, hint, DGREY, BLACK)
        await session.present(g)
        k = await session.get_key(1.0)           # refresh now and then: the leaderboard changes
        if k is None:
            continue
        if k == "UP":
            sel = (sel - 1) % len(MENU_ITEMS)
        elif k == "DOWN" or k == "TAB":
            sel = (sel + 1) % len(MENU_ITEMS)
        elif k in "1234" and len(k) == 1:
            sel = int(k) - 1
        elif k == "ESC":
            if await confirm_overlay(session, "Are you sure you want to leave?"):
                raise ConnectionError("left from main menu")
        elif k == "ENTER":
            if sel == 0:
                return ("play_menu",)
            if sel == 1:
                return ("my_scores",)
            if sel == 2:
                return ("controls",)
            if sel == 3:
                raise ConnectionError("disconnect requested")


# ==================================================================== my scores
async def page_my_scores(session):
    db = session.app.db
    while True:
        s = db.get_stats(session.username)
        rank, total, rows = db.leaderboard_rank(session.username)
        g = session.new_grid()
        draw_header(g, "MY SCORES")
        w = min(session.cols - 2, 46)
        c0 = center_x(session.cols, w)
        kdr = s["kills"] / max(1, s["deaths"])
        panel(g, 3, c0, 7, w, "TOTALS", MGREY, YELLOW)
        g.text(4, c0 + 2, f"KDR     {kdr:5.2f}", YELLOW, BLACK)
        g.text(5, c0 + 2, f"Kills   {s['kills']}", GREEN, BLACK)
        g.text(6, c0 + 2, f"Deaths  {s['deaths']}", RED, BLACK)
        g.text(7, c0 + 2, f"Reloads {s['reloads']}", WHITE, BLACK)
        g.text(8, c0 + 2, f"Games   {s['games']}", WHITE, BLACK)
        last5 = s["last5"] or []
        panel(g, 10, c0, 3, w, "DEATHS, LAST 5 GAMES", MGREY, YELLOW)
        g.text(11, c0 + 2, "  ".join(str(x) for x in last5) if last5 else "no games yet", WHITE if last5 else DGREY, BLACK)
        panel(g, 13, c0, 7, w, "LEADERBOARD", MGREY, YELLOW)
        if rank is None:
            g.text(14, c0 + 2, "play a match to get ranked", DGREY, BLACK)
        else:
            lo = max(0, rank - 3)
            hi = min(len(rows), rank + 2)
            for i in range(lo, hi):
                uname, kills, deaths, r_kdr = rows[i]
                place = i + 1
                is_me = (place == rank)
                name = "You" if is_me else shorten(uname, 6)
                line = f"#{place:<3} {name:<7} {r_kdr:5.2f}"
                g.text(14 + (i - lo), c0 + 2, ("> " if is_me else "  ") + line, YELLOW if is_me else LGREY, BLACK)
            g.text(19, c0 + w - 12, f"of {total}", DGREY, BLACK)
        await session.present(g)
        k = await session.get_key()
        if k in ("ESC", "ENTER"):
            return ("main_menu",)


# ==================================================================== controls
def _meta_block(name):
    """Lines of one <!-- NAME START --> ... <!-- NAME END --> block in imp-meta.md."""
    for base in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        try:
            with open(os.path.join(base, "imp-meta.md"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        m = re.search(rf"<!-- {name} START -->(.*?)<!-- {name} END -->", text, re.S)
        if m:
            return [ln.rstrip() for ln in m.group(1).strip().splitlines() if ln.strip()]
    return []


def _controls_pages(cols, rows):
    """Lay the three help blocks out into screens of lines (text, fg) that fit cols x rows."""
    import textwrap
    w = min(cols - 4, 60)
    body_rows = max(4, rows - 6)
    pages = []

    def paginate(title, items):
        cur, out = [], []
        for ln in items:
            if len(cur) + len(ln) > body_rows and cur:
                out.append(cur)
                cur = []
            cur.extend(ln)
        if cur:
            out.append(cur)
        for n, lines in enumerate(out):
            pages.append((title, lines))

    for title, block in (("CONTROLS", "CONTROLS"), ("HUD (THE SCREEN)", "HUD")):
        keyw = max([len(l.partition("|")[0].strip()) for l in _meta_block(block)] or [4]) + 2
        keyw = min(keyw, 14)
        items = []
        for line in _meta_block(block):
            k, _, v = line.partition("|")
            k, v = k.strip(), v.strip()
            wrapped = textwrap.wrap(v, max(10, w - keyw - 5)) or [""]
            ln = []
            for j, part in enumerate(wrapped):
                ln.append(((k if j == 0 else "").ljust(keyw) + " " + part, j == 0, keyw))
            items.append(ln)
        paginate(title, items)
    items = []
    for line in _meta_block("HOWTO"):
        bullet = line.startswith("- ")
        txt = line[2:] if bullet else line
        wrapped = textwrap.wrap(txt, max(10, w - 6)) or [""]
        items.append([(("* " if (bullet and j == 0) else "  ") + part, False, 0) for j, part in enumerate(wrapped)])
    paginate("HOW TO PLAY", items)
    return pages, w


async def page_controls(session):
    page = 0
    while True:
        pages, w = _controls_pages(session.cols, session.rows)
        if not pages:
            pages = [("CONTROLS", [("(no help text found)", False, 0)])]
        page %= len(pages)
        title, lines = pages[page]
        g = session.new_grid()
        draw_header(g, title)
        c0 = center_x(session.cols, w)
        panel(g, 3, c0, len(lines) + 2, w, f"{page + 1}/{len(pages)}", MGREY, YELLOW)
        for n, (text, is_first, keyw) in enumerate(lines):
            rr = 4 + n
            if rr >= session.rows - 2:
                break
            if keyw:
                g.text(rr, c0 + 2, text[:keyw], YELLOW if is_first else WHITE, BLACK)
                g.text(rr, c0 + 2 + keyw, text[keyw:], WHITE, BLACK)
            else:
                g.text(rr, c0 + 2, text, WHITE, BLACK)
        g.center(session.rows - 1, "LEFT/RIGHT page   ESC back", DGREY, BLACK)
        await session.present(g)
        k = await session.get_key()
        if k in ("ESC",):
            return ("main_menu",)
        if k in ("RIGHT", "DOWN", "ENTER", "TAB", "SPACE"):
            page += 1
        elif k in ("LEFT", "UP"):
            page -= 1


# ==================================================================== play menu
PLAY_INFO = {
    "Bot Practice": "Solo match against 5 bots",
    "Multiplayer": "Join a public match",
    "Private Team": "Host or join with a code",
    "Back": "Return to the main menu",
}


async def _menu_page(session, title, items, info, back_state):
    sel = 0
    while True:
        g = session.new_grid()
        draw_header(g, title)
        w = min(session.cols - 2, 34)
        c0 = center_x(session.cols, w)
        panel(g, 3, c0, 2 + len(items) * 2, w, "SELECT", MGREY, YELLOW)
        draw_menu(g, 4, c0 + 1, w - 2, items, sel, 2)
        g.center(3 + 2 + len(items) * 2 + 1, info.get(items[sel], ""), LGREY, BLACK)
        g.center(session.rows - 1, "UP/DOWN + ENTER   ESC back", DGREY, BLACK)
        await session.present(g)
        k = await session.get_key()
        if k == "UP":
            sel = (sel - 1) % len(items)
        elif k in ("DOWN", "TAB"):
            sel = (sel + 1) % len(items)
        elif k == "ESC":
            return back_state
        elif k == "ENTER":
            return sel


async def page_play_menu(session):
    items = ["Bot Practice", "Multiplayer", "Private Team", "Back"]
    sel = await _menu_page(session, "PLAY", items, PLAY_INFO, ("main_menu",))
    if isinstance(sel, tuple):
        return sel
    app = session.app
    if sel == 0:
        lob = app.new_bot_lobby(session)
        session.lobby = lob
        app._start_countdown(lob, int(session.cfg.game_start_countdown_seconds))
        return ("lobby_wait", lob)
    if sel == 1:
        lob = app.find_or_create_public_lobby(session)
        session.lobby = lob
        return ("lobby_wait", lob)
    if sel == 2:
        return ("private_menu",)
    return ("main_menu",)


async def page_private_menu(session):
    items = ["Host", "Join", "Back"]
    info = {"Host": "Get a code for up to 4 friends", "Join": "Enter a friend's code", "Back": "Return to Play"}
    sel = await _menu_page(session, "PRIVATE TEAM", items, info, ("play_menu",))
    if isinstance(sel, tuple):
        return sel
    app = session.app
    if sel == 0:
        lob = app.create_private_lobby(session)
        session.lobby = lob
        return ("private_host", lob)
    if sel == 1:
        return ("private_join",)
    return ("play_menu",)


async def page_private_join(session):
    code = ""
    error = ""
    while True:
        g = session.new_grid()
        draw_header(g, "JOIN PRIVATE TEAM")
        w = min(session.cols - 4, 26)
        c0 = center_x(session.cols, w)
        draw_field_box(g, 4, c0, w, "ENTER JOIN CODE", code, True)
        g.center(9, "Ask the host for their 5-letter code", DGREY, BLACK)
        if error:
            g.center(11, error, RED, BLACK)
        g.center(session.rows - 1, "ENTER join   ESC back", DGREY, BLACK)
        await session.present(g)
        k = await session.get_key()
        if k == "ESC":
            return ("private_menu",)
        if k == "BACKSPACE":
            code = code[:-1]
        elif k == "ENTER":
            lob = session.app.join_private_lobby(session, code)
            if lob is None:
                error = "Invalid or full code"
            else:
                session.lobby = lob
                return ("lobby_wait", lob)
        elif len(k) == 1 and k.isalnum() and len(code) < 8:
            code += k.upper()
            error = ""


# ==================================================================== lobby wait / roster
def draw_roster(g, r0, lob, c0=None, w=None, max_rows=None):
    """Framed player list with empty slots; returns the first free row below it."""
    c0 = center_x(g.cols, w) if c0 is None else c0
    w = min(g.cols - 2, 34) if w is None else w
    c0 = center_x(g.cols, w) if c0 is None else c0
    slots = lob.max_players if lob.mode != "bot" else 1
    if max_rows:
        slots = min(slots, max_rows)
    panel(g, r0, c0, slots + 2, w, f"PLAYERS {len(lob.members)}/{lob.max_players if lob.mode != 'bot' else 1}", MGREY, YELLOW)
    for i in range(slots):
        if i < len(lob.members):
            m = lob.members[i]
            tag = " (host)" if m is lob.host else ""
            g.text(r0 + 1 + i, c0 + 2, f"{i+1}. {m.username}{tag}", WHITE, BLACK)
        else:
            g.text(r0 + 1 + i, c0 + 2, f"{i+1}. - waiting -", DGREY, BLACK)
    return r0 + slots + 2


async def page_private_host(session, lob):
    while True:
        if lob.id not in session.app.lobbies:
            return ("main_menu",)
        g = session.new_grid()
        draw_header(g, "PRIVATE TEAM - HOST", back=False)
        w = min(session.cols - 2, 34)
        c0 = center_x(session.cols, w)
        panel(g, 3, c0, 4, w, "JOIN CODE", YELLOW, YELLOW)
        g.center(4, " ".join(lob.code), WHITE, BLACK)
        g.center(5, f"up to {lob.max_players - 1} friends can join", DGREY, BLACK)
        nxt = draw_roster(g, 7, lob, c0, w, max_rows=max(2, session.rows - 7 - 6))
        draw_button(g, min(session.rows - 3, nxt + 1), center_x(session.cols, 9), "START", True)
        g.center(session.rows - 1, "ESC leave (closes the lobby for everyone)", DGREY, BLACK)
        await session.present(g)
        k = await session.get_key(1.0)
        if k == "ESC":
            session.app.leave_lobby(session, lob)
            return ("main_menu",)
        if k == "ENTER":
            session.app.host_start_private(lob)
            return ("lobby_wait", lob)


async def page_lobby_wait(session, lob):
    while True:
        if lob.id not in session.app.lobbies and lob.state != "playing":
            return ("main_menu",)
        if lob.state == "playing" and lob.match is not None:
            session.combatant = next((c for c in lob.match.players if getattr(c, "session", None) is session), None)
            return ("game", lob)
        g = session.new_grid()
        title = {"bot": "BOT PRACTICE", "public": "MULTIPLAYER", "private": "PRIVATE TEAM"}.get(lob.mode, "LOBBY")
        draw_header(g, title, back=False)
        w = min(session.cols - 2, 34)
        c0 = center_x(session.cols, w)
        nxt = draw_roster(g, 3, lob, c0, w, max_rows=max(2, session.rows - 3 - 7))
        if lob.mode == "bot":
            g.center(nxt, f"+ {int(session.cfg.game_bot_count)} bots", LGREY, BLACK)
            nxt += 1
        r1 = nxt + 1
        if lob.state == "countdown":
            left = max(0.0, lob.countdown_seconds - (time.monotonic() - lob.countdown_started))
            frac = 1.0 - left / max(0.1, float(lob.countdown_seconds))
            barw = max(8, w - 8)
            filled = int(barw * min(1.0, max(0.0, frac)))
            g.center(r1, f"Starting in {left:0.0f}s", YELLOW, BLACK)
            g.center(r1 + 1, "[" + "#" * filled + "-" * (barw - filled) + "]", GREEN, BLACK)
        elif lob.mode == "public":
            g.center(r1, "Waiting for more players...", LGREY, BLACK)
        else:
            g.center(r1, "Waiting for the host to start...", LGREY, BLACK)
        g.center(session.rows - 1, "ESC leave", DGREY, BLACK)
        await session.present(g)
        k = await session.get_key(0.4)
        if k == "ESC":
            session.app.leave_lobby(session, lob)
            return ("main_menu",)
        if k == "__lobby_closed__":
            return ("main_menu",)


# ==================================================================== in-game
def _ammo_wait_left(match, c, now):
    if c.ammo > 0:
        return 0.0
    if c.reloading:
        return max(0.0, c.reload_until - now)
    cd_left = match.reload_cd - (now - c.last_ammo_loss)
    if cd_left > 0:
        return cd_left + match.reload_time
    return 0.0


def draw_hud_game(g, scene, world, match, c, now):
    cols, rows = g.cols, g.rows
    vx, vy, vw, vh = 0, 1, cols, max(3, rows - 2)
    others = [o for o in match.players if o is not c and o.alive]
    cam = (c.x, c.z, c.yaw, c.eye)
    mc, mr = int(match.cfg.video_render_max_cols), int(match.cfg.video_render_max_rows)
    if vw > mc or vh > mr:
        sw, sh = min(vw, mc), min(vh, mr)
        sub = Grid(sh, sw)
        scene.render(sub, 0, 0, sw, sh, cam, others, now)
        ri = (np.arange(vh) * sh // vh)
        ci = (np.arange(vw) * sw // vw)
        g.ch[vy:vy + vh, vx:vx + vw] = sub.ch[np.ix_(ri, ci)]
        g.fg[vy:vy + vh, vx:vx + vw] = sub.fg[np.ix_(ri, ci)]
        g.bg[vy:vy + vh, vx:vx + vw] = sub.bg[np.ix_(ri, ci)]
    else:
        scene.render(g, vx, vy, vw, vh, cam, others, now)
    draw_crosshair(g, vx, vy, vw, vh)
    draw_gun(g, vx, vy, vw, vh, firing=(now < c.flash_until))
    g.fill(0, 0, 1, cols, bg=BLACK)
    g.text(0, 1, f"K-{c.kills}", GREEN, BLACK)
    g.text(0, 9, f"D-{c.deaths}", RED, BLACK)
    g.text(0, 17, f"A-{c.ammo}", YELLOW, BLACK)
    wait = _ammo_wait_left(match, c, now)
    if wait > 0:
        g.text(1, 1, f"{wait:0.1f}", ORANGE, BLACK)
    for i, (ft, killer, victim) in enumerate(list(match.feed)):
        if now - ft < 4.0 and cols >= 36:
            g.text(1 + i, cols - 1 - len(f"{shorten(killer, 5)}>{shorten(victim, 5)}"),
                   f"{shorten(killer, 5)}>{shorten(victim, 5)}", ORANGE, BLACK)
    fov_half = math.atan(scene.plane) + 0.05
    used = set()
    for bearing, dist in sorted(match.threats(c, now, fov_half), key=lambda t: t[1])[:6]:
        # straight behind = centre; the mark slides toward the edge on that side as the enemy
        # comes round, and disappears once it is in view
        frac = (math.pi - abs(bearing)) / max(0.1, math.pi - fov_half)      # 0 = behind, 1 = at the edge
        half = max(1, cols // 2 - 1)
        x = cols // 2 + int(round((1 if bearing > 0 else -1) * frac * half))
        while x in used and x < cols - 1:
            x += 1
        used.add(x)
        x = max(0, min(cols - 1, x))
        flash = int(now * 5) % 2 == 0
        fg, bg = (WHITE, RED) if flash else (RED, WHITE)
        g.put(rows - 2, x, "!", fg, bg)
        if dist < 10.0 and rows >= 8:
            g.put(rows - 3, x, "!", fg, bg)
    left = match.time_left()
    mm, ss = int(left // 60), int(left % 60)
    tcol = RED if left <= 15 else WHITE
    g.text(rows - 1, 1, f"{mm}:{ss:02d}", tcol, BLACK)


def draw_you_died(g, match, c, now):
    g.fill(0, 0, g.rows, g.cols, bg=BLACK)
    g.center(g.rows // 2 - 2, "YOU DIED", RED, BLACK)
    remain = max(0.0, c.respawn_at - now)
    g.center(g.rows // 2, f"respawning in {remain:0.0f}s", LGREY, BLACK)
    g.center(g.rows // 2 + 2, "ESC to leave", DGREY, BLACK)


def _leave_match(session, lob, match, c):
    """Quit mid-match: the player's stats so far still count."""
    if not c.recorded:
        c.recorded = True
        try:
            session.app.db.record_match(session.username, c.kills, c.deaths, c.reloads)
        except Exception:
            pass
    match.remove(c)
    session.app.leave_lobby(session, lob)


async def page_game(session, lob):
    match = lob.match
    c = session.combatant
    if c is None or match is None:
        return ("main_menu",)
    world = session.app.world
    scene = Scene3D(world)
    fps = session.cfg.video_commodore_fps if session.petscii else session.cfg.video_fps
    frame_dt = 1.0 / max(1, int(fps))
    next_frame = time.monotonic()
    while True:
        now = time.monotonic()
        if match.is_over():
            return ("match_end", lob)
        g = session.new_grid()
        if c.alive:
            match.apply_move(c, now)             # exact position for THIS frame, not the last tick
            draw_hud_game(g, scene, world, match, c, now)
        else:
            draw_you_died(g, match, c, now)
        await session.present(g)
        # Wait for the next frame, but act on every key the moment it arrives (a shot or a
        # turn is applied immediately, not at the next frame boundary).
        last_render = time.monotonic()
        next_frame = last_render + frame_dt
        min_gap = 0.12 if session.petscii else 0.035
        dirty = False
        while True:
            session.key_ev.clear()
            for k, t in await session.drain_keys_nonblocking():
                if k == "ESC":
                    if not c.alive:
                        _leave_match(session, lob, match, c)
                        return ("main_menu",)
                    leave = await confirm_overlay(session, "Are you sure you want to leave?")
                    if leave:
                        _leave_match(session, lob, match, c)
                        return ("main_menu",)
                    next_frame = time.monotonic()
                    continue
                if c.alive:
                    match.handle_key(c, k, t)
                    dirty = True
            if session.closed:
                raise ConnectionError("closed")
            if dirty and time.monotonic() - last_render >= min_gap:
                break                            # a key just changed things: show it now, don't wait
            rem = next_frame - time.monotonic()
            if rem <= 0 or match.is_over():
                break
            try:
                await asyncio.wait_for(session.key_ev.wait(), rem)
            except asyncio.TimeoutError:
                break


# ==================================================================== match end
async def page_match_end(session, lob):
    match = lob.match
    c = session.combatant
    if c is not None and not c.recorded:
        c.recorded = True
        session.app.db.record_match(session.username, c.kills, c.deaths, c.reloads)
    rows = match.results or match.finalize()
    while True:
        g = session.new_grid()
        g.center(1, "MATCH RESULTS", RED, BLACK)
        for i, (name, kills, deaths, kdr, _human) in enumerate(rows[:10]):
            g.text(3 + i, 2, f"{i+1}. {shorten(name,10):<10} K{kills} D{deaths} {kdr:4.2f}", YELLOW if (i == 0) else WHITE, BLACK)
        g.center(session.rows - 1, "ESC for main menu", DGREY, BLACK)
        await session.present(g)
        k = await session.get_key()
        if k == "ESC" or k == "ENTER":
            session.lobby = None
            return ("main_menu",)


STATE_TABLE.update({
    "choose_terminal": page_choose_terminal,
    "login": page_login,
    "create_account": page_create_account,
    "main_menu": page_main_menu,
    "my_scores": page_my_scores,
    "controls": page_controls,
    "play_menu": page_play_menu,
    "private_menu": page_private_menu,
    "private_join": page_private_join,
    "private_host": page_private_host,
    "lobby_wait": page_lobby_wait,
    "game": page_game,
    "match_end": page_match_end,
})
