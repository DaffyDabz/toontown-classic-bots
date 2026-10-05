"""PERSONAL SPACE (owner 10-04: in his screenshots toons stood inside each other).

Measured 10-04 on the sim copy: bot pairs 0.9-1.4 ft apart in Toontown Central (a toon is 1.5-2 ft wide), a bot
inside a toon in a Lighthouse Lane battle. Why: every stand-about pick (a spot round a landmark, a door, the trolley,
an elevator, a spot near the player, a new friend) chose its walk-map node without looking at who already stood
there or was on the way there, and a few stops are not walk-map nodes at all (a greeting stop on the way, the
Fisherman's counter, a battle's join point).

A bot keeps GAP ft between its centre and every other toon's where it stops:
  spotFor(bot, node)        BotToon.walkTo: `node` itself when nobody stands within GAP of it or walks to a spot
                            within GAP of it, else the nearest free walk-map node round it (along the map's own
                            edges, at most ROOM ft away); unchanged when nothing round it is free.
  freeNode(director, area, node, bot)   the same for a toon not standing in `area` yet (an arrival).
  freePoint(bot, points)    the first free one of a list of exact stops [(x, y, z)], else the first.
  settle(bot, now)          BotToon.tick, in an area a real player is in: a bot standing about (between activities,
                            a stroll, a hangout group, a street pause, a facility / boss elevator wait) with another
                            toon standing inside its space for SETTLE_AFTER s takes one short walk to the nearest
                            free node. Of two such bots the one that stopped there last steps; a bot never waits
                            for a real toon to move.
  sample(director, now)     proof sampler: while <run>/bots-gaps.on exists, every toon pair standing closer than
                            GAP (episodes: start, length, closest, who was doing what) and a census every 30 s go
                            to <run>/bots-gaps.jsonl; always: the counters and their cost in <run>/bots-space.json.
Doors: every client draws a toon coming out of a building door on one spot in front of it, so no stop is ever picked
on a door's way-out spot (doorSpots), one toon comes out of a door every DOOR_OUT s, a bot inside waits a moment
before coming out onto a toon (lifekit.Visit) and an arrival by door takes a door whose spot is clear (BotTravel).
Fishing: no stop is picked within SEAT_KEEP ft of a fishing spot (seatSpots); a toon getting up walks to a free node.
Battles: their toons stand at the battle's own spots (BattleBase toonPoints, as every client draws them), not where
they walked in; a toon that is not in a battle never stops within BATTLE_KEEP ft of its centre (the 9 ft join tube).
Walk paths are untouched: toons still walk past each other (a real toon does too); only where they stop counts.
"""
import json
import math
import os
import time
import traceback

from direct.directnotify import DirectNotifyGlobal

notify = DirectNotifyGlobal.directNotify.newCategory('BotSpace')

GAP = 2.5               # ft between two standing toons' centres
ROOM = 9.0              # ft a stop may move to find a free node (3 walk-map steps)
BATTLE_KEEP = 11.0      # ft from a battle's centre a toon not in it never stops (its 9 ft join tube + a toon)
DZ = 4.0                # ft apart in height = another floor (a bridge over a path): no clash
SETTLE_AFTER = 1.5      # s a clash lasts before the bot steps aside (a toon walking past is never stepped round)
SETTLE_EVERY = 1.0      # s between one bot's clash checks
KINDS = ('playground', 'street', 'coghq')
INDEX_TTL = 0.5         # s the who-stands-where index is reused (a pick is added to it at once, see _claim)
CELL = 5.0              # ft grid cells of the index (>= every keep-clear radius, so a 3 x 3 look covers it)
BATTLE_TTL = 1.0        # s the battle spots are reused
HIDDEN = ('board', 'boarding', 'seated', 'ride', 'hopoff')     # phases drawn on a trolley / in an elevator
STATS = {'picks': 0, 'moved': 0, 'kept': 0, 'arrivals': 0, 'points': 0, 'settles': 0, 'settleStuck': 0}

# activities whose bot only stands about: it may take a step aside (never a seat, a door, a battle, a fishing spot,
# a trolley or elevator queue that may board any moment, a follower, a command)
SETTLE = {'stroll': True, 'hangout': True, 'wander': True, 'facwait': True, 'lobbywait': True,
          'streetwalk': lambda act: getattr(act, 'visit', None) is None}

_IDX = {'t': -1e9, 'areas': {}, 'grids': {}, 'battles': {}, 'bt': -1e9}
PROF = {}               # what this costs: name -> [calls, seconds] (<run>/bots-space.json every 30 s)


def _now():
    return globalClock.getRealTime()


def _timed(name):
    def wrap(fn):
        def timed(*args, **kw):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kw)
            finally:
                p = PROF.get(name)
                if p is None:
                    p = PROF[name] = [0, 0.0]
                p[0] += 1
                p[1] += time.perf_counter() - t0
        timed.__doc__ = fn.__doc__
        return timed
    return wrap


def _cell(x, y):
    return int(x // CELL), int(y // CELL)


# ---- who stands where -------------------------------------------------------------------------------------------
class Entry:
    __slots__ = ('x', 'y', 'z', 'avId', 'bot', 'kind')

    def __init__(self, x, y, z, avId, bot, kind):
        self.x, self.y, self.z, self.avId, self.bot, self.kind = x, y, z, avId, bot, kind


def _battleSpots(p, s, members):
    """{avId: (x, y, z)} where a battle's toons stand (BattleBase.toonPoints / toonPendingPoints, turned to face
    the Cogs as battlebrain.myIndexPos does)."""
    from toontown.battle.BattleBase import BattleBase
    h = math.atan2(-(s[0] - p[0]), s[1] - p[1]) if s else 0.0
    c, sn = math.cos(h), math.sin(h)

    def at(pt):
        return (p[0] + pt[0] * c - pt[1] * sn, p[1] + pt[0] * sn + pt[1] * c, p[2])
    act = list(members.activeToons)[:4] or list(members.toons)[:4]
    out = {}
    for i, t in enumerate(act):
        out[t] = at(BattleBase.toonPoints[len(act) - 1][i][0])
    for i, t in enumerate(t for t in members.toons if t not in out):
        out[t] = at(BattleBase.toonPendingPoints[i % 4][0])
    return out


def battles(director, area):
    """[(x, y, z, {avId: spot})] of the battles in `area` (any of its location zones)."""
    now = _now()
    if now - _IDX['bt'] > BATTLE_TTL:
        _IDX['bt'] = now
        _IDX['battles'] = {}
    got = _IDX['battles'].get(area.id)
    if got is not None:
        return got
    return _battlesNow(director, area)


@_timed('battles')
def _battlesNow(director, area):
    from toontown.bots.battlebrain import Members
    out = []
    for z in area.zones:
        v = director.viewOf(z)
        if v is None:
            continue
        for o in v.objects.values():
            if o.className != 'DistributedBattle':
                continue
            p = o.get('setPosition')
            if not p:
                continue
            spots = {}
            m = o.get('setMembers')
            if m:
                try:
                    spots = _battleSpots(p, o.get('setInitialSuitPos'), Members(m))
                except Exception:
                    spots = {}
                    if not _IDX.get('spotErr'):
                        _IDX['spotErr'] = True
                        director.error('space battle spots', traceback.format_exc())
            out.append((p[0], p[1], p[2], spots))
    _IDX['battles'][area.id] = out
    return out


def index(director):
    """{area id: [Entry]} of every toon a client in that area sees standing ('stand': a bot with no walk left or a
    real toon; 'battle': a toon at its battle spot), every walking bot ('walk') and where it will stop ('goal')."""
    now = _now()
    if now - _IDX['t'] < INDEX_TTL:
        return _IDX['areas']
    return _rebuild(director, now)


@_timed('index')
def _rebuild(director, now):
    world = director.world
    areas = {}
    inBattle = set()

    def listOf(a):
        lst = areas.get(a.id)
        if lst is None:
            lst = areas[a.id] = []
            for bx, by, bz, spots in battles(director, a):
                for avId, s in spots.items():
                    inBattle.add(avId)
                    lst.append(Entry(s[0], s[1], s[2], avId, director.bots.get(avId), 'battle'))
        return lst

    for b in director.bots.values():
        a = b.area
        if b.state != 'present' or a is None or a.kind not in KINDS or world.zoneToArea.get(b.zoneId) is not a:
            continue
        lst = listOf(a)
        if b.avId in inBattle:
            continue
        act = b.activity
        if act is not None and getattr(act, 'phase', None) in HIDDEN:
            continue
        p = b.pos
        if b.path:
            lst.append(Entry(p[0], p[1], p[2], b.avId, b, 'walk'))
            g = b.path[-1]
            lst.append(Entry(g[0], g[1], g[2], b.avId, b, 'goal'))
        else:
            lst.append(Entry(p[0], p[1], p[2], b.avId, b, 'stand'))
    for avId, zone in list(director.players.items()):
        a = world.zoneToArea.get(zone)
        if a is None or a.kind not in KINDS:
            continue
        v = director.viewOf(zone)
        o = v.objects.get(avId) if v is not None else None
        if o is None:
            continue
        lst = listOf(a)
        if avId not in inBattle:
            lst.append(Entry(o.pos[0], o.pos[1], o.pos[2], avId, None, 'stand'))
    grids = {}
    for aid, lst in areas.items():
        g = grids[aid] = {}
        for e in lst:
            g.setdefault(_cell(e.x, e.y), []).append(e)
    _IDX['t'] = now
    _IDX['areas'] = areas
    _IDX['grids'] = grids
    return areas


def _near(director, area, x, y):
    """The index entries of `area` in the 3 x 3 cells round (x, y)."""
    index(director)
    g = _IDX['grids'].get(area.id)
    if not g:
        return ()
    cx, cy = _cell(x, y)
    out = []
    for i in (cx - 1, cx, cx + 1):
        for j in (cy - 1, cy, cy + 1):
            lst = g.get((i, j))
            if lst:
                out.extend(lst)
    return out


def doorSpots(area):
    """Where every client draws a toon coming out of each building door of `area` (DistributedDoor.avatarExitTrack:
    3 ft out from the door; here the door's step, 3 ft from it towards its walk-map node, and that node): door
    traffic, never a place to stand about."""
    got = getattr(area, '_doorSpots', None)
    if got is None:
        got = []
        for p in area.doors:
            x, y, z = p['pos']
            got.append((x, y, z))
            k = area.placeNode(p)
            if k is not None:
                nx, ny, nz = area.wm.pos(k)
                d = math.hypot(nx - x, ny - y)
                if d > 0.5:
                    got.append((x + (nx - x) * min(1.0, 3.0 / d), y + (ny - y) * min(1.0, 3.0 / d), nz))
                got.append((nx, ny, nz))
        area._doorSpots = got
        area._doorGrid = {}
        for sp in got:
            area._doorGrid.setdefault(_cell(sp[0], sp[1]), []).append(sp)
    return got


def _doorsNear(area, x, y):
    doorSpots(area)
    cx, cy = _cell(x, y)
    g = area._doorGrid
    return [sp for i in (cx - 1, cx, cx + 1) for j in (cy - 1, cy, cy + 1) for sp in g.get((i, j), ())]


SEAT_KEEP = GAP + 0.5   # ft kept clear round a fishing spot (the seated toon is drawn up to ~0.5 ft off the spot point)


def seatSpots(director, area):
    """(x, y, z) of every fishing spot in `area`: a seat, never a place to stand about (seated or not)."""
    now = _now()
    c = _IDX.setdefault('seats', {}).get(area.id)
    if c is not None and now - c[0] < BATTLE_TTL:
        return c[1]
    return _seatsNow(director, area, now)


@_timed('seats')
def _seatsNow(director, area, now):
    out = []
    for z in area.zones:
        v = director.viewOf(z)
        if v is None:
            continue
        for o in v.objects.values():
            if o.className == 'DistributedFishingSpot':
                out.append((o.pos[0], o.pos[1], o.pos[2]))
    _IDX['seats'][area.id] = (now, out)
    return out


DOOR_OUT = 3.0          # s one door's way-out spot is busy after a toon comes out of it (the exit walk + a moment)
_DOORS = {}             # door doId -> when its last toon came out


def doorFree(doId):
    """No toon has come out of this door in the last DOOR_OUT s."""
    return _now() - _DOORS.get(doId, -1e9) >= DOOR_OUT


def doorUsed(doId):
    _DOORS[doId] = _now()


def isFree(director, area, x, y, z, me=None, gap=GAP, keepOut=True, doors=False):
    """Nobody stands within `gap` ft of (x, y, z), nobody walks to a stop there, (keepOut, for a toon not in it) no
    battle's centre is within BATTLE_KEEP ft and (doors) it is not a door's way-out spot or a fishing seat."""
    mine = me.avId if me is not None else None
    g2 = gap * gap
    for e in _near(director, area, x, y):
        if e.avId == mine or e.kind == 'walk':
            continue
        if abs(e.z - z) < DZ and (e.x - x) ** 2 + (e.y - y) ** 2 < g2:
            return False
    if doors:
        for dx, dy, dz in _doorsNear(area, x, y):
            if abs(dz - z) < DZ and (dx - x) ** 2 + (dy - y) ** 2 < g2:
                return False
        s2 = SEAT_KEEP * SEAT_KEEP
        for sx, sy, sz in seatSpots(director, area):
            if abs(sz - z) < DZ and (sx - x) ** 2 + (sy - y) ** 2 < s2:
                return False
    if not keepOut:
        return True
    k2 = BATTLE_KEEP * BATTLE_KEEP
    for bx, by, bz, spots in battles(director, area):
        if mine in spots:
            continue
        if abs(bz - z) < DZ and (bx - x) ** 2 + (by - y) ** 2 < k2:
            return False
    return True


def clear(director, area, x, y, z, me=None):
    """isFree for callers outside this module: an error here never stops a bot (it counts as clear)."""
    try:
        return area is None or area.kind not in KINDS or isFree(director, area, x, y, z, me)
    except Exception:
        director.error('space clear', traceback.format_exc())
        return True


def _claim(director, area, x, y, z, bot):
    """A pick is a stop from now on: the next pick (before the index is rebuilt) sees it."""
    lst = index(director).get(area.id)
    if lst is not None and bot is not None:
        e = Entry(x, y, z, bot.avId, bot, 'goal')
        lst.append(e)
        _IDX['grids'].setdefault(area.id, {}).setdefault(_cell(x, y), []).append(e)


def freeNode(director, area, k, bot=None, room=ROOM):
    """`k` when it is free, else the nearest free node round it along the walk map's edges (<= room ft), else k."""
    if k is None or area is None or area.kind not in KINDS:
        return k
    try:
        return _freeNode(director, area, k, bot, room)
    except Exception:
        director.error('space freeNode', traceback.format_exc())
        return k


@_timed('freeNode')
def _freeNode(director, area, k, bot, room):
    wm = area.wm
    x0, y0, z0 = wm.pos(k)
    STATS['picks'] += 1
    if isFree(director, area, x0, y0, z0, bot, doors=True):
        _claim(director, area, x0, y0, z0, bot)
        return k
    seen = {k}
    ring = [k]
    r2 = room * room
    while ring:
        nxt = []
        for u in ring:
            for v in wm._nbr[u]:
                if v in seen:
                    continue
                seen.add(v)
                vx, vy, vz = wm.pos(v)
                if (vx - x0) ** 2 + (vy - y0) ** 2 <= r2:
                    nxt.append((((vx - x0) ** 2 + (vy - y0) ** 2), v, vx, vy, vz))
        nxt.sort()
        for d2, v, vx, vy, vz in nxt:
            if isFree(director, area, vx, vy, vz, bot, doors=True):
                STATS['moved'] += 1
                _claim(director, area, vx, vy, vz, bot)
                return v
        ring = [n[1] for n in nxt]
    STATS['kept'] += 1
    return k


def spotFor(bot, k):
    """BotToon.walkTo: where a bot heading for node k stops (k, or a free node a step or two round it)."""
    a = bot.area
    if a is None or bot.director.world.zoneToArea.get(bot.zoneId) is not a:
        return k
    return freeNode(bot.director, a, k, bot)


def freePoint(bot, points, keepOut=True):
    """The first free (x, y, z) of `points` (exact stops), else the first (keepOut=False: a battle's own join
    point, where only its toons count)."""
    a = bot.area
    if not points:
        return None
    if a is None or a.kind not in KINDS:
        return points[0]
    try:
        for p in points:
            if isFree(bot.director, a, p[0], p[1], p[2], bot, keepOut=keepOut):
                _claim(bot.director, a, p[0], p[1], p[2], bot)
                STATS['points'] += 1
                return p
    except Exception:
        bot.director.error('space freePoint', traceback.format_exc())
    return points[0]


# ---- stepping aside -------------------------------------------------------------------------------------------------
def mayStep(bot):
    act = bot.activity
    if act is None:
        return True
    rule = SETTLE.get(act.name)
    if rule is None or getattr(act, 'holdsPose', False):
        return False
    return rule is True or bool(rule(act))


def stoodAt(bot):
    return max(getattr(bot, 'stoodAt', 0.0), getattr(bot, 'arrivedAt', 0.0))


def clash(bot):
    """The toon standing inside this bot's space that it should make room for (a real toon, a bot that cannot step,
    a bot that stood there first), the battle it stands in without being in it, or None."""
    d, a = bot.director, bot.area
    x, y, z = bot.pos
    g2 = GAP * GAP
    mine = stoodAt(bot)
    for e in _near(d, a, x, y):
        if e.avId == bot.avId or e.kind not in ('stand', 'battle'):
            continue
        if abs(e.z - z) >= DZ or (e.x - x) ** 2 + (e.y - y) ** 2 >= g2:
            continue
        o = e.bot
        if o is not None and e.kind == 'stand' and mayStep(o):
            theirs = stoodAt(o)
            if theirs > mine + 0.05 or (abs(theirs - mine) <= 0.05 and o.avId > bot.avId):
                continue                   # it came last: it steps
        return e
    k2 = BATTLE_KEEP * BATTLE_KEEP
    for bx, by, bz, spots in battles(d, a):
        if bot.avId not in spots and abs(bz - z) < DZ and (bx - x) ** 2 + (by - y) ** 2 < k2:
            return Entry(bx, by, bz, 0, None, 'battle-circle')
    return None


def settle(bot, now):
    if now < getattr(bot, '_settleAt', 0.0):
        return
    bot._settleAt = now + SETTLE_EVERY
    if not bot.director.watched(bot.area):
        bot._clashSince = 0.0        # nobody real here to see it (the picks still keep the gap): no checks
        return
    _settle(bot, now)


@_timed('settle')
def _settle(bot, now):
    a = bot.area
    if bot.state != 'present' or bot.travel is not None or bot.path or a is None or a.kind not in KINDS \
            or bot.director.world.zoneToArea.get(bot.zoneId) is not a or not mayStep(bot):
        bot._clashSince = 0.0
        return
    try:
        e = clash(bot)
        if e is None:
            bot._clashSince = 0.0
            return
        if not getattr(bot, '_clashSince', 0.0):
            bot._clashSince = now
            return
        if now - bot._clashSince < SETTLE_AFTER:
            return
        bot._clashSince = 0.0
        wm = a.wm
        here = wm.nearestNode(bot.pos[0], bot.pos[1], bot.pos[2])
        # out of a battle's circle takes a longer walk than out of a toon's space
        k = freeNode(bot.director, a, here, bot, room=BATTLE_KEEP + 9.0 if e.kind == 'battle-circle' else ROOM)
        if k is None or not isFree(bot.director, a, *wm.pos(k), me=bot):
            STATS['settleStuck'] += 1
            return
        STATS['settles'] += 1
        notify.debug('[TTBOTS-SPACE] %s steps aside from %s (%s) to node %s' % (bot.avId, e.avId, e.kind, k))
        from toontown.bots.BotToon import WALK_SPEED
        if not bot.walkTo(k, WALK_SPEED, 'walk', exact=True) or not bot.path:
            bot.path = [wm.pos(k) + (k,)]         # stopped off the map's nodes: the last foot or two straight
            bot.speed = WALK_SPEED
            bot.setAnim('walk')
    except Exception:
        bot.director.error('space settle', traceback.format_exc())


# ---- proof sampler --------------------------------------------------------------------------------------------------
_S = {'open': {}, 'last': {}, 'census': 0.0, 'rows': []}


def label(director, avId):
    b = director.bots.get(avId)
    if b is None:
        return 'REAL'
    act = b.activity
    if act is None:
        return 'free' if b.travel is None else 'travel:%s' % b.travel.phase
    v = getattr(act, 'visit', None)
    ph = v.phase if v is not None else getattr(act, 'phase', None)
    return '%s:%s' % (act.name, ph if ph is not None else getattr(act, 'label', ''))


def sample(director, now):
    """While <run>/bots-gaps.on exists, once a director second: every pair of toons standing still closer than GAP
    ft (an episode line when it ends: area, start, seconds, closest ft, both toons' doings) and a census every 30 s."""
    run = director.runDir
    if now - _S.get('prof', 0.0) >= 30.0:
        _S['prof'] = now
        try:
            path = os.path.join(run, 'bots-space.json')
            with open(path + '.new', 'w') as f:
                json.dump({'t': round(now, 1), 'stats': STATS,
                           'prof': {k: [v[0], round(v[1] * 1000.0, 1)] for k, v in PROF.items()}}, f)
            os.replace(path + '.new', path)
        except Exception:
            director.error('space stats file', traceback.format_exc())
    if not os.path.exists(os.path.join(run, 'bots-gaps.on')):
        if _S['open']:
            _S['open'] = {}
        return
    try:
        idx = index(director)
        last = _S['last']
        still = {}
        census = {}
        pairs = set()
        for aid, ents in idx.items():
            pts = []
            for e in ents:
                if e.kind not in ('stand', 'battle'):
                    continue
                prev = last.get(e.avId)
                last[e.avId] = (e.x, e.y, now)
                if e.bot is None and e.kind == 'stand' and (prev is None or math.hypot(prev[0] - e.x, prev[1] - e.y) > 0.3):
                    continue                   # a real toon on the move
                pts.append(e)
            n1 = n2 = 0
            for i in range(len(pts)):
                p = pts[i]
                for q in pts[i + 1:]:
                    if abs(p.z - q.z) >= DZ or (p.kind == 'battle' and q.kind == 'battle'):
                        continue           # (two toons of one battle stand where the game puts them)
                    dd = math.hypot(p.x - q.x, p.y - q.y)
                    if dd >= GAP:
                        continue
                    n1 += 1
                    if dd < 1.5:
                        n2 += 1
                    key = (min(p.avId, q.avId), max(p.avId, q.avId))
                    pairs.add(key)
                    ep = _S['open'].get(key)
                    if ep is None:
                        _S['open'][key] = [now, dd, aid, label(director, key[0]), label(director, key[1]),
                                           now, p.kind + '/' + q.kind]
                    else:
                        ep[1] = min(ep[1], dd)
                        ep[5] = now
            census[aid] = [len(pts), n1, n2]
        rows = _S['rows']
        for key in [k for k in _S['open'] if k not in pairs]:
            ep = _S['open'].pop(key)
            rows.append({'t': round(ep[0], 1), 'dur': round(ep[5] - ep[0] + 1.0, 1), 'area': ep[2], 'a': key[0],
                         'b': key[1], 'min': round(ep[1], 2), 'la': ep[3], 'lb': ep[4], 'kinds': ep[6]})
        for k in [k for k, v in last.items() if now - v[2] > 30.0]:
            del last[k]
        if now - _S['census'] >= 30.0:
            _S['census'] = now
            rows.append({'t': round(now, 1), 'census': {str(k): v for k, v in census.items() if v[0]},
                         'stats': dict(STATS), 'players': sorted(director.players.items())})
        if rows:
            with open(os.path.join(run, 'bots-gaps.jsonl'), 'a') as f:
                f.write(''.join(json.dumps(r) + '\n' for r in rows))
            _S['rows'] = []
    except Exception:
        director.error('space sample', traceback.format_exc())
