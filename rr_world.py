# -*- coding: utf-8 -*-
"""
Retro Recoil - world: OBJ map loader, collision, line of sight and nav grids.

The map comes from a Wavefront .obj file:
  * vertical quads  = wall pieces.  `vn` is the outward normal, the vertical
                      extent of the quad is its height range, `usemtl` picks the look
  * group `roof`    = horizontal quads at wall height (ceilings you see inside buildings)
  * group `lowceil` = horizontal quads at y=1.0 (crouch-only ceilings: hide boxes)
  * groups `yardA`/`yardB` = horizontal ground quads of the two courtyards
  * groups `spawnA`/`spawnB`/`hide` = `p` point elements (spawn points / hide pockets)
"""
import heapq
import math
import os

import numpy as np

H_WALL = 3.0
EYE_STAND, EYE_CROUCH = 1.6, 0.8
HGT_STAND, HGT_CROUCH = 1.75, 0.9
RADIUS = 0.35
LOWCEIL_H = 1.0

MATERIALS = {"wall": 0, "pillar": 1, "lintel": 2, "sill": 3, "hide": 4, "cover": 5}
HASH_CELL = 4.0


class WorldMap:
    def __init__(self, path):
        self.path = path
        verts, norms = [], []
        group, mat = "default", "wall"
        segs = []
        self.rects = {}
        self.points = {}
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.split()
                if not p or p[0].startswith("#"):
                    continue
                k = p[0]
                if k == "v":
                    verts.append((float(p[1]), float(p[2]), float(p[3])))
                elif k == "vn":
                    norms.append((float(p[1]), float(p[2]), float(p[3])))
                elif k in ("g", "o"):
                    group = p[1] if len(p) > 1 else "default"
                elif k == "usemtl":
                    mat = p[1] if len(p) > 1 else "wall"
                elif k == "p":
                    for s in p[1:]:
                        v = verts[int(s.split("/")[0]) - 1]
                        self.points.setdefault(group, []).append((v[0], v[2]))
                elif k == "f":
                    idx = []
                    nidx = None
                    for s in p[1:]:
                        parts = s.split("/")
                        idx.append(int(parts[0]) - 1)
                        if len(parts) >= 3 and parts[2]:
                            nidx = int(parts[2]) - 1
                    pts = [verts[i] for i in idx]
                    ys = [q[1] for q in pts]
                    if max(ys) - min(ys) < 1e-6:
                        xs = [q[0] for q in pts]
                        zs = [q[2] for q in pts]
                        self.rects.setdefault(group, []).append((min(xs), min(zs), max(xs), max(zs), ys[0]))
                        continue
                    xz = []
                    for q in pts:
                        if not any(abs(q[0] - e[0]) < 1e-6 and abs(q[2] - e[1]) < 1e-6 for e in xz):
                            xz.append((q[0], q[2]))
                    if len(xz) < 2:
                        continue
                    a, b = xz[0], xz[1]
                    nx = nz = 0.0
                    if nidx is not None and nidx < len(norms):
                        nx, nz = norms[nidx][0], norms[nidx][2]
                    segs.append((a[0], a[1], b[0] - a[0], b[1] - a[1], min(ys), max(ys), nx, nz,
                                 MATERIALS.get(mat, 0)))
        if not segs:
            raise ValueError("map has no walls: " + path)
        S = np.array(segs, np.float64)
        self.ax, self.az, self.ex, self.ez = S[:, 0].copy(), S[:, 1].copy(), S[:, 2].copy(), S[:, 3].copy()
        self.y0, self.y1 = S[:, 4].copy(), S[:, 5].copy()
        self.nx, self.nz = S[:, 6].copy(), S[:, 7].copy()
        self.mat = S[:, 8].astype(np.int32)
        self.len = np.hypot(self.ex, self.ez)
        self.n = len(segs)
        # Per-segment mild directional light (0.55..1.0) for shading
        lx, lz = 0.35, 0.94
        self.light = np.clip(0.78 + 0.22 * (self.nx * lx + self.nz * lz), 0.5, 1.0)
        # 'full height' segments hide everything behind them
        self.full = (self.y0 <= 0.01) & (self.y1 >= H_WALL - 0.01)
        self.minx = float(min(self.ax.min(), (self.ax + self.ex).min())) - 1
        self.maxx = float(max(self.ax.max(), (self.ax + self.ex).max())) + 1
        self.minz = float(min(self.az.min(), (self.az + self.ez).min())) - 1
        self.maxz = float(max(self.az.max(), (self.az + self.ez).max())) + 1
        # collision hash (pure python tuples; 30 Hz * few entities)
        self.hash = {}
        for i in range(self.n):
            x0, x1 = sorted((self.ax[i], self.ax[i] + self.ex[i]))
            z0, z1 = sorted((self.az[i], self.az[i] + self.ez[i]))
            rec = (float(self.ax[i]), float(self.az[i]), float(self.ex[i]), float(self.ez[i]),
                   float(self.len[i] ** 2) or 1e-9, float(self.y0[i]), float(self.y1[i]),
                   float(self.nx[i]), float(self.nz[i]))
            for cx in range(int(math.floor((x0 - 1.0) / HASH_CELL)), int(math.floor((x1 + 1.0) / HASH_CELL)) + 1):
                for cz in range(int(math.floor((z0 - 1.0) / HASH_CELL)), int(math.floor((z1 + 1.0) / HASH_CELL)) + 1):
                    self.hash.setdefault((cx, cz), []).append(rec)
        self.lowceil = self.rects.get("lowceil", [])
        self.spawn = {"A": self.points.get("spawnA", []), "B": self.points.get("spawnB", [])}
        self.hide_points = self.points.get("hide", [])
        self.yard = {}
        for k in ("yardA", "yardB"):
            r = self.rects.get(k)
            if r:
                self.yard[k[-1]] = r[0][:4]
        self._build_masks()
        self.nav = {}

    # ------------------------------------------------------------ roof masks
    def _build_masks(self):
        self.mcell = 0.5
        self.mw = int((self.maxx - self.minx) / self.mcell) + 2
        self.mh = int((self.maxz - self.minz) / self.mcell) + 2
        self.roof_mask = np.zeros((self.mh, self.mw), bool)
        self.low_mask = np.zeros((self.mh, self.mw), bool)
        for name, mask in (("roof", self.roof_mask), ("lowceil", self.low_mask)):
            for x0, z0, x1, z1, _y in self.rects.get(name, []):
                i0, i1 = int((x0 - self.minx) / self.mcell), int(math.ceil((x1 - self.minx) / self.mcell))
                j0, j1 = int((z0 - self.minz) / self.mcell), int(math.ceil((z1 - self.minz) / self.mcell))
                mask[j0:j1, i0:i1] = True

    def mask_at(self, mask, X, Z):
        i = ((X - self.minx) / self.mcell).astype(np.int32)
        j = ((Z - self.minz) / self.mcell).astype(np.int32)
        ok = (i >= 0) & (i < self.mw) & (j >= 0) & (j < self.mh)
        out = np.zeros(X.shape, bool)
        out[ok] = mask[j[ok], i[ok]]
        return out

    # ------------------------------------------------------------ headroom / collision
    def headroom(self, x, z):
        for x0, z0, x1, z1, _y in self.lowceil:
            if x0 <= x <= x1 and z0 <= z <= z1:
                return LOWCEIL_H
        return H_WALL

    def can_stand(self, x, z):
        return self.headroom(x, z) > HGT_STAND + 0.05

    def push_out(self, x, z, r, h):
        for _ in range(4):
            lst = self.hash.get((int(math.floor(x / HASH_CELL)), int(math.floor(z / HASH_CELL))))
            if not lst:
                return x, z
            best = None
            for ax, az, ex, ez, l2, y0, y1, nx, nz in lst:
                if y0 >= h or y1 <= 0.05:
                    continue
                t = ((x - ax) * ex + (z - az) * ez) / l2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                cx, cz = ax + t * ex, az + t * ez
                dx, dz = x - cx, z - cz
                d2 = dx * dx + dz * dz
                if d2 < r * r and (best is None or d2 < best[0]):
                    best = (d2, dx, dz, nx, nz)
            if best is None:
                return x, z
            d2, dx, dz, nx, nz = best
            d = math.sqrt(d2)
            if d < 1e-6:
                dx, dz, d = nx, nz, 1.0 if (nx or nz) else 1.0
            x += dx / d * (r - math.sqrt(d2) + 1e-4)
            z += dz / d * (r - math.sqrt(d2) + 1e-4)
        return x, z

    def blocked_circle(self, x, z, r, h):
        lst = self.hash.get((int(math.floor(x / HASH_CELL)), int(math.floor(z / HASH_CELL))))
        if not lst:
            return False
        rr = r * r
        for ax, az, ex, ez, l2, y0, y1, nx, nz in lst:
            if y0 >= h or y1 <= 0.05:
                continue
            t = ((x - ax) * ex + (z - az) * ez) / l2
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            dx, dz = x - (ax + t * ex), z - (az + t * ez)
            if dx * dx + dz * dz < rr:
                return True
        return False

    def _clamp(self, x, z):
        """Invisible perimeter wall. Without this, a player who slips past the outermost
        geometry finds nothing left to collide with (the collision hash has no entries out
        there), so they can keep drifting into empty space and have no way back except
        walking the whole way in blind. Clamping into the map's own bounding box (which
        already carries a margin past the outermost walls/floor - see __init__) turns the
        edge of the level into a soft wall instead, the same way push_out stops a player at
        real geometry."""
        return (min(max(x, self.minx + RADIUS), self.maxx - RADIUS),
                min(max(z, self.minz + RADIUS), self.maxz - RADIUS))

    def move(self, x, z, dx, dz, h, crouch_ok=True):
        """Move a body (radius RADIUS, height h); returns the resulting (x, z)."""
        nx, nz = self.push_out(x + dx, z + dz, RADIUS, h)
        if h >= self.headroom(nx, nz) - 0.02:
            # no headroom there: try sliding on each axis, else stay
            for tx, tz in ((x + dx, z), (x, z + dz)):
                px, pz = self.push_out(tx, tz, RADIUS, h)
                if h < self.headroom(px, pz) - 0.02:
                    return self._clamp(px, pz)
            return self._clamp(x, z)
        return self._clamp(nx, nz)

    # ------------------------------------------------------------ line of sight
    def ray_blocked(self, x0, z0, x1, z1, y):
        """True if a horizontal line at height y from (x0,z0) to (x1,z1) hits a wall."""
        dx, dz = x1 - x0, z1 - z0
        m = (self.y0 <= y) & (self.y1 >= y)
        den = dx * self.ez[m] - dz * self.ex[m]
        apx, apz = self.ax[m] - x0, self.az[m] - z0
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (apx * self.ez[m] - apz * self.ex[m]) / den
            s = (apx * dz - apz * dx) / den
        ok = (np.abs(den) > 1e-9) & (t > 0.001) & (t < 0.999) & (s >= 0.0) & (s <= 1.0)
        return bool(ok.any())

    # ------------------------------------------------------------ nav grids
    def nav_grid(self, crouch):
        key = bool(crouch)
        g = self.nav.get(key)
        if g is None:
            g = NavGrid(self, crouch)
            self.nav[key] = g
        return g


class NavGrid:
    """Walkable-cell grid + A* for bots.  One grid for standing, one for crouched bodies."""

    CELL = 0.5

    def __init__(self, world, crouch):
        self.w = world
        self.crouch = crouch
        self.h = HGT_CROUCH if crouch else HGT_STAND
        self.minx, self.minz = world.minx, world.minz
        self.nw = int((world.maxx - world.minx) / self.CELL) + 1
        self.nh = int((world.maxz - world.minz) / self.CELL) + 1
        ok = np.zeros((self.nh, self.nw), bool)
        r = RADIUS + 0.02
        for j in range(self.nh):
            z = self.minz + (j + 0.5) * self.CELL
            for i in range(self.nw):
                x = self.minx + (i + 0.5) * self.CELL
                if world.headroom(x, z) <= self.h + 0.03:
                    continue
                if world.blocked_circle(x, z, r, self.h):
                    continue
                ok[j, i] = True
        # keep only cells connected to the spawn points
        seeds = []
        for k in ("A", "B"):
            for (x, z) in world.spawn.get(k, []):
                seeds.append(self.cell_of(x, z))
        reach = np.zeros_like(ok)
        stack = [s for s in seeds if self._in(s) and ok[s[1], s[0]]]
        for s in stack:
            reach[s[1], s[0]] = True
        while stack:
            i, j = stack.pop()
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                a, b = i + di, j + dj
                if 0 <= a < self.nw and 0 <= b < self.nh and ok[b, a] and not reach[b, a]:
                    reach[b, a] = True
                    stack.append((a, b))
        self.ok = reach
        self.cells = [(i, j) for j in range(self.nh) for i in range(self.nw) if reach[j, i]]

    def _in(self, c):
        return 0 <= c[0] < self.nw and 0 <= c[1] < self.nh

    def cell_of(self, x, z):
        return (int((x - self.minx) / self.CELL), int((z - self.minz) / self.CELL))

    def center(self, c):
        return (self.minx + (c[0] + 0.5) * self.CELL, self.minz + (c[1] + 0.5) * self.CELL)

    def nearest_ok(self, c):
        if self._in(c) and self.ok[c[1], c[0]]:
            return c
        for rad in range(1, 6):
            for dj in range(-rad, rad + 1):
                for di in range(-rad, rad + 1):
                    a, b = c[0] + di, c[1] + dj
                    if self._in((a, b)) and self.ok[b, a]:
                        return (a, b)
        return None

    def line_clear(self, a, b):
        (x0, y0), (x1, y1) = a, b
        n = max(abs(x1 - x0), abs(y1 - y0))
        for k in range(n + 1):
            t = k / n if n else 0
            i, j = int(round(x0 + (x1 - x0) * t)), int(round(y0 + (y1 - y0) * t))
            if not (self._in((i, j)) and self.ok[j, i]):
                return False
        return True

    def path(self, sx, sz, gx, gz):
        s = self.nearest_ok(self.cell_of(sx, sz))
        g = self.nearest_ok(self.cell_of(gx, gz))
        if s is None or g is None:
            return []
        if s == g:
            return [self.center(g)]
        ok = self.ok
        openh = [(0.0, 0, s)]
        came = {s: None}
        cost = {s: 0.0}
        nbrs = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414))
        tick = 0
        found = False
        while openh:
            _, _, cur = heapq.heappop(openh)
            if cur == g:
                found = True
                break
            ci, cj = cur
            base = cost[cur]
            for di, dj, c in nbrs:
                a, b = ci + di, cj + dj
                if not (0 <= a < self.nw and 0 <= b < self.nh) or not ok[b, a]:
                    continue
                if di and dj and not (ok[cj, a] and ok[b, ci]):
                    continue
                nc = base + c
                if nc < cost.get((a, b), 1e18):
                    cost[(a, b)] = nc
                    came[(a, b)] = cur
                    tick += 1
                    h = math.hypot(g[0] - a, g[1] - b)
                    heapq.heappush(openh, (nc + h, tick, (a, b)))
        if not found:
            return []
        out = []
        cur = g
        while cur is not None:
            out.append(cur)
            cur = came[cur]
        out.reverse()
        # string-pull: drop cells that are in a clear straight line
        res = [out[0]]
        i = 0
        while i < len(out) - 1:
            j = len(out) - 1
            while j > i + 1 and not self.line_clear(out[i], out[j]):
                j -= 1
            res.append(out[j])
            i = j
        return [self.center(c) for c in res[1:]] or [self.center(g)]
