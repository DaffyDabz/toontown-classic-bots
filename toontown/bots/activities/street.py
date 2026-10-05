"""P7 STREETS: a toon walking a street from one tunnel to the other, like a player doing ToonTasks.

  streetwalk  from the end it stands at, it walks the sidewalks building front to building front
              (the doors, in order along the street) to the far tunnel; now and then it goes INTO a
              toon building (real door protocol, talks to the shopkeeper sometimes, see lifekit.Visit),
              stops for a line or an emote; at the far tunnel it goes on (director.travel via='tunnel')
              into the next street or playground, or turns back when that place is full.
  Cogs: a bot never walks into a Cog or a street battle. The street's Cogs are replayed from the
  street DNA with the clients' own SuitLegList (lifekit.SuitWatch); a Cog on the bot's next few
  feet stops it, it waits for the Cog to pass and steps aside if the Cog comes at it. (A bot has no
  client, so touching a Cog would not start a battle anyway, but a player would see it walk through.)
  STREET LIFE (owner 09-25): a BATTLE is never waited out: the toon starts its own fight, heads for a Cog
  building, runs round the battle or turns back (__battleAhead); no stand-about spot near a battle.
"""
import heapq
import math
import random

from toontown.bots.activities import Activity, register
from toontown.bots.activities import lifekit as kit
from toontown.bots.BotToon import RUN_SPEED, WALK_SPEED, tripSpeed

_DIST = {}          # (area id, tunnel name) -> [walk distance from that tunnel per node]
NEAR_BATTLE = 60.0  # STREET LIFE: no standing about this close to a battle a bot is not in (owner 09-25)


def battlesOn(director, area):
    """(x, y, radius) of the street battles going on (lifekit.SuitWatch hazards: a Cog is 11 ft, a battle 20 ft)."""
    return [h for h in kit.SUITS.hazards(director, area) if h[2] >= 15.0]


def nearBattle(director, area, x, y, r=NEAR_BATTLE):
    return any(math.hypot(x - h[0], y - h[1]) < r for h in battlesOn(director, area))


def battleOf(director, area, hit):
    """The battle a hazard belongs to: a battle's circle, or one of the Cogs standing in a battle (they are
    Cog hazards too, and were dodged like a walking Cog: stop, face it, wait, try again)."""
    if hit[2] >= 15.0:
        return hit
    for h in battlesOn(director, area):
        if math.hypot(hit[0] - h[0], hit[1] - h[1]) < h[2] + 5.0:
            return h
    return None


def distFrom(area, place):
    key = (area.id, place['name'])
    d = _DIST.get(key)
    if d is None:
        wm = area.wm
        src = area.placeNode(place)
        d = [float('inf')] * wm.nodeCount
        if src is not None:
            d[src] = 0.0
            q = [(0.0, src)]
            pos = wm.pos
            while q:
                du, u = heapq.heappop(q)
                if du > d[u]:
                    continue
                ux, uy, uz = pos(u)
                for v in wm._nbr[u]:
                    vx, vy, vz = pos(v)
                    nd = du + math.sqrt((vx - ux) ** 2 + (vy - uy) ** 2 + (vz - uz) ** 2)
                    if nd < d[v]:
                        d[v] = nd
                        heapq.heappush(q, (nd, v))
        _DIST[key] = d
    return d


@register
class StreetWalk(Activity):
    name = 'streetwalk'
    weight = 5.0
    kinds = ('street',)
    chatty = True
    label = 'street-walking'

    @classmethod
    def canRun(cls, bot):
        return bot.area is not None and bot.node is not None and len(bot.area.tunnels) >= 1

    def start(self):
        kit.LIFE.ensureTask(self.director)
        a, bot = self.bot.area, self.bot
        tunnels = [(nb, p) for nb, p in a.tunnels.items() if self.director.canWalkTo(bot, p)]
        if not tunnels:
            return False
        # the end I stand nearer to is where I came from; the other end is where I am going
        near = min(tunnels, key=lambda t: distFrom(a, t[1])[bot.node])
        far = [t for t in tunnels if t is not near]
        self.turned = False
        self.visit = None
        self.pauseUntil = 0.0
        self.waitSuitUntil = 0.0
        self.avoids = 0
        self.walkUntil = 0.0
        self.speed = tripSpeed()[0]            # owner 09-25: players ran almost everywhere (85% run)
        if far:
            self.fromT, self.toT = near, random.choice(far)
        else:
            self.fromT, self.toT = near, near
        self.__route()
        return self.__next()

    def __route(self):
        """Doors ahead of me, in walking order along the street, then the far tunnel."""
        a, bot = self.bot.area, self.bot
        dFrom = distFrom(a, self.fromT[1])
        dTo = distFrom(a, self.toT[1])
        here = dTo[bot.node]
        ahead = []
        for p in a.doors:
            k = a.placeNode(p)
            if k is None or dTo[k] == float('inf') or dTo[k] >= here:
                continue
            ahead.append((-dTo[k], p))
        ahead.sort(key=lambda t: t[0])
        # not every door: a stop every few buildings, the rest is walking past
        self.route = [p for _, p in ahead if random.random() < 0.45]
        near = None if getattr(self, 'noNear', False) else self.__nearPlayer()
        self.nearMode = False
        if near is not None and (getattr(self, 'forceNear', False) or random.random() < 0.75):   # POP: director recall
            # a real player is on this street: busy where he can see it (his visgroups show only a
            # stretch of the street): a spot or two near him (stand about there, as toons did at the
            # tunnel mouths), the buildings round him, then on
            self.nearMode = True
            px, py = near
            round_ = [p for p in a.doors if a.placeNode(p) is not None and dTo[a.placeNode(p)] < float('inf')
                      and math.hypot(p['pos'][0] - px, p['pos'][1] - py) < 200.0]
            pick = random.sample(round_, min(len(round_), random.randint(1, 3))) if round_ else []
            # STREET LIFE (owner 09-25): never a spot to stand about near a battle (his, when he is fighting,
            # or anyone's): the toons round a fight run past it, go into the buildings, start their own
            for _ in range(random.randint(1, 2)):
                k = a.nodeNear(px, py, random.uniform(15.0, 70.0))
                if k is not None and a._comp[k] == a._comp[bot.node] and dTo[k] < float('inf') \
                        and not nearBattle(self.director, a, *a.wm.pos(k)[:2]):
                    pick.append({'kind': 'spot', 'name': 'spot', 'pos': list(a.wm.pos(k)), 'node': k, 'extra': {}})
            pick.sort(key=lambda p: -dTo[a.placeNode(p)])
            self.route = pick + self.route[-1:]
            kit.LIFE.count('street_near_player')
        self.route.append(self.toT[1])
        self.fromD = dFrom

    def __nearPlayer(self):
        a = self.bot.area
        for avId in a.players:
            z = self.director.players.get(avId)
            v = self.director.viewOf(z)
            o = v.objects.get(avId) if v is not None else None
            if o is not None:
                return (o.pos[0], o.pos[1])
        return None

    def __next(self):
        bot, a = self.bot, self.bot.area
        while self.route:
            p = self.route[0]
            k = a.placeNode(p)
            if k is not None and bot.walkTo(k, self.speed):
                self.target = p
                self.walkUntil = globalClock.getRealTime() + 120.0
                self.label = 'street-walking'
                return True
            self.route.pop(0)
        return False

    # ---- the tick ------------------------------------------------------------------------------------
    def step(self, now):
        bot, a = self.bot, self.bot.area
        if self.visit is not None and self.visit.phase == 'walk' and kit.SUITS.blocked(self.director, a, bot):
            self.visit = None                      # nothing sent yet: drop the visit and let the Cog pass
            self.releaseAll()
        if self.visit is not None:
            ok = self.visit.step(now)
            self.label = self.visit.label
            if not ok:
                self.visit = None
                self.releaseAll()
                self.label = 'street-walking'
                self.pauseUntil = now + random.uniform(1.0, 3.0)
            return True
        if now < self.waitSuitUntil:
            return True
        if bot.path:
            hit = kit.SUITS.blocked(self.director, a, bot)
            if hit is not None:
                fight = battleOf(self.director, a, hit)
                if fight is not None:
                    return self.__battleAhead(now, fight)
                self.__dodge(now, hit)
                return True
            if now > self.walkUntil:
                bot.stopWalking()
                return False
            return True
        if self.waitSuitUntil:
            self.waitSuitUntil = 0.0
            hit = kit.SUITS.blocked(self.director, a, bot, ahead=0.0)
            if hit is not None and self.avoids < 12:
                self.__dodge(now, hit)
                return True
            return self.__resume()
        if self.pauseUntil:
            hit = kit.SUITS.blocked(self.director, a, bot, ahead=0.0)
            if hit is not None and self.avoids < 30:
                self.pauseUntil = 0.0
                self.__dodge(now, hit)
                return True
            if self.label == 'idle' and self.pauseUntil - now > 3.0 and nearBattle(self.director, a, bot.pos[0], bot.pos[1]):
                self.pauseUntil = now + random.uniform(0.5, 3.0)     # STREET LIFE: a fight started by my spot
            if now < self.pauseUntil:
                return True
            self.pauseUntil = 0.0
            return self.__resume()
        # arrived at a stop
        p = self.target
        if p['kind'] == 'tunnel':
            return self.__atTunnel(now)
        if self.route:                              # a dodge / re-route can empty it under a stale target
            self.route.pop(0)
        if p['kind'] == 'pass':
            return self.__resume()                  # STREET LIFE: stepped clear of a battle: straight on
        if p['kind'] == 'spot':
            # standing about near the player for a while: a line, an emote, turn to him
            self.label = 'idle'
            self.pauseUntil = now + random.uniform(12.0, 35.0)
            if nearBattle(self.director, a, bot.pos[0], bot.pos[1]):
                self.pauseUntil = now + random.uniform(1.0, 3.0)     # STREET LIFE: a fight started here: move on
            near = self.__nearPlayer()
            if near is not None:
                bot.faceTo(near)
            if random.random() < 0.5:
                kit.say(bot, random.choice(kit.STREET + kit.GREET), now)
            elif random.random() < 0.4:
                kit.emote(bot, now=now)
            return True
        if not getattr(self, 'nearMode', False) and not getattr(self, 'noNear', False) \
                and self.__nearPlayer() is not None and random.random() < 0.6:
            self.__route()                     # he just came onto this street: head his way
            return self.__next()
        r = random.random()
        if r < 0.35:
            ex = p['extra']
            doId = self.director.doorDOs.get((a.id, ex['block'], ex.get('door', 0)))
            if doId is not None:
                zone = kit.interiorZone(self.director.world.doorZone(a, p), ex['block'])
                cap = kit.CAP.get(p['kind'], 2)
                slot = next((i for i in range(cap) if self.claim(('interior', zone, i))), None)
                if slot is not None:
                    v = kit.Visit(self, p, doId, stay=random.uniform(12.0, 35.0))
                    # already at the door: begin() walks zero steps
                    if v.begin():
                        self.visit = v
                        return True
                    self.releaseAll()
        self.pauseUntil = now + random.uniform(0.8, 4.0)
        if random.random() < 0.18:
            kit.say(bot, random.choice(kit.STREET), now)
        elif random.random() < 0.10:
            kit.emote(bot, now=now)
        return True

    def __resume(self):
        return self.__next()

    def __battleAhead(self, now, hit):
        """STREET LIFE (owner 09-25): a battle on my way. It was a Cog dodge: stop, face the battle, wait, walk
        the same path into it again, for as long as the fight lasted (a ring of toons staring at it). Now: a
        toon that can fight goes and starts its OWN fight now and then (a free slot in a real player's battle
        is the help hook's job) or heads for a Cog building, else it runs round the battle, else it turns and
        runs the street back."""
        bot = self.bot
        if math.hypot(bot.pos[0] - hit[0], bot.pos[1] - hit[1]) < hit[2] + 2.0:
            self.__dodge(now, hit)          # inside its circle (it started round me): hop out first
            return True
        kit.LIFE.count('battle_ahead')
        from toontown.bots.activities.battle import StreetBattle
        if random.random() < 0.4 and StreetBattle.runsIn(bot):
            if random.random() < 0.5:
                kit.say(bot, random.choice((1102, 1103, 514, 508)), now)      # Classic SpeedChat lines
            if bot.startActivity(StreetBattle(bot)):
                kit.LIFE.count('battle_ahead_fight')
            return True          # this walk is over either way (a failed start leaves the bot free)
        from toontown.bots.activities.cogbuilding import CogBuilding
        if random.random() < 0.2 and CogBuilding.runsIn(bot):
            if bot.startActivity(CogBuilding(bot)):      # its leader says "Let's go take over a Cog building!"
                kit.LIFE.count('battle_ahead_building')
            return True
        if self.__roundBattle(hit):
            kit.LIFE.count('battle_ahead_round')
            return True
        self.aheadN = getattr(self, 'aheadN', 0) + 1
        if self.aheadN == 1:
            kit.LIFE.count('battle_ahead_back')
            self.noNear = True
            self.fromT, self.toT = self.toT, self.fromT
            self.__route()
            if self.__next() and kit.SUITS.blocked(self.director, bot.area, bot) is None:
                return True
        # the way back runs past the battle too (it stands by my sidewalk), or I met it twice: run to a node
        # clear of it and end this walk there (the next pick starts from a clear spot)
        kit.LIFE.count('battle_ahead_clear')
        k = self.__clearOf(hit)
        if k is None:
            bot.stopWalking()
            return False
        self.route = [{'kind': 'pass', 'name': 'pass', 'pos': list(bot.area.wm.pos(k)), 'node': k, 'extra': {}}]
        return self.__next()

    def __clearOf(self, hit):
        """A node 30-45 ft from the battle on my side of it, walkable without crossing its circle."""
        bot, a = self.bot, self.bot.area
        hx, hy, r = hit
        wm = a.wm
        away = math.atan2(bot.pos[1] - hy, bot.pos[0] - hx)
        for _ in range(8):
            ang = away + random.uniform(-1.2, 1.2)
            rr = random.uniform(r + 10.0, r + 25.0)
            k = wm.nearestNode(hx + rr * math.cos(ang), hy + rr * math.sin(ang), maxDist=6.0)
            if k is None or not a.onGround(k) or a._comp[k] != a._comp[bot.node]:
                continue
            ns = wm.pathNodes(bot.node, k)
            if ns and all(math.hypot(wm.pos(n)[0] - hx, wm.pos(n)[1] - hy) >= r + 1.0 for n in ns[1:]):
                return k
        return None

    def __roundBattle(self, hit):
        """A walk-map way past the battle's circle to my next stop: a node beside it, both legs clear."""
        bot, a = self.bot, self.bot.area
        hx, hy, r = hit
        wm = a.wm
        tk = a.placeNode(self.target) if getattr(self, 'target', None) else None
        if tk is None or bot.node is None:
            return False

        def clear(ns):
            return all(math.hypot(wm.pos(n)[0] - hx, wm.pos(n)[1] - hy) >= r + 1.0 for n in ns)
        for _ in range(6):
            ang = random.uniform(0.0, 2.0 * math.pi)
            rr = random.uniform(r + 5.0, r + 15.0)
            k = wm.nearestNode(hx + rr * math.cos(ang), hy + rr * math.sin(ang), maxDist=6.0)
            if k is None or not a.onGround(k) or a._comp[k] != a._comp[bot.node]:
                continue
            p1, p2 = wm.pathNodes(bot.node, k), wm.pathNodes(k, tk)
            if p1 and p2 and clear(p1[1:]) and clear(p2) and bot.walkTo(k, self.speed):
                bot.path.extend(wm.pos(n) + (n,) for n in p2[1:])
                self.walkUntil = globalClock.getRealTime() + 120.0
                return True
        return False

    def __dodge(self, now, hit):
        """A Cog ahead: stop and let it pass; if it is at me, step away from it."""
        bot, a = self.bot, self.bot.area
        self.avoids += 1
        kit.LIFE.count('suit_avoid')
        hx, hy, r = hit
        d = math.hypot(bot.pos[0] - hx, bot.pos[1] - hy)
        bot.stopWalking()
        if d < r + 2.0:
            # step away: the ground node farthest from the Cog among a few near me
            best, bd = None, d
            for _ in range(8):
                k = a.nodeNear(bot.pos[0], bot.pos[1], 12.0)
                if k is None or a._comp[k] != a._comp[bot.node]:
                    continue
                x, y, z = a.wm.pos(k)
                dd = math.hypot(x - hx, y - hy)
                if dd > bd:
                    best, bd = k, dd
            if best is not None:
                bot.walkTo(best, RUN_SPEED)        # a hop clear of the Cog: run cycle at run speed
        else:
            bot.faceTo((hx, hy))
        self.waitSuitUntil = now + random.uniform(2.0, 3.5)

    def __atTunnel(self, now):
        bot, a, d = self.bot, self.bot.area, self.director
        nb = self.toT[0]
        dest = d.world.areas.get(nb)
        if dest is not None and d.eligible(bot, dest) and d.botsIn(dest) + 1 <= dest.max \
                and d.botsIn(a) - 1 >= a.min:
            if d.travel(bot, dest, why='street walk (P7)', via='tunnel'):
                kit.LIFE.count('tunnel_out_street')
            return False
        if self.turned:
            return False
        # that side is full: turn round and walk the street back
        self.turned = True
        kit.LIFE.count('street_turned_back')
        self.fromT, self.toT = self.toT, self.fromT
        self.__route()
        return self.__next()

    # ---- plumbing ------------------------------------------------------------------------------------
    def onDirect(self, fieldName, args):
        if self.visit is not None:
            self.visit.onDirect(fieldName, args)

    def onField(self, obj, fieldName, args):
        if self.visit is not None:
            self.visit.onField(obj, fieldName, args)

    def onHeard(self, speaker, msgId):
        if self.bot.path or (self.visit is not None and self.visit.phase != 'stay'):
            return False
        return kit.answerReal(self.bot, speaker, msgId)

    def stop(self, why):
        v = self.visit
        if v is not None and v.phase not in ('walk', 'out', 'enter'):
            v.abort(why)
        elif self.bot.path and why != 'travel':
            self.bot.stopWalking()
