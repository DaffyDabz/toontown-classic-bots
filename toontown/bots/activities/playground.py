"""P4 PLAYGROUND LIFE (every playground: the 6 hoods + Acorn Acres, Goofy Speedway, MiniGolf).

  hangout   walk to a landmark (fountain, gag shop, trolley, gazebo, Toon HQ, ...) and stand in a loose
            group there: turn to the others, a SpeedChat line now and then (the zone's budget keeps it to
            a few toons at once), an emote now and then, answer a toon who speaks near by
  wander    stroll the walk map with natural pauses; greet or wave at a toon it passes, sometimes
  shop      walk to the gag shop / Toon HQ / clothes / pet shop / bank / library / school / Toon Hall
            door and go IN through the real door protocol, stay a while (buy gags from a clerk with a
            valid setInventory, talk to an HQ officer or shopkeeper, stand about), come back out
  tunnel    walk into a tunnel and leave for the street behind it (director.travel via='tunnel')
No random jumping: nothing here sends a jump, Belly Flop or Banana Peel.
"""
import math
import random

from toontown.bots import space
from toontown.bots.activities import Activity, register
from toontown.bots.activities import lifekit as kit
from toontown.bots.BotToon import WALK_SPEED, tripSpeed

LANDMARKS = {'fountain': 3.0, 'gagshop': 2.5, 'trolley': 3.0, 'gazebo': 1.5, 'hq': 2.0, 'statue': 1.0,
             'toonhall': 1.0, 'petshop': 0.8, 'clothes': 0.8, 'bank': 0.5, 'library': 0.5, 'school': 0.5,
             'partygate': 0.6, 'fishing': 0.8, 'gametable': 2.0, 'picnictable': 2.0, 'golfkart': 2.0,
             'racingpad': 1.5, 'viewingpad': 1.0, 'leaderboard': 1.5, 'kartshop': 1.5}
GROUP_MAX = 6
SHOP_W = {'gagshop': 3.0, 'hq': 2.5, 'clothes': 0.8, 'petshop': 0.8, 'bank': 0.5, 'library': 0.5, 'school': 0.5,
          'toonhall': 0.7, 'door': 1.0}


class _Life(Activity):
    chatty = True
    label = None

    def start(self):
        kit.LIFE.ensureTask(self.director)
        return self._start()

    def onHeard(self, speaker, msgId):
        if self.bot.path:
            return False
        return kit.answerReal(self.bot, speaker, msgId)

    def _groupAt(self, x, y, r=14.0):
        bot = self.bot
        return [b for b in self.director.bots.values() if b is not bot and b.zoneId == bot.zoneId and b.state == 'present'
                and b.activity is not None and getattr(b.activity, 'name', '') == 'hangout'
                and math.hypot(b.pos[0] - x, b.pos[1] - y) < r]


@register
class Hangout(_Life):
    name = 'hangout'
    weight = 3.0
    kinds = ('playground',)
    label = 'to-landmark'

    @classmethod
    def canRun(cls, bot):
        return bot.area is not None and bot.node is not None

    def _start(self):
        a, bot = self.bot.area, self.bot
        places = [p for p in a.wm.places() if p['kind'] in LANDMARKS and a.placeNode(p) is not None
                  and self.director.canWalkTo(bot, p)]
        if not places:
            return False
        scored = []
        for p in places:
            n = len(self._groupAt(p['pos'][0], p['pos'][1]))
            if n >= GROUP_MAX:
                continue
            scored.append((p, LANDMARKS[p['kind']] * (1.0 + 0.6 * min(n, 4))))
        if not scored:
            return False
        r = random.uniform(0, sum(w for _, w in scored))
        for p, w in scored:
            r -= w
            if r <= 0:
                break
        self.place = p
        cx, cy = p['pos'][0], p['pos'][1]
        k = None
        for rad in (10.0, 14.0, 18.0, 24.0):
            k = a.nodeNear(cx, cy, rad)
            if k is not None and a._comp[k] == a._comp[bot.node]:
                break
            k = None
        if k is None or not bot.walkTo(k, *tripSpeed()):
            return False
        self.stayUntil = 0.0
        self.nextThing = 0.0
        self.walkUntil = globalClock.getRealTime() + 80.0
        return True

    def step(self, now):
        bot = self.bot
        if bot.path:
            # (the walk there only: a shuffle or a step aside during the stay is not cut short)
            if now > self.walkUntil and not self.stayUntil:
                bot.stopWalking()
                return False
            return True
        if not self.stayUntil:
            self.stayUntil = now + random.uniform(25.0, 100.0)
            self.nextThing = now + random.uniform(1.0, 4.0)
            self.label = 'idle'
            self.__faceGroup()
            return True
        if now >= self.stayUntil:
            if random.random() < 0.3:
                kit.say(bot, random.choice((200, 201, 202, 207)), now, answer=False)
            return False
        if now >= self.nextThing:
            self.nextThing = now + random.uniform(4.0, 11.0)
            r = random.random()
            if r < 0.40:
                kit.say(bot, kit.placeLine(bot, self.place['kind']), now)
            elif r < 0.58:
                kit.emote(bot, now=now)
            elif r < 0.85:
                self.__faceGroup()
            elif r < 0.93:
                # a small shuffle, a few feet
                a = bot.area
                k = a.nodeNear(bot.pos[0], bot.pos[1], 5.0)
                if k is not None and a._comp[k] == a._comp[bot.node]:
                    bot.walkTo(k, WALK_SPEED)
        return True

    def __faceGroup(self):
        bot = self.bot
        g = self._groupAt(bot.pos[0], bot.pos[1], 16.0)
        if g and random.random() < 0.7:
            bot.faceTo(random.choice(g).pos)
        else:
            bot.faceTo(self.place['pos'])

    def stop(self, why):
        if self.bot.path and why != 'travel':
            self.bot.stopWalking()


@register
class Wander(_Life):
    name = 'wander'
    weight = 1.5
    kinds = ('playground',)
    label = 'strolling'

    @classmethod
    def canRun(cls, bot):
        return bot.area is not None and bot.node is not None

    def _start(self):
        self.rounds = random.randint(3, 7)
        self.pauseUntil = 0.0
        self.greeted = set()
        return self.__walk()

    def __walk(self):
        a, bot = self.bot.area, self.bot
        for _ in range(4):
            k = a.nodeNear(bot.pos[0], bot.pos[1], random.uniform(30.0, 90.0))
            if k is not None and a._comp[k] == a._comp[bot.node] and bot.walkTo(k, *tripSpeed()):
                return True
        return True

    def step(self, now):
        bot = self.bot
        if bot.path:
            # a toon passing close by: sometimes stop and say hi / wave
            if random.random() < 0.06:
                bots = self.director.bots
                for o in bot.view.ofClass('DistributedToon') if bot.view else ():
                    pos = bots[o.doId].pos if o.doId in bots else o.pos      # bots' own moves never echo back
                    # personal space: a toon right beside me is walked past, never stopped inside
                    if o.doId != bot.avId and o.doId not in self.greeted and \
                            space.GAP + 1.5 <= (pos - bot.pos).length() < 9.0:
                        self.greeted.add(o.doId)
                        bot.stopWalking()
                        bot.faceTo(pos)
                        if random.random() < 0.5:
                            kit.say(bot, random.choice(kit.GREET), now)
                        else:
                            kit.emote(bot, 0, now)
                        self.pauseUntil = now + random.uniform(2.0, 4.0)
                        break
            return True
        if not self.pauseUntil:
            self.pauseUntil = now + (random.uniform(6.0, 15.0) if random.random() < 0.2 else random.uniform(1.5, 5.0))
            bot.setAnim('neutral')
            if random.random() < 0.12:
                kit.emote(bot, now=now)
            elif random.random() < 0.10:
                kit.say(bot, kit.placeLine(bot), now)
            return True
        if now < self.pauseUntil:
            return True
        self.pauseUntil = 0.0
        self.rounds -= 1
        if self.rounds <= 0:
            return False
        self.__walk()
        return True

    def stop(self, why):
        if self.bot.path and why != 'travel':
            self.bot.stopWalking()


@register
class Shop(_Life):
    name = 'shop'
    weight = 1.4
    kinds = ('playground',)
    label = 'to-shop'

    @classmethod
    def canRun(cls, bot):
        return bot.area is not None and bot.node is not None and bool(kit.visitKinds(bot.area))

    def _start(self):
        a, bot, d = self.bot.area, self.bot, self.director
        cands = []
        for p in kit.visitKinds(a):
            ex = p['extra']
            doId = d.doorDOs.get((a.id, ex['block'], ex.get('door', 0)))
            if doId is None or not d.canWalkTo(bot, p):
                continue
            cands.append((p, doId, SHOP_W.get(p['kind'], 0.5)))
        random.shuffle(cands)
        cands.sort(key=lambda c: -c[2] * random.random())
        for p, doId, w in cands:
            zone = kit.interiorZone(d.world.doorZone(a, p), p['extra']['block'])
            cap = kit.CAP.get(p['kind'], 2)
            slot = next((i for i in range(cap) if self.claim(('interior', zone, i))), None)
            if slot is None:
                continue
            self.visit = kit.Visit(self, p, doId)
            if self.visit.begin():
                self.label = self.visit.label
                return True
            self.releaseAll()
        return False

    def step(self, now):
        ok = self.visit.step(now)
        self.label = self.visit.label
        return ok

    def onDirect(self, fieldName, args):
        self.visit.onDirect(fieldName, args)

    def onField(self, obj, fieldName, args):
        self.visit.onField(obj, fieldName, args)

    def onHeard(self, speaker, msgId):
        if self.visit.phase != 'stay':
            return False
        return _Life.onHeard(self, speaker, msgId)

    def stop(self, why):
        v = getattr(self, 'visit', None)
        if v is not None and v.phase not in ('walk', 'out') and not (v.phase == 'enter'):
            v.abort(why)
        elif self.bot.path and why != 'travel':
            self.bot.stopWalking()


@register
class Tunnel(_Life):
    """Walk into a tunnel and go on to the street (or playground) behind it."""
    name = 'tunnel'
    weight = 0.35
    kinds = ('playground',)
    label = 'to-tunnel'

    @classmethod
    def canRun(cls, bot):
        return bot.area is not None and bot.node is not None and not bot.pinned and bool(bot.area.tunnels)

    def _start(self):
        a, bot, d = self.bot.area, self.bot, self.director
        if d.botsIn(a) - 1 < a.min:
            return False
        opts = []
        for nbId, place in a.tunnels.items():
            nb = d.world.areas.get(nbId)
            if nb is None or not d.eligible(bot, nb) or not d.canWalkTo(bot, place):
                continue
            if d.botsIn(nb) + 1 > nb.max:
                continue
            opts.append((nb, place))
        if not opts:
            return False
        self.dest, self.place = random.choice(opts)
        x, y, z = self.place['pos']
        if not bot.walkTo(a.placeNode(self.place), *tripSpeed()):
            return False
        self.until = globalClock.getRealTime() + 120.0
        if random.random() < 0.3:
            kit.say(bot, random.choice((1000, 1101, 207, 200)) if self.dest.kind == 'playground'
                    else random.choice((1102, 1000, 1103)), answer=True)
        return True

    def step(self, now):
        bot = self.bot
        if bot.path:
            if now > self.until:
                bot.stopWalking()
                return False
            return True
        if self.director.botsIn(self.dest) + 1 > self.dest.max:
            return False
        if self.director.travel(bot, self.dest, why='tunnel (P4)', via='tunnel'):
            kit.LIFE.count('tunnel_out_%s' % bot.area.kind if bot.area is not None else 'tunnel_out')
        return False

    def stop(self, why):
        if self.bot.path and why != 'travel':
            self.bot.stopWalking()
