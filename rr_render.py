# -*- coding: utf-8 -*-
"""
Retro Recoil - terminal rendering.

* Grid        rows x cols of (char, fg, bg) where fg/bg are C64 palette indices 0-15
* Presenter   turns a Grid into bytes for an ANSI terminal or a PETSCII (Commodore)
              terminal, sending only the cells that really changed since the last
              frame that was actually sent (exact compare, no thresholds - so nothing
              can get "stuck" half faded).  PETSCII deltas use cursor-move codes.
* Scene3D     server-side headless raycaster for the .obj map + stick-figure players

Portions of the PETSCII/colour tables follow the TelTube project (GPLv2+).
"""
import math
import time

import numpy as np

from rr_world import (EYE_CROUCH, EYE_STAND, H_WALL, HGT_CROUCH, HGT_STAND, LOWCEIL_H)

# ---------------------------------------------------------------- palette
(BLACK, WHITE, RED, CYAN, PURPLE, GREEN, BLUE, YELLOW, ORANGE, BROWN, LRED, DGREY, MGREY,
 LGREEN, LBLUE, LGREY) = range(16)

PAL_C64 = np.array([
    (0, 0, 0), (255, 255, 255), (136, 0, 0), (170, 255, 238), (204, 68, 204),
    (0, 204, 85), (0, 0, 170), (238, 238, 119), (221, 136, 85), (102, 68, 0),
    (255, 119, 119), (51, 51, 51), (119, 119, 119), (170, 255, 102), (0, 136, 255),
    (187, 187, 187)], np.int32)

PETSCII_COLOR = [144, 5, 28, 159, 156, 30, 31, 158, 129, 149, 150, 151, 152, 153, 154, 155]
PET_CLEAR, PET_HOME = 0x93, 0x13
PET_REV_ON, PET_REV_OFF = 0x12, 0x92
PET_DOWN, PET_UP, PET_RIGHT, PET_LEFT = 0x11, 0x91, 0x1D, 0x9D
PET_DEL = 0x14

# ASCII chars that are a different glyph in unshifted PETSCII
_PET_SUBST = {ord("\\"): ord("/"), ord("^"): ord("@"), ord("_"): ord("-"), ord("|"): ord(":"),
              ord("{"): ord("("), ord("}"): ord(")"), ord("~"): ord("-"), ord("`"): ord("'"),
              ord('"'): ord("'")}


def _pet_char(o):
    if o == 0:
        return 32
    if 97 <= o <= 122:
        o -= 32
    o = _PET_SUBST.get(o, o)
    return o if 32 <= o <= 93 else 63


PET_LUT = np.array([_pet_char(o) for o in range(256)], np.uint8)


def to_petscii_bytes(s):
    return bytes(PET_LUT[ord(c) if ord(c) < 256 else 63] for c in s)


_XTERM16 = np.array([(0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205), (0, 205, 205),
                     (229, 229, 229), (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0), (92, 92, 255),
                     (255, 0, 255), (0, 255, 255), (255, 255, 255)], np.int32)


def _nearest(rgb, table):
    return int(((table - np.array(rgb)) ** 2).sum(1).argmin())


def _cube256(rgb):
    r, g, b = rgb
    lv = [0, 95, 135, 175, 215, 255]
    ci = [min(range(6), key=lambda i: abs(lv[i] - c)) for c in (r, g, b)]
    cube = 16 + 36 * ci[0] + 6 * ci[1] + ci[2]
    cube_rgb = np.array([lv[i] for i in ci])
    gray_i = min(23, max(0, int(round((sum(rgb) / 3 - 8) / 10))))
    gv = 8 + 10 * gray_i
    if ((np.array(rgb) - gv) ** 2).sum() < ((np.array(rgb) - cube_rgb) ** 2).sum():
        return 232 + gray_i
    return cube


def build_sgr(mode):
    """(fg_strings, bg_strings) - SGR parameter text for the 16 C64 colours."""
    fg, bg = [], []
    for i in range(16):
        r, g, b = [int(v) for v in PAL_C64[i]]
        if mode == "truecolor":
            fg.append(f"38;2;{r};{g};{b}")
            bg.append(f"48;2;{r};{g};{b}")
        elif mode == "256":
            n = _cube256((r, g, b))
            fg.append(f"38;5;{n}")
            bg.append(f"48;5;{n}")
        else:
            n = _nearest((r, g, b), _XTERM16)
            fg.append(str(30 + n if n < 8 else 90 + n - 8))
            bg.append(str(40 + n if n < 8 else 100 + n - 8))
    return fg, bg


# ---------------------------------------------------------------- grid
class Grid:
    def __init__(self, rows, cols, fg=WHITE, bg=BLACK):
        self.rows, self.cols = rows, cols
        self.ch = np.full((rows, cols), 32, np.int16)
        self.fg = np.full((rows, cols), fg, np.int16)
        self.bg = np.full((rows, cols), bg, np.int16)

    def put(self, r, c, ch, fg=None, bg=None):
        if 0 <= r < self.rows and 0 <= c < self.cols:
            self.ch[r, c] = ord(ch) if isinstance(ch, str) else ch
            if fg is not None:
                self.fg[r, c] = fg
            if bg is not None:
                self.bg[r, c] = bg

    def text(self, r, c, s, fg=None, bg=None):
        if not 0 <= r < self.rows:
            return
        for i, ch in enumerate(s):
            x = c + i
            if 0 <= x < self.cols:
                self.ch[r, x] = ord(ch)
                if fg is not None:
                    self.fg[r, x] = fg
                if bg is not None:
                    self.bg[r, x] = bg

    def center(self, r, s, fg=None, bg=None, c0=0, width=None):
        w = self.cols - c0 if width is None else width
        self.text(r, c0 + max(0, (w - len(s)) // 2), s, fg, bg)

    def fill(self, r, c, h, w, bg=None, fg=None, ch=32):
        r0, c0 = max(r, 0), max(c, 0)
        r1, c1 = min(r + h, self.rows), min(c + w, self.cols)
        if r1 <= r0 or c1 <= c0:
            return
        self.ch[r0:r1, c0:c1] = ch
        if bg is not None:
            self.bg[r0:r1, c0:c1] = bg
        if fg is not None:
            self.fg[r0:r1, c0:c1] = fg

    def box(self, r, c, h, w, fg, bg=BLACK):
        self.fill(r, c, h, w, bg=bg, fg=fg)
        for x in range(c + 1, c + w - 1):
            self.put(r, x, "-", fg, bg)
            self.put(r + h - 1, x, "-", fg, bg)
        for y in range(r + 1, r + h - 1):
            self.put(y, c, ":", fg, bg)
            self.put(y, c + w - 1, ":", fg, bg)
        for (y, x) in ((r, c), (r, c + w - 1), (r + h - 1, c), (r + h - 1, c + w - 1)):
            self.put(y, x, "+", fg, bg)

    def copy(self):
        g = Grid.__new__(Grid)
        g.rows, g.cols = self.rows, self.cols
        g.ch, g.fg, g.bg = self.ch.copy(), self.fg.copy(), self.bg.copy()
        return g


# ---------------------------------------------------------------- presenter
class Presenter:
    """Encodes Grids into terminal bytes with exact delta compression.

    The previous grid is only replaced when a frame is actually *sent*, so a frame that is
    dropped (client too slow / bandwidth budget) is simply folded into the next delta.
    """

    def __init__(self, petscii, color_mode="truecolor", rate=100000, gap=3, full_refresh=12.0, wrap=True):
        self.petscii = petscii
        self.mode = color_mode
        self.rate = float(rate)
        self.gap = gap
        self.full_refresh = full_refresh
        self.wrap = wrap
        self.cap = max(600.0, self.rate * 0.25)       # a small burst only: a big one is just queued lag
        self.budget = self.cap
        self.t_budget = time.monotonic()
        self.reset()
        self.set_color_mode(color_mode)

    def set_color_mode(self, mode):
        self.mode = mode
        self.fg_sgr, self.bg_sgr = build_sgr(mode)
        self._sgr = {}
        if self.petscii:
            # one colour token + the odd reverse-toggle byte; cursor hops are 1 byte/step
            self._move_cost, self._char_cost, self._color_cost = 2.0, 1.0, 1.0
        else:
            # an SGR colour-change costs whatever this colour mode's escape text actually is
            # (truecolor ~28 bytes, 256-colour ~16, 16-colour ~9) - measuring it keeps the
            # cost model honest instead of guessing one number for every mode
            self._move_cost = 5.0
            self._char_cost = 1.0
            self._color_cost = float(len(self._both(WHITE, BLACK)))
        self.reset()

    def reset(self):
        self.prev = None
        self.last_full = 0.0
        self.p_rev = False
        self.p_color = None
        self.a_cur = None
        self._pend_cur = None

    def _refill(self):
        now = time.monotonic()
        self.budget = min(self.cap, self.budget + (now - self.t_budget) * self.rate)
        self.t_budget = now

    def ready(self, backlog=0):
        self._refill()
        return self.budget > self.rate * 0.02 and backlog < max(4000, self.rate * 0.5)

    def _limit_mask(self, mask, fg, bg, ch_changed, byte_budget):
        """Pick which changed cells actually get sent this frame, under a byte budget.

        The old version spent the budget on a flat count of "most important" cells,
        scattered one by one across the screen. In a hallway - a big, nearly solid-colour
        wall - that scatter is the worst possible pattern: half the wall gets redrawn and
        half doesn't, which looks like static/stipple since the untouched cells just sit
        there showing last frame's view. It also under-serves exactly the areas that would
        have been cheap: a long run of same-coloured cells barely costs anything per cell
        to send (no repeated colour escapes), while a cluttered, many-coloured area costs a
        lot - but the old code charged every cell the same.

        So instead: group each row's changed cells into contiguous runs (the same runs the
        encoder itself will emit), estimate the real byte cost of each run from its length
        and how many colour changes it contains, and greedily pack whole runs into the
        budget by score-per-byte. A plain hallway wall is one cheap run and gets drawn
        solid; a visually busy area gets whatever fraction of it the budget can afford,
        instead of degrading everything equally into noise.
        """
        rows, cols = mask.shape
        move_c, char_c, color_c = self._move_cost, self._char_cost, self._color_cost
        runs = []                                        # (score_per_byte, cost, r, c0, c1)
        total_cost = 0.0
        for r in np.nonzero(mask.any(axis=1))[0].tolist():
            idxs = np.nonzero(mask[r])[0]
            if idxs.size == 0:
                continue
            gaps = np.diff(idxs) > self.gap + 1           # same short-gap bridging the encoder uses (k = diff-1 <= gap)
            splits = np.nonzero(gaps)[0] + 1
            row_chg = ch_changed[r]
            pair_id = fg[r].astype(np.int32) * 16 + bg[r].astype(np.int32)
            for grp in np.split(idxs, splits):
                c0, c1 = int(grp[0]), int(grp[-1]) + 1
                length = c1 - c0
                # colour cost scales with how many times the colour actually *changes* while
                # scanning left to right (what the encoder really emits), not how many
                # distinct colours appear - a flat wall has one, a striped one alternates
                # every cell even with only two colours total
                switches = int(np.count_nonzero(np.diff(pair_id[c0:c1]))) if length > 1 else 0
                cost = move_c + length * char_c + switches * color_c
                score = float(row_chg[c0:c1].sum()) * 2.0 + length
                score -= 0.6 * abs(r - rows / 2.0) / (rows / 2.0) * length
                if r == 0 or r >= rows - 3:
                    score += 3.0 * length
                total_cost += cost
                runs.append((score / cost, cost, r, c0, c1))
        if total_cost <= byte_budget:
            return mask                                   # everything that changed fits - send it all
        runs.sort(key=lambda t: -t[0])
        out = np.zeros_like(mask)
        spent = 0.0
        for _density, cost, r, c0, c1 in runs:
            if spent + cost > byte_budget:
                continue                                  # skip it, but a cheaper later run may still fit
            out[r, c0:c1] = True
            spent += cost
        return out

    def _both(self, f, b):
        k = f * 16 + b
        s = self._sgr.get(k)
        if s is None:
            s = self._sgr[k] = f"\x1b[{self.fg_sgr[f]};{self.bg_sgr[b]}m"
        return s

    def sgr(self, f, b):
        k = f * 16 + b
        s = self._sgr.get(k)
        if s is None:
            s = self._sgr[k] = f"\x1b[{self.fg_sgr[f]};{self.bg_sgr[b]}m"
        return s

    # -- public
    def encode(self, g, force=False):
        """Return bytes to send (b'' if nothing changed) or None to skip this frame.

        Under a tight byte budget (a C64 on a slow link) only the most important changed cells
        are sent (centre of the view, HUD rows, characters that changed); the rest stay marked
        as changed and go out with a later frame.  So the picture degrades smoothly instead of
        freezing, and never queues up behind the link."""
        self._refill()
        now = time.monotonic()
        prev = self.prev
        full = (prev is None or prev[0].shape != g.ch.shape or force == "full"
                or now - self.last_full > self.full_refresh)
        byte_budget = None
        if not full and not force:
            if self.budget < self.rate * 0.02:
                return None
            byte_budget = self.budget * 0.9
        if self.petscii:
            data, state, cells = self._pet(g, prev, full, byte_budget)
        else:
            data, state, cells = self._ansi(g, prev, full, byte_budget)
        if data is None:
            self.prev = state
            return b""
        n = len(data)
        if full and not force and n > self.budget and self.budget < self.cap * 0.9:
            return None
        self.budget -= n
        self.prev = state
        if full:
            self.last_full = now
        return data

    # -- ANSI
    def _ansi(self, g, prev, full, byte_budget=None):
        state = (g.ch, g.fg, g.bg)
        rows, cols = g.ch.shape
        pre = ""
        if full:
            mask = np.ones((rows, cols), bool)
            pre = "\x1b[0m\x1b[?7l\x1b[2J\x1b[H"      # autowrap off, clear: nothing can scroll the screen
            cur = None
        else:
            mask = (g.ch != prev[0]) | (g.bg != prev[2]) | ((g.fg != prev[1]) & (g.ch != 32))
            cur = self.a_cur                              # colours persist between frames
        mask[rows - 1, cols - 1] = False                  # the last cell is where terminals scroll
        if not full and not mask.any():
            return None, state, 0
        if byte_budget is not None and not full:
            sel = self._limit_mask(mask, g.fg, g.bg, g.ch != prev[0], byte_budget)
            if sel is not mask:
                mask = sel
                state = (np.where(mask, g.ch, prev[0]), np.where(mask, g.fg, prev[1]), np.where(mask, g.bg, prev[2]))
        ncells = int(mask.sum())
        out = [pre]
        gap = self.gap
        fgs_s, bgs_s = self.fg_sgr, self.bg_sgr
        both = self._both
        for r in np.nonzero(mask.any(axis=1))[0].tolist():
            idxs = np.nonzero(mask[r])[0].tolist()
            chs, fgs, bgs = g.ch[r].tolist(), g.fg[r].tolist(), g.bg[r].tolist()
            n = len(idxs)
            i = 0
            x = -1                                        # cursor column after the last write (-1 = unknown)
            while i < n:
                s0 = idxs[i]
                if x < 0:
                    out.append(f"\x1b[{r + 1};{s0 + 1}H")
                elif s0 > x:
                    d = s0 - x
                    out.append("\x1b[C" if d == 1 else f"\x1b[{d}C")
                x = s0
                e = s0
                while True:
                    c, f, b = chs[x], fgs[x], bgs[x]
                    if c == 32 and cur is not None:
                        f = cur[0]
                    if cur is None:
                        out.append(both(f, b))
                    elif f != cur[0] or b != cur[1]:
                        if f == cur[0]:
                            out.append(f"\x1b[{bgs_s[b]}m")
                        elif b == cur[1]:
                            out.append(f"\x1b[{fgs_s[f]}m")
                        else:
                            out.append(both(f, b))
                    cur = (f, b)
                    out.append(chr(c))
                    x += 1
                    # next changed cell on this row: continue the run through a short gap
                    # when re-writing the unchanged cells is cheaper than moving the cursor
                    while i < n and idxs[i] < x:
                        i += 1
                    if i >= n:
                        break
                    nxt = idxs[i]
                    k = nxt - x
                    if k == 0:
                        continue
                    if k <= gap:
                        ok = True
                        for y in range(x, nxt):
                            if bgs[y] != cur[1] or (chs[y] != 32 and fgs[y] != cur[0]):
                                ok = False
                                break
                        if ok:
                            while x < nxt:
                                out.append(chr(chs[x]))
                                x += 1
                            continue
                    break
        self._pend_cur = cur
        return "".join(out).encode("ascii", "replace"), state, ncells

    # -- PETSCII
    def _pet(self, g, prev, full, byte_budget=None):
        rows, cols = g.ch.shape
        rev = g.bg != BLACK
        color = np.where(rev, g.bg, g.fg).astype(np.int16)
        ch = g.ch
        color = np.where((ch == 32) & ~rev, 0, color)          # a plain space has no colour
        state = (ch.copy(), color, rev)
        if full:
            mask = np.ones((rows, cols), bool)
        else:
            mask = (ch != prev[0]) | (color != prev[1]) | (rev != prev[2])
        mask[rows - 1, cols - 1] = False                         # never touch the last cell (scrolls)
        if not mask.any():
            return None, state, 0
        if byte_budget is not None and not full:
            # color doubles as both fg/bg here (petscii cells are one colour, not two);
            # feeding it in twice keeps _limit_mask's "pairs of colour" cost estimate meaningful
            sel = self._limit_mask(mask, color, color, ch != prev[0], byte_budget)
            if sel is not mask:
                mask = sel
                state = (np.where(mask, ch, prev[0]), np.where(mask, color, prev[1]), np.where(mask, rev, prev[2]))
        ncells = int(mask.sum())
        out = bytearray([PET_HOME])
        pos = (0, 0)
        p_rev, p_color = self.p_rev, self.p_color
        wrap = self.wrap
        for r in np.nonzero(mask.any(axis=1))[0].tolist():
            idxs = np.nonzero(mask[r])[0].tolist()
            chs = PET_LUT[np.clip(ch[r], 0, 255)].tolist()
            cols_l, revs = color[r].tolist(), rev[r].tolist()
            for x in idxs:
                # --- move the cursor
                if pos is None:
                    out.append(PET_HOME)
                    pos = (0, 0)
                dr, dc = r - pos[0], x - pos[1]
                if abs(dr) + abs(dc) > 1 + r + x:            # cheaper from HOME
                    out.append(PET_HOME)
                    dr, dc = r, x
                if dr:
                    out.extend(bytes([PET_DOWN if dr > 0 else PET_UP]) * abs(dr))
                if dc:
                    out.extend(bytes([PET_RIGHT if dc > 0 else PET_LEFT]) * abs(dc))
                # --- the cell
                rv = revs[x]
                if rv != p_rev:
                    out.append(PET_REV_ON if rv else PET_REV_OFF)
                    p_rev = rv
                cv = cols_l[x]
                if (rv or chs[x] != 32) and cv != p_color:
                    out.append(PETSCII_COLOR[cv])
                    p_color = cv
                out.append(chs[x])
                if x == cols - 1:
                    pos = None                                   # don't rely on the client's line wrap
                else:
                    pos = (r, x + 1)
        self.p_rev, self.p_color = p_rev, p_color
        return bytes(out), state, ncells


# ---------------------------------------------------------------- 3D scene
TONES = {
    0: (DGREY, MGREY, LGREY),       # wall
    1: (MGREY, LGREY, WHITE),       # pillar
    2: (DGREY, DGREY, MGREY),       # lintel
    3: (DGREY, DGREY, MGREY),       # sill
    4: (DGREY, DGREY, MGREY),       # hide box walls
    5: (BROWN, BROWN, BROWN),       # cover crates
}


def shorten(name, n):
    """Cut the middle out of a long name and put a '.' there so it fits n chars."""
    if n <= 0:
        return ""
    if len(name) <= n:
        return name
    if n <= 2:
        return name[:n]
    head = (n - 1 + 1) // 2
    tail = n - 1 - head
    return name[:head] + "." + (name[len(name) - tail:] if tail else "")


class Scene3D:
    def __init__(self, world, aspect=2.0, fov=66.0, texture=True, max_dist=70.0):
        self.w = world
        self.aspect = aspect
        self.plane = math.tan(math.radians(fov) / 2)
        self.texture = texture
        self.max_dist = max_dist
        self.maxd_tex = 14.0
        self.fx = self.fy = 1.0

    # ---------------------------------------------------------- walls etc
    def render(self, g, vx, vy, vw, vh, cam, others, now):
        w = self.w
        px, pz, yaw, eye = cam
        fx = (vw / 2.0) / self.plane
        fy = fx / self.aspect
        h0 = vh / 2.0
        self.fx, self.fy = fx, fy
        fwdx, fwdz = math.cos(yaw), math.sin(yaw)
        rx, rz = math.sin(yaw), -math.cos(yaw)
        camx = (2.0 * (np.arange(vw) + 0.5) / vw - 1.0) * self.plane
        Dx, Dz = fwdx + rx * camx, fwdz + rz * camx

        ch = np.full((vh, vw), 32, np.int16)
        fg = np.full((vh, vw), BLACK, np.int16)
        bg = np.full((vh, vw), BLACK, np.int16)
        dep = np.full((vh, vw), np.inf, np.float32)

        rows = np.arange(vh) + 0.5
        # floor
        below = rows - h0
        fi = np.nonzero(below > 0.01)[0]
        if len(fi):
            dpf = eye * fy / below[fi]
            X = px + Dx[None, :] * dpf[:, None]
            Z = pz + Dz[None, :] * dpf[:, None]
            tile = ((np.floor(X / 2.0) + np.floor(Z / 2.0)).astype(np.int32) & 1).astype(bool)
            mark = tile & (dpf[:, None] < 20.0)
            bg[fi, :] = YELLOW
            fg[fi, :] = BROWN
            ch[fi, :] = np.where(mark, 46, 32)
        # sky / ceilings
        up = h0 - rows
        si = np.nonzero(up > 0.01)[0]
        if len(si):
            upv = up[si]
            dpr = (H_WALL - eye) * fy / upv
            X = px + Dx[None, :] * dpr[:, None]
            Z = pz + Dz[None, :] * dpr[:, None]
            roofm = w.mask_at(w.roof_mask, X, Z)
            bg[si, :] = np.where(roofm, DGREY, BLUE)
            tex = ((np.floor(X / 1.5) + np.floor(Z / 1.5)).astype(np.int32) & 1).astype(bool) & roofm
            ch[si, :] = np.where(tex, 58, 32)
            fg[si, :] = MGREY
            if eye < LOWCEIL_H - 0.05:
                dpl = (LOWCEIL_H - eye) * fy / upv
                X = px + Dx[None, :] * dpl[:, None]
                Z = pz + Dz[None, :] * dpl[:, None]
                lowm = w.mask_at(w.low_mask, X, Z)
                sub = bg[si, :]
                sub[lowm] = DGREY
                bg[si, :] = sub
                chs = ch[si, :]
                chs[lowm] = 45
                ch[si, :] = chs

        # walls: ray / segment intersection for every column at once
        ax, az, ex, ez = w.ax, w.az, w.ex, w.ez
        apx, apz = ax - px, az - pz
        den = Dx[:, None] * ez[None, :] - Dz[:, None] * ex[None, :]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (apx * ez - apz * ex)[None, :] / den
            s = (apx[None, :] * Dz[:, None] - apz[None, :] * Dx[:, None]) / den
        has_n = (w.nx != 0) | (w.nz != 0)
        facing = ((Dx[:, None] * w.nx[None, :] + Dz[:, None] * w.nz[None, :]) < 0) | (~has_n)[None, :]
        valid = (np.abs(den) > 1e-9) & (t > 0.03) & (t < self.max_dist) & (s >= 0.0) & (s <= 1.0) & facing
        t = np.where(valid, t, np.inf)
        K = 8
        order = np.argpartition(t, min(K, t.shape[1] - 1), axis=1)[:, :K] if t.shape[1] > K else \
            np.tile(np.arange(t.shape[1]), (vw, 1))
        ts = np.take_along_axis(t, order, 1)
        so = np.argsort(ts, axis=1)
        order = np.take_along_axis(order, so, 1)
        ts = np.take_along_axis(ts, so, 1)
        ss = np.take_along_axis(s, order, 1)
        opq = w.full[order] & np.isfinite(ts)
        first = np.where(opq.any(axis=1), opq.argmax(axis=1), ts.shape[1] - 1).tolist()
        ts_l, od_l, ss_l = ts.tolist(), order.tolist(), ss.tolist()
        y0a, y1a, mata, lena, lighta = w.y0, w.y1, w.mat, w.len, w.light
        texon = self.texture
        inf = math.inf
        for c in range(vw):
            tc, oc, sc = ts_l[c], od_l[c], ss_l[c]
            for k in range(first[c], -1, -1):
                tt = tc[k]
                if tt == inf:
                    continue
                i = oc[k]
                top = h0 - (y1a[i] - eye) / tt * fy
                bot = h0 - (y0a[i] - eye) / tt * fy
                r0 = int(top + 0.5) if top > 0 else 0
                r1 = int(bot + 0.5) if bot < vh else vh
                if r1 > vh:
                    r1 = vh
                if r0 < 0:
                    r0 = 0
                if r1 <= r0:
                    continue
                m = int(mata[i])
                tone = TONES.get(m, TONES[0])
                v = lighta[i] * (0.5 + 0.5 * max(0.0, 1.0 - tt / 40.0))
                col = tone[2] if v > 0.80 else (tone[1] if v > 0.58 else tone[0])
                bg[r0:r1, c] = col
                dep[r0:r1, c] = tt
                ch[r0:r1, c] = 32
                if texon and tt < self.maxd_tex and r1 - r0 >= 2:
                    uu = sc[k] * lena[i]
                    yy = eye - (np.arange(r0, r1) + 0.5 - h0) / fy * tt
                    if m == 1:                                   # pillar: stripes
                        if (uu * 3.0) % 1.0 < 0.5:
                            ch[r0:r1, c] = 58
                            fg[r0:r1, c] = DGREY
                    elif m == 5:                                 # crate: slats
                        ch[r0:r1, c] = np.where((yy % 0.45) < 0.1, 61, 32)
                        fg[r0:r1, c] = BLACK
                    elif m in (0, 4):                            # brick
                        mort = (yy % 0.6) < 0.12
                        joint = ((uu + (np.floor(yy / 0.6) % 2) * 0.6) % 1.2) < 0.1
                        ch[r0:r1, c] = np.where(mort, 45, np.where(joint, 58, 32))
                        fg[r0:r1, c] = DGREY if col != DGREY else BLACK

        # players
        ctx = (vw, vh, fx, fy, h0, fwdx, fwdz, rx, rz)
        ents = []
        for e in others:
            dx, dz = e.x - px, e.z - pz
            d = dx * fwdx + dz * fwdz
            if 0.35 < d < 60:
                ents.append((d, e))
        ents.sort(key=lambda a: -a[0])
        for d, e in ents:
            self._sprite(e, d, cam, ctx, ch, fg, bg, dep, now)

        g.ch[vy:vy + vh, vx:vx + vw] = ch
        g.fg[vy:vy + vh, vx:vx + vw] = fg
        g.bg[vy:vy + vh, vx:vx + vw] = bg

    # ---------------------------------------------------------- stick figures
    def _sprite(self, e, d, cam, ctx, ch, fg, bg, dep, now):
        vw, vh, fx, fy, h0, fwdx, fwdz, rx, rz = ctx
        px, pz, yaw, eye = cam
        dx, dz = e.x - px, e.z - pz
        lat = dx * rx + dz * rz
        k = (HGT_CROUCH if e.crouch else HGT_STAND) / HGT_STAND
        fxv, fzv = math.cos(e.yaw), math.sin(e.yaw)
        f_lat = fxv * rx + fzv * rz
        f_dep = fxv * fwdx + fzv * fwdz
        toward = -f_dep                                    # 1 = facing the viewer, -1 = facing away
        at = abs(toward)
        dtest = d - 0.2
        dying = e.dying_until > now
        flash = e.flash_until > now
        white = WHITE
        if dying:
            white = LRED if int(now * 14) % 2 else RED

        def rect(r0, r1, c0, c1, color):
            if r1 <= r0 or c1 <= c0:
                return
            r0, c0 = max(r0, 0), max(c0, 0)
            r1, c1 = min(r1, vh), min(c1, vw)
            if r1 <= r0 or c1 <= c0:
                return
            sub = dep[r0:r1, c0:c1]
            m = sub > dtest
            if not m.any():
                return
            bsub = bg[r0:r1, c0:c1]
            bsub[m] = color
            csub = ch[r0:r1, c0:c1]
            csub[m] = 32

        def proj(lat_off, dep_off, y):
            dd = max(0.25, d + dep_off)
            return vw / 2.0 + (lat + lat_off) / dd * fx, h0 - (y - eye) / dd * fy

        def line(c0, r0, c1, r1, color, th):
            n = int(max(abs(c1 - c0), abs(r1 - r0))) + 1
            n = min(n, 200)
            for i in range(n + 1):
                t = i / n
                c = int(round(c0 + (c1 - c0) * t))
                r = int(round(r0 + (r1 - r0) * t))
                rect(r, r + th, c, c + 1, color)

        cc, rtop = proj(0, 0, 1.28 * k)
        _, rbot = proj(0, 0, 0.22 * k)
        _, rhead_top = proj(0, 0, 1.75 * k)
        _, rhead_bot = proj(0, 0, 1.32 * k)
        cc = int(round(cc))
        tw = max(1, int(round(0.26 * fx / d * (0.5 + 0.5 * at))))
        c0 = cc - tw // 2
        # torso
        rect(int(round(rtop)), int(round(rbot)), c0, c0 + tw, white)
        # head (hollow when you can see the face)
        hw = max(1, int(round(0.40 * fx / d * (0.45 + 0.55 * at))))
        hh = max(1, int(round(rhead_bot - rhead_top)))
        hr0 = int(round(rhead_top))
        hc0 = cc - hw // 2
        if toward > 0.35 and hw >= 3 and hh >= 3:
            rect(hr0, hr0 + 1, hc0, hc0 + hw, white)
            rect(hr0 + hh - 1, hr0 + hh, hc0, hc0 + hw, white)
            rect(hr0 + 1, hr0 + hh - 1, hc0, hc0 + 1, white)
            rect(hr0 + 1, hr0 + hh - 1, hc0 + hw - 1, hc0 + hw, white)
        else:
            rect(hr0, hr0 + hh, hc0, hc0 + hw, white)
        # arm + gun
        La = 0.72
        ys = 1.05 * k
        cs, rs = proj(0, 0, ys)
        th = max(1, int(round(0.09 * fy / d)))
        gun = MGREY if not dying else LRED
        if toward > 0.78:
            gw = max(1, int(round(0.22 * fx / d)))
            gh = max(1, int(round(0.22 * fy / d)))
            gc, gr = int(round(cs)) - gw // 2, int(round(rs)) - gh // 2
            if flash:
                rect(gr - 1, gr + gh + 1, gc - 1, gc + gw + 1, ORANGE)
                if gw >= 2:
                    rect(gr - 2, gr + gh + 2, gc + gw // 2, gc + gw // 2 + 1, ORANGE)
                    rect(gr + gh // 2, gr + gh // 2 + 1, gc - 2, gc + gw + 2, ORANGE)
            rect(gr, gr + gh, gc, gc + gw, gun)
        elif toward > -0.78:
            ce, re_ = proj(f_lat * La, f_dep * La, ys)
            cm, rm = proj(f_lat * La * 0.5, f_dep * La * 0.5, ys)
            line(int(round(cs)), int(round(rs)), int(round(cm)), int(round(rm)), white, th)
            line(int(round(cm)), int(round(rm)), int(round(ce)), int(round(re_)), gun, th)
            if flash:
                c2, r2 = proj(f_lat * (La + 0.6), f_dep * (La + 0.6), ys)
                line(int(round(ce)), int(round(re_)), int(round(c2)), int(round(r2)), ORANGE, max(th, 1))
        # name above the head
        if d < 30:
            maxc = int(min(14, 2.3 * fx / d))
            if maxc >= 3:
                label = shorten(e.name, maxc)
                r = hr0 - 1
                c = cc - len(label) // 2
                if 0 <= r < vh:
                    for i, chr_ in enumerate(label):
                        x = c + i
                        if 0 <= x < vw and dep[r, x] > dtest:
                            ch[r, x] = ord(chr_)
                            fg[r, x] = WHITE if not dying else LRED
                            bg[r, x] = BLACK


# ---------------------------------------------------------------- overlays
GUN_ART = ((0, 2, 2, "T"), (1, 2, 2, "B"), (2, 2, 5, "S"), (3, 3, 5, "S"), (4, 5, 3, "G"))


def draw_gun(g, vx, vy, vw, vh, firing, big=False):
    sc = 2 if big else 1
    gw, gh = 9 * sc, 5 * sc
    left = vx + vw - gw - 1 * sc
    top = vy + vh - gh + (0 if not firing else -1)
    col = {"T": DGREY, "B": MGREY, "S": LGREY, "G": DGREY}
    if firing:
        col["T"] = WHITE
    for (rr, cc, ww, kind) in GUN_ART:
        g.fill(top + rr * sc, left + cc * sc, sc, ww * sc, bg=col[kind])
    if firing:
        g.fill(top - sc, left + 2 * sc, sc, 2 * sc, bg=ORANGE)
        if sc == 1:
            g.put(top - 2, left + 2, "*", WHITE, YELLOW)
            g.put(top - 2, left + 3, "*", WHITE, YELLOW)
        else:
            g.fill(top - 3 * sc // 1, left + 2 * sc, sc, 2 * sc, bg=YELLOW)


def draw_crosshair(g, vx, vy, vw, vh):
    r, c = vy + vh // 2, vx + vw // 2
    g.put(r, c, "+", WHITE, BLACK)
    if vw >= 70:
        g.put(r, c - 1, "-", WHITE, BLACK)
        g.put(r, c + 1, "-", WHITE, BLACK)
