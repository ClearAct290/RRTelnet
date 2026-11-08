# -*- coding: utf-8 -*-
"""
Retro Recoil - the live game simulation shared by human sessions and bots.

One Match owns a WorldMap and a list of Combatants (Player and/or Bot) and runs a
fixed-rate tick() that everyone feeds into.  Hit detection is an instant hitscan
along the shooter's facing (a 0.08 s cooldown, one-round-per-press retro gun).

Telnet cannot report key releases, so a movement key is "held" for a short window
after each key event (see ``Match.handle_key``): the first press is a short step,
terminal auto-repeat keeps it going, and pressing the opposite key cancels it.
"""
import math
import random
import time
from collections import deque

from rr_world import HGT_STAND, HGT_CROUCH, EYE_STAND, EYE_CROUCH, RADIUS

WALK_SPEED = 7.5            # defaults; real values come from cfg game_walk_speed / game_crouch_speed
CROUCH_SPEED = 3.8
HIT_RADIUS = 0.42
CROUCH_SHOOT_RANGE = 9.0     # max range to hit a crouched target, unless the shooter is crouched too
FIRE_REPEAT_GAP = 0.07       # 'e' events closer than this are terminal auto-repeat, not a new press
OPPOSITE = {"w": "s", "s": "w", "a": "d", "d": "a", "LEFT": "RIGHT", "RIGHT": "LEFT"}


class Combatant:
    def __init__(self, name, is_bot=False):
        self.name = name
        self.is_bot = is_bot
        self.x = self.z = 0.0
        self.yaw = 0.0
        self.crouch_key = False      # the player wants to be crouched
        self.force_crouch = False    # standing in a low-ceiling hide pocket / crouch hole
        self.alive = True
        self.ammo = 15
        self.mag_size = 15
        self.kills = self.deaths = self.reloads = 0
        self.last_shot = -999.0
        self.last_ammo_loss = -999.0
        self.reload_until = 0.0
        self.reloading = False
        self.dying_until = 0.0       # killed: shown flashing red until then, then gone
        self.flash_until = 0.0       # muzzle flash shown this long after firing
        self.respawn_at = 0.0
        self.protected_until = 0.0   # can't be targeted or hit while now < this
        self.killed_by = ""
        self.death_t = 0.0
        self.yard = "A"
        self.vx = self.vz = 0.0      # current velocity: starts/stops are eased so a late key repeat is a dip, not a freeze
        self.move_t = 0.0            # last time apply_move ran for this combatant
        self.typ_delay = 0.40        # learned: keyboard typematic delay (first press -> first repeat)
        self.rep_gap = 0.04          # learned: gap between auto-repeat events
        self.held = {}               # movement/turn key -> expiry (monotonic)
        self.key_last = {}           # key -> time of its last event
        self.recorded = False        # stats already written to the database
        self.left = False

    @property
    def crouch(self):
        return self.crouch_key or self.force_crouch

    @property
    def height(self):
        return HGT_CROUCH if self.crouch else HGT_STAND

    @property
    def eye(self):
        return EYE_CROUCH if self.crouch else EYE_STAND

    def is_held(self, key, now):
        return self.held.get(key, 0.0) > now


class Player(Combatant):
    def __init__(self, session):
        super().__init__(session.username, is_bot=False)
        self.session = session


class Bot(Combatant):
    def __init__(self, name):
        super().__init__(name, is_bot=True)
        self.brain = {}   # scratch state for rr_bots.bot_think: target, path, timers, ...


class Match:
    def __init__(self, world, cfg, lobby=None, clock=time.monotonic):
        self.world = world
        self.cfg = cfg
        self.lobby = lobby
        self.clock = clock
        self.players = []            # Combatants currently in the match (Player + Bot)
        self.departed = []           # humans that left early (their stats still count)
        self.started_at = None
        self.duration = float(cfg.game_match_seconds)
        self.ended = False
        self.force_over = False
        self.results = []
        self.mag_size = int(cfg.game_magazine_size)
        self.fire_cd = float(cfg.game_fire_cooldown_seconds)
        self.reload_cd = float(cfg.game_reload_cooldown_seconds)
        self.reload_time = float(cfg.game_reload_seconds)
        self.respawn_time = float(cfg.game_respawn_seconds)
        self.spawn_protection = float(cfg.game_spawn_protection_seconds)
        self.start_grace = float(cfg.game_start_grace_seconds)
        self.turn_rate = float(cfg.game_turn_rate)
        self.toggle_crouch = str(cfg.game_crouch_mode).lower() != "hold"
        self.key_first = float(cfg.game_key_first_hold)
        self.key_repeat = float(cfg.game_key_repeat_hold)
        self.turn_first = float(cfg.game_turn_first_hold)
        self.turn_repeat = float(cfg.game_turn_repeat_hold)
        self.key_carry = float(cfg.game_key_carry_hold)
        self.walk = float(cfg.game_walk_speed)
        self.crouch_speed = float(cfg.game_crouch_speed)
        self.accel_time = max(0.01, float(cfg.game_accel_seconds))
        self.decel_time = max(0.01, float(cfg.game_decel_seconds))
        self.assist_cos = math.cos(math.radians(float(cfg.game_assist_cone_deg)))
        self.assist_slope = float(cfg.game_assist_lateral_slope)
        self.assist_radius = float(cfg.game_assist_radius)
        self.feed = deque(maxlen=4)  # (time, killer, victim) for the HUD kill feed

    # ---------------------------------------------------------------- setup
    def humans(self):
        return [c for c in self.players if not c.is_bot]

    def add(self, c, yard=None):
        c.mag_size = self.mag_size
        c.ammo = self.mag_size
        self.players.append(c)
        self.respawn(c, yards=[yard] if yard else None)

    def remove(self, c):
        """A combatant leaves mid-match (human quit / disconnect)."""
        if c in self.players:
            self.players.remove(c)
            if not c.is_bot:
                self.departed.append(c)
        c.left = True

    def start(self, now=None):
        now = self.clock() if now is None else now
        self.started_at = now
        for c in self.players:                      # a few seconds to get your bearings
            c.protected_until = max(c.protected_until, now + self.start_grace)

    def time_left(self, now=None):
        if self.started_at is None:
            return self.duration
        now = self.clock() if now is None else now
        return max(0.0, self.duration - (now - self.started_at))

    def is_over(self):
        return self.force_over or (self.started_at is not None and self.time_left() <= 0.0)

    # ---------------------------------------------------------------- spawning
    def _pick_spot(self, c, yards):
        """A spot in a courtyard far from, and out of sight of, everybody still alive."""
        world = self.world
        enemies = [e for e in self.players if e is not c and e.alive]
        best, best_score = None, -1e9
        for _ in range(30):
            yard = random.choice(yards)
            x0, z0, x1, z1 = world.yard[yard]
            x = random.uniform(x0 + 2.0, x1 - 2.0)
            z = random.uniform(z0 + 2.0, z1 - 2.0)
            if world.blocked_circle(x, z, RADIUS + 0.3, HGT_STAND):
                continue
            score = random.random() * 3.0
            near = 60.0
            for e in enemies:
                d = math.hypot(e.x - x, e.z - z)
                near = min(near, d)
                if d < 40.0 and not world.ray_blocked(x, z, e.x, e.z, EYE_STAND):
                    score -= 25.0                    # would be in plain view of somebody
            score += min(near, 40.0)
            if score > best_score:
                best, best_score = (x, z, yard), score
        if best is None:
            yard = random.choice(yards)
            pts = world.spawn.get(yard) or [(world.yard[yard][0] + 2, world.yard[yard][1] + 2)]
            x, z = random.choice(pts)
            best = (x, z, yard)
        return best

    def respawn(self, c, yards=None):
        yards = [y for y in (yards or list(self.world.yard.keys()) or ["A"]) if y in self.world.yard] \
            or list(self.world.yard.keys())
        x, z, yard = self._pick_spot(c, yards)
        c.x, c.z, c.yard = x, z, yard
        x0, z0, x1, z1 = self.world.yard[yard]
        c.yaw = math.atan2((z0 + z1) / 2 - z, (x0 + x1) / 2 - x) + random.uniform(-0.8, 0.8)
        c.alive = True
        c.ammo = c.mag_size
        c.reloading = False
        c.crouch_key = False
        c.force_crouch = False
        c.dying_until = 0.0
        c.flash_until = 0.0
        c.respawn_at = 0.0
        c.held = {}
        c.move_t = self.clock()
        c.vx = c.vz = 0.0
        c.protected_until = self.clock() + self.spawn_protection
        if getattr(c, "brain", None) is not None:
            c.brain = {}

    # ---------------------------------------------------------------- input
    def handle_key(self, c, k, t):
        """One key event from a human (t = when the server received it)."""
        if not c.alive:
            return
        if k == "UP":                                # arrow keys also walk forward / back
            k = "w"
        elif k == "DOWN":
            k = "s"
        lk = k.lower() if len(k) == 1 else k
        if lk == "SPACE":                            # emergency brake: stop all movement
            c.held = {}
            return
        if lk not in ("w", "a", "s", "d"):
            # Holding W and then tapping E / an arrow makes the keyboard stop repeating W, so
            # without this the player would halt on every shot or turn.  Carry the movement on.
            for mk in ("w", "a", "s", "d"):
                if c.held.get(mk, 0.0) > t:
                    c.held[mk] = max(c.held[mk], t + self.key_carry)
        last = c.key_last.get(lk, -99.0)
        gap = t - last
        c.key_last[lk] = t
        if lk in ("w", "a", "s", "d"):
            if gap < 0.12:                           # a real auto-repeat stream: learn its speed
                c.rep_gap = 0.8 * c.rep_gap + 0.2 * max(0.015, gap)
                hold = min(0.30, max(0.19, c.rep_gap * 3.0 + 0.10))
            elif gap < 0.9:                          # first auto-repeat after the typematic delay
                if 0.2 <= gap <= 0.7:
                    c.typ_delay = 0.7 * c.typ_delay + 0.3 * gap
                hold = 0.25
            else:                                    # fresh press: bridge the learned typematic delay
                hold = min(self.key_first, max(0.28, c.typ_delay + 0.07))
            c.held[lk] = t + hold
            c.held.pop(OPPOSITE[lk], None)
        elif lk in ("LEFT", "RIGHT"):
            if gap < 0.12:
                hold = min(self.turn_repeat, max(0.09, c.rep_gap * 3.0 + 0.03))
            else:
                hold = self.turn_repeat if gap < 0.9 else self.turn_first
            c.held[lk] = t + hold
            c.held.pop(OPPOSITE[lk], None)
        elif lk == "e":
            if gap >= FIRE_REPEAT_GAP:               # one round per press, not per auto-repeat
                self.try_fire(c, t)
        elif lk == "q":
            self.try_reload(c, t)
        elif lk == "c":
            if self.toggle_crouch:
                if gap >= 0.25:                      # ignore the terminal's auto-repeat
                    c.crouch_key = not c.crouch_key
            else:
                c.held["c"] = t + (self.key_repeat if gap < 0.9 else self.key_first)

    # ---------------------------------------------------------------- shooting / reloading
    def try_fire(self, c, now, aim_error=0.0):
        """aim_error (radians) biases the shot before the hitscan runs (bots miss like people)."""
        if not c.alive or c.reloading or c.ammo <= 0:
            return False
        if now - c.last_shot < self.fire_cd:
            return False
        c.last_shot = now
        c.ammo -= 1
        c.last_ammo_loss = now
        c.flash_until = now + 0.12
        self._hitscan(c, now, aim_error)
        return True

    def try_reload(self, c, now):
        if not c.alive or c.reloading or c.ammo >= c.mag_size:
            return False
        if now - c.last_ammo_loss < self.reload_cd:
            return False
        c.reloading = True
        c.reload_until = now + self.reload_time
        return True

    def _finish_reloads(self, now):
        for c in self.players:
            if c.reloading and now >= c.reload_until:
                c.reloading = False
                c.ammo = c.mag_size
                c.reloads += 1

    def _hitscan(self, shooter, now, aim_error=0.0):
        fyaw = shooter.yaw + aim_error
        fx, fz = math.cos(fyaw), math.sin(fyaw)
        best = None
        if shooter.is_bot:
            cone, slope, radius = 0.965, 0.14, HIT_RADIUS
        else:                                               # people get an invisible, slightly wider hit area
            cone, slope, radius = self.assist_cos, self.assist_slope, self.assist_radius
        for other in self.players:
            if other is shooter or not other.alive or other.protected_until > now:
                continue
            dx, dz = other.x - shooter.x, other.z - shooter.z
            dist = math.hypot(dx, dz)
            if dist < 0.05 or dist > 60.0:
                continue
            if (dx * fx + dz * fz) / dist < cone:           # a crosshair hit, not pixel-perfect
                continue
            if other.crouch and not shooter.crouch and dist > CROUCH_SHOOT_RANGE:
                continue
            lateral = abs(dx * -fz + dz * fx)
            if lateral > dist * slope + radius:
                continue
            y = min(shooter.eye, other.height - 0.15)
            if self.world.ray_blocked(shooter.x, shooter.z, other.x, other.z, y):
                continue
            if best is None or dist < best[1]:
                best = (other, dist)
        if best:
            target = best[0]
            target.alive = False
            target.dying_until = now + 0.35
            target.death_t = now
            target.respawn_at = now + self.respawn_time
            target.deaths += 1
            target.killed_by = shooter.name
            shooter.kills += 1
            self.feed.append((now, shooter.name, target.name))

    # ---------------------------------------------------------------- awareness
    def threats(self, c, now, fov_half, max_dist=26.0):
        """Enemies that could shoot c but are outside its field of view: [(bearing, dist)].
        bearing is signed radians from straight ahead, positive = to the right on screen,
        +-pi = directly behind.  Drives the red HUD exclamation marks."""
        fx, fz = math.cos(c.yaw), math.sin(c.yaw)
        rx, rz = math.sin(c.yaw), -math.cos(c.yaw)          # the renderer's screen-right
        out = []
        if c.protected_until > now:
            return out
        for o in self.players:
            if o is c or not o.alive or o.protected_until > now:
                continue
            dx, dz = o.x - c.x, o.z - c.z
            d = math.hypot(dx, dz)
            if d < 0.3 or d > max_dist:
                continue
            bearing = math.atan2(dx * rx + dz * rz, dx * fx + dz * fz)
            if abs(bearing) <= fov_half:
                continue                                     # already on screen
            if self.world.ray_blocked(o.x, o.z, c.x, c.z, min(o.eye, c.height - 0.15)):
                continue
            out.append((bearing, d))
        return out

    # ---------------------------------------------------------------- movement
    def apply_move(self, c, now, dt=None):
        """Advance one combatant by the REAL time since its last update, so it can be called
        from the tick loop and again right before a frame is drawn without double-moving."""
        if not c.alive:
            c.move_t = now
            return
        if dt is None:
            dt = now - c.move_t if c.move_t else 0.0
        c.move_t = now
        dt = max(0.0, min(0.1, dt))
        if dt <= 0.0:
            return
        speed = self.crouch_speed if c.crouch else self.walk
        mx = mz = 0.0
        fx, fz = math.cos(c.yaw), math.sin(c.yaw)
        rx, rz = -fz, fx
        rx, rz = -rx, -rz            # the renderer's screen-right is (sin, -cos): 'd' must go that way
        if c.is_held("w", now):
            mx += fx
            mz += fz
        if c.is_held("s", now):
            mx -= fx
            mz -= fz
        if c.is_held("a", now):
            mx -= rx
            mz -= rz
        if c.is_held("d", now):
            mx += rx
            mz += rz
        if c.is_held("LEFT", now):       # yaw grows counter-clockwise on screen
            c.yaw += self.turn_rate * dt
        if c.is_held("RIGHT", now):
            c.yaw -= self.turn_rate * dt
        m = math.hypot(mx, mz)
        tvx = tvz = 0.0
        if m > 1e-6:
            tvx, tvz = mx / m * speed, mz / m * speed
        # ease toward the wanted velocity: quick to get going, a little slower to stop
        ex, ez = tvx - c.vx, tvz - c.vz
        dist = math.hypot(ex, ez)
        if dist > 1e-9:
            rate = speed / (self.accel_time if m > 1e-6 else self.decel_time)
            step = min(dist, rate * dt)
            c.vx += ex / dist * step
            c.vz += ez / dist * step
        if abs(c.vx) + abs(c.vz) > 1e-3:
            c.x, c.z = self.world.move(c.x, c.z, c.vx * dt, c.vz * dt, c.height)
        c.force_crouch = self.world.headroom(c.x, c.z) < HGT_STAND - 0.02

    def tick(self, dt):
        now = self.clock()
        self._finish_reloads(now)
        for c in self.players:
            if not c.alive or c.is_bot:
                continue                      # bots drive their own movement and shooting (rr_bots)
            if not self.toggle_crouch:
                c.crouch_key = c.is_held("c", now)
            self.apply_move(c, now)
        for c in self.players:
            if not c.alive and c.respawn_at > 0 and now >= c.respawn_at:
                self.respawn(c)

    # ---------------------------------------------------------------- end of match
    def finalize(self):
        self.ended = True
        rows = []
        for c in self.players:
            rows.append((c.name, c.kills, c.deaths, c.kills / max(1, c.deaths), not c.is_bot))
        rows.sort(key=lambda r: (-r[3], -r[1], r[2], r[0].lower()))
        self.results = rows
        return rows
