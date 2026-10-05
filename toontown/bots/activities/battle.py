"""P8 STREET BATTLES: bots fight the street's Cogs like players do (the brain is toontown/bots/battlebrain.py).

  battle      a bot on a street (healthy, with gags) either walks up to a Cog and walks into it
              (DistributedSuit.requestBattle with the Cog's own position, as the client's collision
              sends it) or runs over to a battle that is already going and joins it (toonRequestJoin),
              up to 4 toons. After the battle it stands where the battle put it; low on laff it goes to
              its playground, sad (0 laff) it teleports there, as a player does.
  help        a REAL player's battle on the street with a free spot: 1-3 bots near him run over and
              join within ~10-20 s and fight with him (heal him when he is low, see the gag picker).
  restock     a bot low on gags in a playground goes to the gag shop and buys (the P4 Visit, a valid
              setInventory), as a player does before heading out again.
Rate limits (the AI checks almost nothing): at most 8 bot-started battles a minute on the district,
3 bot battles per street at once, a bot waits 45-90 s between two of its battles.
"""
import math
import random

from panda3d.core import Point3

from toontown.bots import battlebrain as bb
from toontown.bots import space
from toontown.bots.activities import Activity, register
from toontown.bots.activities import lifekit as kit
from toontown.bots.BotToon import RUN_SPEED

BATTLES_PER_MIN = 8
PER_STREET = 3


def suitNow(director, area, o):
    """(pos, heading, walking) of a street Cog right now: the client's own SuitLegList replay
    (lifekit.SuitWatch) plus the leg type (the AI only accepts requestBattle on a TWalk leg)."""
    from panda3d.toontown import SuitLeg
    from direct.distributed.ClockDelta import globalClockDelta
    pos = kit.SUITS._SuitWatch__suitPos(area, o)
    if pos is None:
        return None, 0.0, False
    cached = kit.SUITS.legs.get(o.doId)
    ll = cached[1] if cached else None
    pp = o.get('setPathPosition')
    if ll is None or not pp or (o.get('setPathState') or (0,))[0] != 1:
        return pos, 0.0, False
    try:
        start = globalClockDelta.networkToLocalTime(pp[1]) - ll.getStartTime(pp[0])
        elapsed = globalClock.getFrameTime() - start
        i = ll.getLegIndexAtTime(elapsed, 0)
        leg = ll.getLeg(i)
        a, b = leg.getPosA(), leg.getPosB()
        h = math.degrees(math.atan2(-(b[0] - a[0]), b[1] - a[1]))
        left = leg.getStartTime() + leg.getLegTime() - elapsed
        return pos, h, leg.getType() == SuitLeg.TWalk and left > 1.0
    except Exception:
        return pos, 0.0, False


def streetBattles(director, area):
    out = []
    for z in area.zones:
        v = director.viewOf(z)
        if v is None:
            continue
        for o in v.objects.values():
            if o.className == 'DistributedBattle':
                out.append(o)
    return out


def canFight(bot, frac=0.6, gags=8):
    return bb.healthy(bot, frac) and bb.gagCount(bot, bb.ATTACK_TRACKS) >= gags


def fightGags(bot):
    """PROGRESSION: the attack gags a bot wants before it starts a fight - 8, but a toon with no beans for the gag
    shop fights with what it has (a new toon: its cupcake and squirting flower), as a player does."""
    from toontown.bots import taskplan
    money = (bot.ownFields.get('setMoney') or (taskplan.SHOP_BEANS,))[0]
    return taskplan.MIN_ATTACK if money >= taskplan.SHOP_BEANS else taskplan.BROKE_ATTACK


MAX_ROUNDS = 3           # a bot alone starts a fight it ends in this many rounds
LAFF_SHARE = 0.6         # ... while the Cog's expected hits over those rounds cost at most this share of its laff


def canTake(bot, o, mates=0):
    """The owner (09-25): a bot only picks a fight it can handle. Alone against this Cog: its best gag (a Drop at
    ~55%, its miss rate on an unlured Cog) ends it in MAX_ROUNDS, the Cog's expected damage over those rounds
    (the game's own attack tables, gagplan.suitThreat) stays under LAFF_SHARE of its laff, and no single hit of
    the Cog's can take it out. (rounds, why) or (None, why).
    mates (PROGRESSION): toons who will answer its "Can you help me?" - new toons team up on a Cog none of them could
    beat alone, as players do: their gags add up and the Cog's hits are shared."""
    from toontown.bots import gagplan
    from toontown.suit import SuitDNA
    from toontown.battle import SuitBattleGlobals as SBG
    dnaS, lv = o.get('setDNAString'), o.get('setLevelDist')
    if not dnaS or not lv:
        return None, 'Cog unknown'
    try:
        dna = SuitDNA.SuitDNA()
        dna.makeFromNetString(dnaS[0])
        cogHp = SBG.SuitAttributes[dna.name]['hp'][max(0, min(4, lv[0]))]   # setLevelDist is the table row
        level = gagplan.cogLevel(dnaS[0], lv[0])
    except Exception:
        return None, 'Cog unknown'
    inv, exp = bb.loadInventory(bot)
    hp, mx = bb.botHp(bot)
    if inv is None or not hp:
        return None, 'no pouch'
    best = 0.0
    pouch = 0.0              # PROGRESSION: what the whole pouch can deal (a new toon: a cupcake and a flower)
    for t in bb.ATTACK_TRACKS:
        for l in range(7):
            n = inv.numItem(t, l)
            if n > 0:
                dmg = gagplan.gagDamage(t, l, exp) * (0.55 if t == bb.DROP else 1.0)
                best = max(best, dmg)
                pouch += n * dmg          # every gag landing: a new toon's 4 + 3 just beats a level-1 Flunky (6)
    if best <= 0:
        return None, 'no attack gags'
    team = 1 + mates
    rounds = int(math.ceil(cogHp / (best * team)))
    expd, biggest, group = gagplan.suitThreat(dnaS[0], lv[0])
    why = 'level %d Cog %d laff: best gag %d -> %d rounds, ~%d damage vs my %d laff (biggest hit %d)' % (
        level, cogHp, best, rounds, rounds * expd, hp, biggest)
    if mates:
        why += ' with %d more toons' % mates
    if rounds > MAX_ROUNDS or rounds * expd > LAFF_SHARE * hp * team or biggest >= hp:
        return None, why
    if pouch * team < cogHp:
        return None, why + '; my gags deal ~%d' % pouch
    return rounds, why


TASK_CHASE = 700.0      # ft: how far down its street a bot runs for a Cog its ToonTask needs
TEAM_REACH = 400.0      # ft: the help call's reach (helpcall.REACH)


def teamMates(bot, at=None):
    """PROGRESSION: how many free toons on this street would answer this bot's call for help (at most 3)."""
    d, n = bot.director, 0
    p = at or bot.pos
    for b in d.bots.values():
        if b is bot or b.area is not bot.area or b.state != 'present' or b.node is None or b.travel is not None:
            continue
        if b.activity is not None and (not b.activity.interruptible or b.activity.name in ('battle', 'cogbuilding')):
            continue
        if not canFight(b, 0.5, min(5, fightGags(b))):
            continue
        if math.hypot(b.pos[0] - p[0], b.pos[1] - p[1]) < TEAM_REACH:
            n += 1
            if n >= 3:
                break
    return n


@register
class StreetBattle(Activity):
    name = 'battle'
    progress = True          # PROGRESSION (owner 1a): none starts while no real player is online
    weight = 1.6
    kinds = ('street',)
    label = 'battle'

    @classmethod
    def canRun(cls, bot):
        d = bot.director
        if bot.area is None or bot.node is None or not d.clockSynced:
            return False
        now = globalClock.getRealTime()
        if now < getattr(bot, 'p8NextBattle', 0.0):
            return False
        bb.HUB.recent = [t for t in bb.HUB.recent if now - t < 60.0]
        if len(bb.HUB.recent) >= BATTLES_PER_MIN:
            return False
        busy = sum(1 for b in d.bots.values() if b.area is bot.area and b.activity is not None
                   and b.activity.name in ('battle', 'cogbuilding') and b.state == 'present')
        if busy >= PER_STREET * 4:        # PROGRESSION: new toons fight in teams of up to 4 (was * 2)
            return False
        return canFight(bot, gags=fightGags(bot))

    def __init__(self, bot, mode=None, battle=None, suitFilter=None):
        Activity.__init__(self, bot)
        self.mode = mode
        self.target = battle
        self.suitFilter = suitFilter      # P9: fn(suitViewObject) -> bool, the Cog to walk into (his ToonTask)
        self.brain = None
        self.suit = None
        self.phase = 'pick'
        self.until = 0.0
        self.retarget = 0.0
        self.fast = False
        self.team = False         # PROGRESSION: a Cog it takes on only with help: it asks as the fight starts
        self.asked = 0
        self.askAt = 0.0

    def start(self):
        bb.HUB.ensure(self.director)
        now = globalClock.getRealTime()
        ok = self.__start()
        if ok:              # PROGRESSION: only a fight it goes to waits out the gap (a failed look cost 45-90 s)
            self.bot.p8NextBattle = now + random.uniform(45.0, 90.0)
        return ok

    def __start(self):
        if self.mode in ('help', 'join'):
            return self.__goJoin(self.target)
        if self.suitFilter is not None:
            self.mode = 'start'
            return self.__pickSuit()
        # join a battle that is already going near me (a bot's or a player's) now and then
        near = [o for o in streetBattles(self.director, self.bot.area) if self.__joinable(o)
                and self.__dist(o.get('setPosition')) < 160.0]
        if near and random.random() < 0.45:
            self.mode = 'join'
            return self.__goJoin(min(near, key=lambda o: self.__dist(o.get('setPosition'))))
        if sum(1 for o in streetBattles(self.director, self.bot.area)) >= PER_STREET:
            return False
        self.mode = 'start'
        return self.__pickSuit()

    # -- helpers ------------------------------------------------------------------------------------------
    def __dist(self, p):
        if not p:
            return 1e9
        return math.hypot(p[0] - self.bot.pos[0], p[1] - self.bot.pos[1])

    def __joinable(self, o):
        m = o.get('setMembers')
        st = (o.get('setState') or ('',))[0]
        return bool(m) and len(m[6]) < 4 and st in ('FaceOff', 'WaitForInput', 'MakeMovie', 'PlayMovie', 'WaitForJoin')

    def __pickSuit(self):
        a, d, bot = self.bot.area, self.director, self.bot
        battles = [o.get('setPosition') for o in streetBattles(d, a)]
        cands = []
        for z in a.zones:
            v = d.viewOf(z)
            if v is None:
                continue
            for o in v.objects.values():
                if o.className != 'DistributedSuit':
                    continue
                if self.suitFilter is not None and not self.suitFilter(o):
                    continue
                pos, h, walking = suitNow(d, a, o)
                if pos is None or not walking:
                    continue
                if any(p and math.hypot(p[0] - pos[0], p[1] - pos[1]) < 40.0 for p in battles):
                    continue
                if self.suitFilter is None:
                    # the owner (09-25): never a fight it can't handle (a Cog asked for by a player is his call)
                    rounds, why = canTake(bot, o)
                    if rounds is None:
                        bb.STATS.count('fight_too_tough_skipped')
                        continue
                dist = math.hypot(pos[0] - bot.pos[0], pos[1] - bot.pos[1])
                # PROGRESSION: a Cog its ToonTask needs is worth a run down the street, as a player does
                if dist < (TASK_CHASE if self.suitFilter is not None else 220.0):
                    cands.append((dist, o))
        cands.sort(key=lambda c: c[0])
        for dist, o in cands[:4]:
            if self.claim(('suit', o.doId)):
                self.suit = o
                self.team = self.suitFilter is not None and canTake(bot, o)[0] is None
                self.phase = 'approach'
                self.until = globalClock.getRealTime() + 45.0 + dist / 12.0
                self.label = 'to-cog'
                if random.random() < 0.3:
                    kit.say(bot, random.choice((1102, 1406, 1416)))
                return self.__chase(globalClock.getRealTime(), force=True)
            continue
        if self.suitFilter is not None:
            bb.STATS.count('task_pick_failed_%s' % ('claimed' if cands else 'none_near'))
        return False

    def __chase(self, now, force=False):
        """Run at the Cog, re-aiming at where it is now; touching it starts the battle."""
        a, bot = self.bot.area, self.bot
        # the Cog changes visgroup as it walks (a new view object each time): find it by doId
        o = None
        for z in a.zones:
            v = self.director.viewOf(z)
            if v is not None and self.suit.doId in v.objects:
                o = v.objects[self.suit.doId]
                break
        if o is None:
            bot.stopWalking()
            return False
        self.suit = o
        pos, h, walking = suitNow(self.director, a, o)
        if pos is None:
            return False
        dist = math.hypot(pos[0] - bot.pos[0], pos[1] - bot.pos[1])
        # STREET LIFE (owner 09-25): my Cog was pulled into someone else's battle: find another life, never
        # stand by that battle waiting for it (a bot stood 33 s at 20 ft)
        if not walking and any(p and math.hypot(p[0] - pos[0], p[1] - pos[1]) < 25.0
                               for p in (o2.get('setPosition') for o2 in streetBattles(self.director, a))):
            bot.stopWalking()
            return False
        if walking:
            self.__dict__.pop('stillSince', None)
        if dist < 6.0:
            if not walking:
                if now - getattr(self, 'stillSince', now) > 4.0:
                    bot.stopWalking()
                    return False            # it stopped for good (a door, a battle): give it up
                self.stillSince = getattr(self, 'stillSince', now)
                return True             # it is turning a corner / leaving: wait a moment
            bot.stopWalking()
            bot.faceTo(pos)
            bot.broadcastNow()
            self.brain = bb.BattleBrain(bot, 'street')
            self.brain.startOn(o, pos, h)
            self.interruptible = False
            self.phase = 'battle'
            self.label = 'battle'
            return True
        if force or now >= self.retarget or not bot.path:
            self.retarget = now + 1.0
            # aim a little ahead of it along its heading when far
            lead = min(12.0, dist * 0.25) if walking else 0.0
            tx = pos[0] - math.sin(math.radians(h)) * lead
            ty = pos[1] + math.cos(math.radians(h)) * lead
            if not bot.walkToPos(tx, ty, pos[2], RUN_SPEED, 'run'):
                return False
        return True

    def __goJoin(self, o):
        if o is None or not self.__joinable(o):
            return False
        p = o.get('setPosition')
        if not p or self.__dist(p) > 320.0:
            return False
        if not self.claim(('battleslot', o.doId, self.bot.avId)):
            return False
        self.target = o
        self.phase = 'tojoin'
        self.until = globalClock.getRealTime() + 30.0
        self.label = 'to-battle'
        # stop about 9 ft short of the centre: the battle's collision tube, where the client joins; never inside one
        # of its toons standing there (personal space: the first spot round the tube clear of them)
        ang = math.atan2(self.bot.pos[1] - p[1], self.bot.pos[0] - p[0])
        pts = [(p[0] + math.cos(ang + math.radians(da)) * 9.0, p[1] + math.sin(ang + math.radians(da)) * 9.0, p[2])
               for da in (0, 25, -25, 50, -50, 75, -75)]
        tx, ty = space.freePoint(self.bot, pts, keepOut=False)[:2]
        if self.__dist(p) > 12.0 and not self.bot.walkToPos(tx, ty, p[2], RUN_SPEED, 'run'):
            return False
        return True

    # -- the tick --------------------------------------------------------------------------------------------
    def step(self, now):
        bot = self.bot
        if self.phase == 'approach':
            if now > self.until:
                bot.stopWalking()
                return False
            return self.__chase(now)
        if self.phase == 'tojoin':
            o = self.target
            v = self.director.viewOf(o.zoneId)
            if v is None or o.doId not in v.objects or not self.__joinable(o) or now > self.until:
                bot.stopWalking()
                return False
            p = o.get('setPosition')
            if self.__dist(p) <= 12.0 or not bot.path:
                if self.__dist(p) > 16.0:
                    bot.stopWalking()
                    return False
                bot.stopWalking()
                bot.faceTo(p)
                bot.broadcastNow()
                self.brain = bb.BattleBrain(bot, 'street')
                self.brain.join(o)
                self.interruptible = False
                self.phase = 'battle'
                self.label = 'battle'
                if self.mode == 'help':
                    bb.STATS.count('help_joined')
                    bb.STATS.note('bot %s joined real player battle %s' % (bot.avId, o.doId))
            return True
        if self.phase == 'battle':
            if self.team and self.asked < 3:
                if not self.askAt:
                    self.askAt = now + 3.0
                elif now >= self.askAt:
                    self.__askTeam(now)
            if self.brain.step(now):
                return True
            return self.__after(now)
        if self.phase == 'after':
            if now < self.until:
                return True
            self.interruptible = True
            hp, mx = bb.botHp(bot)
            if hp is not None and mx and hp < 0.4 * mx:
                if random.random() < 0.5:
                    kit.say(bot, 1414)
                bb.HUB.goHeal(bot, 'low')
                return False
            return False
        return False

    def __askTeam(self, now):
        """A new toon on a Cog it can't beat alone: "Can you help me?" and the toons near come running
        (helpcall: the ones with the same ToonTask get the Cog too)."""
        from toontown.bots.activities import helpcall
        self.asked += 1
        self.askAt = now + 6.0
        try:
            if helpcall.HELP.battleOf(self.bot.avId, self.bot.area) is None:
                return
            if self.asked == 1 or random.random() < 0.5:
                kit.say(self.bot, 514, force=True, answer=False)
            bb.STATS.count('team_ask')
            if helpcall.HELP.call(self.bot.avId, self.bot.zoneId, 'task team'):
                self.asked = 3
        except Exception:
            import traceback
            bb.HUB.error('team ask', traceback.format_exc())

    def __after(self, now):
        bot, br = self.bot, self.brain
        res = br.result
        if res in ('denied', 'no-battle', 'join-timeout'):
            self.interruptible = True
            return False
        if res == 'died':
            bb.HUB.goHeal(bot, 'sad')
            return False
        # stand where the battle put me, as every client shows it
        p = br.myIndexPos()
        if p is not None and bot.area is not None:
            bot.pos = Point3(p)
            bot.node = bot.area.wm.nearestNode(p[0], p[1], p[2])
            bot.broadcastNow()
        if res == 'ran':
            k = bot.area.nodeNear(bot.pos[0], bot.pos[1], 35.0) if bot.area is not None else None
            if k is not None:
                bot.walkTo(k, RUN_SPEED, 'run')
            self.interruptible = True
            return False
        if res == 'won' and random.random() < 0.45:
            kit.say(bot, random.choice((1405, 1404, 1407, 1408, 1411)))
        self.phase = 'after'
        self.until = now + random.uniform(1.5, 4.0)
        return True

    # -- plumbing ----------------------------------------------------------------------------------------------
    def onDirect(self, fieldName, args):
        if self.brain is not None:
            self.brain.onDirect(fieldName, args)

    def onHeard(self, speaker, msgId):
        return False

    def stop(self, why):
        if self.brain is not None and self.brain.phase != 'done':
            self.brain.leave(why)
        if self.bot.path and why != 'travel':
            self.bot.stopWalking()


# ---- a real player's battle: send help --------------------------------------------------------------------
def helpPlayer(battleObj, record):
    d = bb.HUB.director
    now = globalClock.getRealTime()
    if record is None:
        return
    if now < getattr(record, 'helpAt', 0.0):
        return
    record.helpAt = now + 2.0
    area = d.world.zoneToArea.get(battleObj.zoneId)
    if area is None or area.kind != 'street':
        return
    if not hasattr(record, 'helpWant'):
        record.helpWant = random.choice((1, 2, 2, 3))
        record.helpers = set()
    m = battleObj.get('setMembers')
    toons = list(m[6])
    coming = [a for a in record.helpers if a not in toons and d.bots.get(a) is not None
              and d.bots[a].activity is not None and d.bots[a].activity.name == 'battle'
              and d.bots[a].activity.brain is None]
    inBots = [t for t in toons if t in d.bots]
    want = min(record.helpWant, 4 - len(toons) + len(inBots)) - len(inBots) - len(coming)
    if want <= 0:
        return
    p = battleObj.get('setPosition')
    if not p:
        return
    cands = []
    for b in d.bots.values():
        if b.area is not area or b.state != 'present' or b.travel is not None or b.node is None:
            continue
        if b.activity is not None and (not b.activity.interruptible or b.activity.name in ('battle', 'cogbuilding')):
            continue
        if not canFight(b, 0.4, 5):
            continue
        dist = math.hypot(b.pos[0] - p[0], b.pos[1] - p[1])
        if dist < 300.0:
            cands.append((dist, b))
    # a hurt player gets a toon who carries Toon-Up first (a kid with heals runs to help a hurt friend)
    hurt = False
    for t in toons:
        if t not in d.bots:
            hp, mx = bb.toonHp(d.air, t)
            hurt = hurt or (hp is not None and mx and hp < 0.6 * mx)
    if hurt:
        cands.sort(key=lambda c: (bb.gagCount(c[1], (bb.HEAL,)) == 0, c[0]))
    else:
        cands.sort(key=lambda c: c[0])
    for dist, b in cands[:want]:
        act = StreetBattle(b, mode='help', battle=battleObj)
        if b.startActivity(act):
            record.helpers.add(b.avId)
            b.nextTick = min(b.nextTick, now)
            bb.STATS.count('help_sent')
            bb.STATS.note('bot %s (%.0f ft away) sent to help real player(s) %s in battle %s' % (
                b.avId, dist, [t for t in toons if t not in d.bots], battleObj.doId))


bb.HUB.helpHook = helpPlayer


# ---- low on gags: the gag shop ------------------------------------------------------------------------------
@register
class Restock(Activity):
    name = 'restock'
    weight = 5.0
    kinds = ('playground',)
    label = 'to-shop'

    @classmethod
    def canRun(cls, bot):
        if bot.area is None or bot.node is None:
            return False
        money = (bot.ownFields.get('setMoney') or (0,))[0]
        # the owner (09-25): a bot keeps itself stocked up like a normal player (a building / facility / boss run
        # takes only stocked bots, bb.stocked): below that it shops, and the trip fills the pouch (lifekit)
        if money < 15 or bb.stocked(bot):
            return False
        return any(p['kind'] == 'gagshop' for p in bot.area.doors)

    def start(self):
        bb.HUB.ensure(self.director)
        a, d, bot = self.bot.area, self.director, self.bot
        self.full, self.why = False, 'no walk to the gag shop'
        for p in a.doors:
            if p['kind'] != 'gagshop' or not d.canWalkTo(bot, p):
                continue
            ex = p['extra']
            doId = d.doorDOs.get((a.id, ex['block'], ex.get('door', 0)))
            if doId is None:
                continue
            zone = kit.interiorZone(d.world.doorZone(a, p), ex['block'])
            slot = next((i for i in range(kit.CAP.get('gagshop', 2)) if self.claim(('interior', zone, i))), None)
            if slot is None:
                self.full = True            # PROGRESSION: a crowded shop is "later", not a failure (toontask)
                continue
            self.visit = kit.Visit(self, p, doId, stay=random.uniform(15.0, 30.0))
            bot.fillUp = True               # this trip stocks up (lifekit.planPurchase: best gags first, to the full)
            if self.visit.begin():
                bb.STATS.count('restock_trips')
                return True
            self.why = 'the shop visit would not begin'
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

    def stop(self, why):
        v = self.visit
        if v.phase not in ('walk', 'out', 'enter'):
            v.abort(why)
        elif self.bot.path and why != 'travel':
            self.bot.stopWalking()
