# -*- coding: utf-8 -*-
"""
Retro Recoil - bot AI for Bot Practice lobbies.

The first version of the bots killed a standing-still player in under half a second:
they saw through the whole yard instantly, had zero reaction time, perfect aim and a
huge hit cone, fired 12 times a second, moved and turned ~2.4x too fast (a per-think
constant was used as the time step on every tick) and nothing protected a fresh spawn.

These bots behave like people:
  * a field of view (about 120 degrees) plus a short "hearing" radius, line of sight required
  * a reaction time (cfg game.bot_reaction_min..max) between first spotting you and the first shot
  * aim wobble that grows with range and settles while they keep you in view
  * a limited shot rate (cfg game.bot_fire_rate), reloads, strafing, now and then crouching
  * chase the last place they saw you, otherwise roam the nav grid and the hide pockets
All motion uses the real elapsed time (dt), at slightly below human walking speed.
"""
import math
import random

from rr_world import HGT_STAND, RADIUS
BOT_WALK, BOT_CROUCH = 3.1, 1.7      # absolute (the player's own speed is configurable)

SIGHT_RANGE = 24.0
HEAR_RANGE = 4.0
FOV_HALF = math.radians(60.0)
ENGAGE_MIN, ENGAGE_MAX = 6.0, 14.0
PERCEIVE_DT = 0.15
BOT_TURN_RATE = 2.8          # rad/s
BOT_SPEED = 1.0


def _ang_diff(a, b):
    return (a - b + math.pi) % (2 * math.pi) - math.pi


def _behind(tgt, bot):
    """True when the bot is behind tgt (tgt has its back to the bot)."""
    dx, dz = bot.x - tgt.x, bot.z - tgt.z
    d = math.hypot(dx, dz) or 1.0
    return (dx * math.cos(tgt.yaw) + dz * math.sin(tgt.yaw)) / d < -0.17


def bot_think(bot, match, now, dt):
    if not bot.alive:
        bot.brain = {}
        return
    b = bot.brain
    cfg = match.cfg
    if now >= b.get("next_perceive", 0.0):
        b["next_perceive"] = now + PERCEIVE_DT
        _perceive(bot, match, now, cfg)
    _move(bot, match, now, dt)
    _fight(bot, match, now, dt, cfg)
    # reload when low (or empty) and not currently being shot at / shooting
    if not bot.reloading and (bot.ammo == 0 or (bot.ammo <= 3 and not b.get("target"))):
        match.try_reload(bot, now)


# ---------------------------------------------------------------- perception
def _perceive(bot, match, now, cfg):
    b = bot.brain
    world = match.world
    best = None
    for other in match.players:
        if other is bot or not other.alive or other.protected_until > now:
            continue                      # nobody can target a freshly spawned (protected) player
        dx, dz = other.x - bot.x, other.z - bot.z
        d = math.hypot(dx, dz)
        if d > SIGHT_RANGE:
            continue
        if d > HEAR_RANGE and abs(_ang_diff(math.atan2(dz, dx), bot.yaw)) > FOV_HALF:
            continue
        # a crouched target is harder to notice from far away
        if other.crouch and d > 16.0:
            continue
        if world.ray_blocked(bot.x, bot.z, other.x, other.z, bot.eye):
            continue
        if best is None or d < best[0]:
            best = (d, other)
    prev = b.get("target")
    if best:
        d, tgt = best
        if tgt is not prev:
            lo = float(cfg.game_bot_reaction_min)
            hi = max(lo, float(cfg.game_bot_reaction_max))
            b["react_at"] = now + random.uniform(lo, hi)
            b["seen_at"] = now
            if _behind(tgt, bot):                    # shooting somebody in the back: be slower about it
                b["react_at"] += float(cfg.game_bot_behind_delay) * random.uniform(0.7, 1.3)
        b["target"] = tgt
        b["dist"] = d
        b["last_seen"] = (tgt.x, tgt.z)
        b["last_seen_t"] = now
        if now >= b.get("crouch_decide", 0.0):
            b["crouch_decide"] = now + random.uniform(1.2, 3.0)
            bot.crouch_key = (d < ENGAGE_MAX and random.random() < 0.28)
    else:
        if prev is not None:
            b["lost_at"] = now
        b["target"] = None
        if now >= b.get("crouch_decide", 0.0):
            bot.crouch_key = False


# ---------------------------------------------------------------- movement
def _goto(bot, match, now, gx, gz, repath=0.6):
    b = bot.brain
    nav = match.world.nav_grid(False)
    g = b.get("goal")
    if (not b.get("path")) or g is None or math.hypot(g[0] - gx, g[1] - gz) > 1.5 or now - b.get("path_t", 0.0) > 2.5:
        if now - b.get("path_t", 0.0) >= repath or not b.get("path"):
            b["goal"] = (gx, gz)
            b["path"] = nav.path(bot.x, bot.z, gx, gz)
            b["path_t"] = now


def _step(bot, match, mx, mz, speed, dt):
    h = bot.height
    nx, nz = match.world.move(bot.x, bot.z, mx * speed * dt, mz * speed * dt, h)
    bot.x, bot.z = nx, nz


def _move(bot, match, now, dt):
    b = bot.brain
    world = match.world
    speed = (BOT_CROUCH if bot.crouch else BOT_WALK) * BOT_SPEED
    tgt = b.get("target")
    moving = False
    if tgt is not None:
        d = b.get("dist", 99.0)
        dx, dz = tgt.x - bot.x, tgt.z - bot.z
        n = math.hypot(dx, dz) or 1.0
        ux, uz = dx / n, dz / n
        if d > ENGAGE_MAX:
            _goto(bot, match, now, tgt.x, tgt.z)
        elif d < ENGAGE_MIN:
            b["path"] = []
            _step(bot, match, -ux, -uz, speed * 0.8, dt)
            moving = True
        else:
            b["path"] = []
            if now >= b.get("strafe_until", 0.0):
                b["strafe_until"] = now + random.uniform(0.5, 1.5)
                b["strafe_dir"] = random.choice((-1, 0, 1))
            sd = b.get("strafe_dir", 0)
            if sd:
                _step(bot, match, -uz * sd, ux * sd, speed * 0.7, dt)
                moving = True
    else:
        ls = b.get("last_seen")
        if ls and now - b.get("last_seen_t", -99.0) < 5.0 and math.hypot(ls[0] - bot.x, ls[1] - bot.z) > 1.5:
            _goto(bot, match, now, ls[0], ls[1])
        else:
            b["last_seen"] = None
            if now < b.get("idle_until", 0.0):
                pass
            elif not b.get("path"):
                if random.random() < 0.15:
                    b["idle_until"] = now + random.uniform(0.6, 2.0)
                    b["look_to"] = bot.yaw + random.uniform(-1.6, 1.6)
                else:
                    nav = world.nav_grid(False)
                    if world.hide_points and random.random() < 0.18:
                        gx, gz = random.choice(world.hide_points)
                    elif nav.cells:
                        gx, gz = nav.center(random.choice(nav.cells))
                    else:
                        gx = gz = None
                    if gx is not None:
                        b["goal"] = (gx, gz)
                        b["path"] = nav.path(bot.x, bot.z, gx, gz)
                        b["path_t"] = now
    path = b.get("path")
    if path and (tgt is None or b.get("dist", 0) > ENGAGE_MAX):
        wx, wz = path[0]
        dx, dz = wx - bot.x, wz - bot.z
        d = math.hypot(dx, dz)
        if d < 0.4:
            path.pop(0)
        else:
            ox, oz = bot.x, bot.z
            _step(bot, match, dx / d, dz / d, speed, dt)
            moving = True
            if tgt is None:
                want = math.atan2(dz, dx)
                step = max(-BOT_TURN_RATE * dt, min(BOT_TURN_RATE * dt, _ang_diff(want, bot.yaw)))
                bot.yaw += step
            # stuck detection: not getting anywhere for a second -> forget the path
            st = b.setdefault("stuck", [now, ox, oz])
            if now - st[0] > 1.0:
                if math.hypot(bot.x - st[1], bot.z - st[2]) < 0.25:
                    b["path"] = []
                    b["goal"] = None
                b["stuck"] = [now, bot.x, bot.z]
    elif tgt is None and now < b.get("idle_until", 0.0) and "look_to" in b:
        step = max(-1.2 * dt, min(1.2 * dt, _ang_diff(b["look_to"], bot.yaw)))
        bot.yaw += step
    bot.force_crouch = world.headroom(bot.x, bot.z) < HGT_STAND - 0.02


# ---------------------------------------------------------------- aiming / shooting
def _fight(bot, match, now, dt, cfg):
    b = bot.brain
    tgt = b.get("target")
    if tgt is None or not tgt.alive:
        return
    desired = math.atan2(tgt.z - bot.z, tgt.x - bot.x)
    err = _ang_diff(desired, bot.yaw)
    # Start turning toward the target immediately (people do) but only shoot after the reaction time
    step = max(-BOT_TURN_RATE * dt, min(BOT_TURN_RATE * dt, err))
    bot.yaw += step
    if now < b.get("react_at", 0.0) or bot.reloading or bot.ammo <= 0:
        return
    if abs(_ang_diff(desired, bot.yaw)) > 0.09:
        return
    if now < b.get("next_shot", 0.0):
        return
    rate = max(0.2, float(cfg.game_bot_fire_rate))
    b["next_shot"] = now + random.uniform(0.75, 1.5) / rate
    # aim wobble: grows with range, settles the longer they have you in view
    base = math.radians(float(cfg.game_bot_aim_error_deg))
    settle = 1.0 - 0.45 * min(1.0, (now - b.get("react_at", now)) / 2.0)
    sigma = base * (0.5 + b.get("dist", 10.0) / 20.0) * settle * (1.25 if tgt.crouch else 1.0)
    if _behind(tgt, bot):
        sigma *= float(cfg.game_bot_behind_aim_mult)
    match.try_fire(bot, now, random.gauss(0.0, sigma))
