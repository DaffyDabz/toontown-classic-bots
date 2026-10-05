"""P8b BOSS BATTLES: VP, CFO, CJ, CEO, fought by bots the way a client fights them.

Shared (every boss):
  avatarEnter   right after the boss object shows up in the office zone (CogHQBossBattle.enter)
  barriers      every phase barrier the bot is listed in, acked after the client's movie (common.Barriers)
  battles       BattleOne / BattleTwo / BattleThree(CEO) are DistributedBattleFinal battles the AI puts the
                toons in: the shared brain attaches (kind 'boss'); the battle's ReservesJoining is a barrier
  reward        'Reward' barrier after the reward panel; Epilogue: a few pages of chat, then avatarExit and
                a teleport out (CogHQBossBattle.exit), like localToonToSafeZone
  zaps          a directed / area attack at the bot sometimes lands: zapToon (the client reports its own hits)
Per boss, the scripted rounds (all through the clsend fields, at a kid's pace and aim):
  VP   BattleThree: touchCage for pies; a pie into the open hatch while the gears fly (doStrafe) makes the
       boss dizzy (hitBossInsides), pies at a dizzy boss hit it (hitBoss(1)); a pie heals a sad-looking
       teammate now and then (hitToon); NearVictory: finalPieSplat.
  CFO  BattleThree: each bot takes a free crane (requestControl), grabs a goon (requestGrab), swings it into
       the boss (hitBoss(impact) -> dizzy at 15+ damage), then safes while it is dizzy (and knocks a helmet
       off); bots without a crane stomp goons: they broadcast their position next to the goon first (the AI
       checks the distance) then requestStunned; they pick up the treasure that drops.
  CJ   BattleTwo: each bot its own cannon: requestEnter, aim (setCannonPosition), setCannonLit, the flight,
       hitChair on an empty juror chair when the shot lands there, setLanded; BattleThree: touchWitnessStand
       for evidence, evidence into the defense pan (hitBoss(1), weighted by the AI), stun lawyers now and
       then (hitByToon), back to the stand when out of evidence.
  CEO  BattleTwo: grab food from a belt (requestGetFood) and serve a hungry diner (requestServeFood), for the
       whole serving timer; BattleThree: the diner battles; BattleFour: take a free table (requestControl),
       squirt the boss (hitBoss(1..3)), a new table when knocked off.
"""
import math
import random
import time

from direct.distributed.ClockDelta import globalClockDelta
from panda3d.core import Point3

from toontown.bots import battlebrain as bb
from toontown.bots.coghq import common as cm

DIZZY_NOW = 12            # ToontownGlobals.BossCogDizzyNow (VP, CJ)
DIZZY = 0                 # ToontownGlobals.BossCogDizzy (CFO)
AREA_ATTACK = 4
DIRECTED_ATTACK = 7
SLOW_DIRECTED = 11
BOSS_ARENA = {             # where the toons stand around the boss in its last round (x, y, z)
    'vp': (0, -110, -6.5), 'cfo': (120, -315, 0), 'cj': (30, 40, 0), 'ceo': (0, 300, 0)}
FRONT_ATTACK = 5
RECOVER_DIZZY = 6
STRAFE_ATTACK = 8
# the VP's last round (DistributedSellbotBoss.__makeBossDamageMovie): the boss rolls from the platform
# (0, 60, 18) down to (0, -110, -6.5) and on to (0, -175, -6.5) in step with its damage, facing -y all the way
VP_START, VP_BOTTOM, VP_DEATH = (0.0, 60.0, 18.0), (0.0, -110.0, -6.5), (0.0, -175.0, -6.5)
VP_SEG1 = math.hypot(VP_START[1] - VP_BOTTOM[1], VP_START[2] - VP_BOTTOM[2])
VP_SEG2 = VP_BOTTOM[1] - VP_DEATH[1]
LAST_ROUND = {'vp': ('BattleThree',), 'cfo': ('BattleThree',), 'cj': ('BattleThree',), 'ceo': ('BattleFour',)}
VP_SLOTS = (45, -45, 135, -135, 65, -65, 115, -115)    # angle from the boss's front: off both doors' gear fans
BATTLE_STATES = {'vp': ('BattleOne', 'BattleTwo'), 'cfo': ('BattleOne',), 'cj': ('BattleOne',),
                 'ceo': ('BattleOne', 'BattleThree')}
# per-boss barrier movie lengths (the defaults in common.DELAYS cover the rest)
BOSS_DELAYS = {
    ('cj', 'BattleTwo'): (66.0, 69.0),               # the jury box rolls in for 70 s (LawbotBossJuryBoxMoveTime)
    ('ceo', 'BattleTwo'): (296.0, 299.5),            # the serving timer (BossbotBossServingDuration 300)
    ('ceo', 'Introduction'): (28.0, 38.0),
    ('cfo', 'PrepareBattleThree'): (30.0, 42.0),     # the crane room movie
    ('cj', 'PrepareBattleTwo'): (22.0, 32.0),
    ('cj', 'PrepareBattleThree'): (22.0, 32.0),
    ('ceo', 'PrepareBattleTwo'): (20.0, 30.0),
    ('ceo', 'PrepareBattleThree'): (12.0, 20.0),
    ('ceo', 'PrepareBattleFour'): (18.0, 28.0),
    ('cfo', 'Victory'): (14.0, 20.0),
    ('cj', 'Victory'): (14.0, 20.0),
    ('ceo', 'Victory'): (14.0, 20.0),
}


def now_():
    return globalClock.getRealTime()


def angDiff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ---- test-only phase skip: the game's own ~boss magic word (MagicWordIndex.BossBattle), said by one bot -----
_MWM = {'view': None}


def mwmView(air):
    if _MWM['view'] is None:
        _MWM['view'] = air.openView(air.districtId, 2)          # OTP_ZONE_ID_MANAGEMENT: the magic word manager
    return _MWM['view']


def sayMagicWord(bot, word, later):
    """TEST RUNS ONLY (a 'skip=' command): the bot gets the Moderator access every account on this test server
    logs in with (default-access-level), says the word through the game's own ToontownMagicWordManager, and is
    put back to no access a few seconds later."""
    from toontown.bots.activities import boss as bossAct
    if not getattr(getattr(bossAct, 'KEEPER', None), 'testRuns', False):
        return False                                   # only while the harness's 'bossruns on' test switch is on
    o = mwmView(bot.air).first('ToontownMagicWordManager')
    if o is None:
        return False
    bot.air.sendInternal(bot.avId, 'DistributedToon', 'setAccessLevel', [100])
    later(0.6, bot.send, 'requestExecuteMagicWord', [0, 0, 0, 0, word], o.doId, o.className)
    taskMgr.doMethodLater(6.0, lambda task: bot.air.sendInternal(bot.avId, 'DistributedToon', 'setAccessLevel', [0])
                          and task.done, 'botboss-mw-reset-%d' % bot.avId)
    return True


class BossRun:
    """One boss office (shared by the group's bots)."""

    def __init__(self, kind, director, log):
        self.kind = kind
        self.info = cm.BOSSES[kind]
        self.director = director
        self.air = director.air
        self.log = log
        self.members = []
        self.players = set()
        self.zone = None
        self.view = None
        self.bossId = None
        self.state = None
        self.stateT = now_()
        self.states = []               # (state, seconds into the run)
        self.t0 = now_()
        self.attack = (None, 0, 0.0)   # (code, avId, when)
        self.strafeT = -99.0
        self.foodNum = [random.randint(3, 40), random.randint(3, 40)]
        self.claimed = {}              # resource key -> avId (cranes, cannons, tables, chairs, diners)
        self.won = False
        self.promoted = []
        self.damage = 0
        self.skipTo = None             # test runs: '~boss <skipTo>' once the fight has started
        self.skipped = False
        self.strafe = (None, 0, -99.0, 0.0)   # VP: (door side, spread direction, when, gear window seconds)
        self.insidesT = -99.0          # VP: the last pie into an open door (one per strafe is enough)
        self.dmgLog = []               # (seconds into the last round, boss damage)
        self.lastT0 = None             # when the boss's last round began

    def enterZone(self, zone):
        if self.zone == zone:
            return
        self.zone = zone
        if self.log.tInside is None:
            self.log.tInside = time.time()
        self.view = self.air.openView(self.air.districtId, zone)
        self.log.note('office zone %s' % zone)
        if self.skipTo:
            mwmView(self.air)

    def end(self):
        if self.view is not None:
            self.air.closeView(self.view)
            self.view = None

    def boss(self):
        if self.view is None:
            return None
        if self.bossId is not None:
            o = self.view.objects.get(self.bossId)
            if o is not None:
                return o
        o = self.view.first(self.info['bossClass'])
        if o is not None and self.bossId != o.doId:
            self.bossId = o.doId
            bb.HUB.watch(o.doId, self.__bossField)
            st = (o.get('setState') or (None,))[0]
            if st:
                self.__bossField(o, 'setState', (st,))
        return o

    def __bossField(self, obj, fieldName, args):
        if fieldName == 'setState':
            if args[0] != self.state:
                self.state = args[0]
                self.stateT = now_()
                self.states.append((self.state, int(now_() - self.t0)))
                self.log.note('boss state %s' % self.state)
                if self.state in LAST_ROUND.get(self.kind, ()) and self.lastT0 is None:
                    self.lastT0 = now_()
                if self.state == 'Victory' and not self.won:
                    self.won = True
                    self.lastRoundSummary()
                if self.state == 'Defeat':
                    self.log.count('defeat')
        elif fieldName == 'setAttackCode':
            if self.kind == 'cfo' and args[0] == DIZZY and self.attack[0] != DIZZY and self.lastT0 is not None:
                self.log.count('knockout')                 # a goon/safe hit of 15+ made it dizzy
            self.attack = (args[0], args[1], now_())
        elif fieldName == 'doStrafe':
            self.strafeT = now_()
            t = min(1.0, self.damage / 100.0)
            self.strafe = (args[0], args[1], self.strafeT, 5.0 - 4.0 * t)     # DistributedSellbotBoss.doStrafe
            self.log.count('strafe')
        elif fieldName == 'setBossDamage':
            if args[0] != self.damage and self.lastT0 is not None:
                self.dmgLog.append((int(now_() - self.lastT0), args[0]))
            self.damage = args[0]
            self.log.counts['bossDamage'] = args[0]

    def lastRoundSummary(self):
        if self.lastT0 is None:
            return
        c = self.log.counts
        secs = now_() - self.lastT0
        self.log.counts['lastRoundSecs'] = int(secs)
        keys = sorted(k for k in c if k not in ('bossDamage', 'lastRoundSecs', 'avatarEnter') and not k.startswith('battle_'))
        self.log.note('%s LAST ROUND beaten in %d s: %s; damage over time %s' % (
            self.kind.upper(), secs, ', '.join('%s %d' % (k, c.get(k, 0)) for k in keys), self.dmgLog[-40:]))

    def ofClass(self, cls):
        return self.view.ofClass(cls) if self.view is not None else []

    def claim(self, key, avId):
        owner = self.claimed.get(key)
        if owner is None or owner == avId:
            self.claimed[key] = avId
            return True
        return False

    def release(self, avId):
        for k in [k for k, a in self.claimed.items() if a == avId]:
            del self.claimed[k]

    def realIn(self):
        return bool(self.players)


class BossBot:
    """One bot in a boss office (driven by activities/boss.py)."""

    def __init__(self, activity, run):
        self.act = activity
        self.bot = activity.bot
        self.run = run
        self.kind = run.kind
        self.barriers = cm.Barriers(self.bot, self.__delay, run.log)
        self.entered = False
        self.enterAt = 0.0
        self.brain = None
        self.lastBattle = None
        self.next = 0.0
        self.pies = 0
        self.cageAt = 0.0
        self.pos = Point3(*BOSS_ARENA[self.kind])
        self.lastState = None
        self.outAt = 0.0
        self.item = None               # CFO: (object doId, kind) on my crane; CEO: food in hand
        self.crane = None
        self.table = None
        self.cannon = None
        self.balls = 0
        self.near = False
        self.tasks = []
        self.zapped = 0.0

    # -- helpers -------------------------------------------------------------------------------------------
    def __delay(self, obj, name):
        d = BOSS_DELAYS.get((self.kind, name))
        if name == 'Reward':
            n = len((self.run.boss().get('setToonIds') or ([],))[0]) if self.run.boss() is not None else 4
            return 9.0 + 2.2 * min(n, 8) + random.uniform(1.0, 4.0)
        return random.uniform(*d) if d else None

    def later(self, delay, fn, *args):
        def run(task):
            if self.act.phase == 'inside' and self.bot.state == 'present':
                try:
                    fn(*args)
                except Exception:
                    import traceback
                    bb.HUB.error('P8b boss %s' % fn.__name__, traceback.format_exc())
            return task.done
        taskMgr.doMethodLater(delay, run, 'botboss-%d-%d' % (self.bot.avId, random.randint(0, 1 << 30)))

    def send(self, field, args, obj=None):
        o = obj if obj is not None else self.run.boss()
        if o is not None:
            self.bot.send(field, args, doId=o.doId, className=o.className)

    def moveTo(self, x, y, z=None, anim='run'):
        b = self.bot
        z = self.pos[2] if z is None else z
        self.pos = Point3(x, y, z)
        dx, dy = x - b.pos[0], y - b.pos[1]
        b.pos = Point3(x, y, z)
        if dx * dx + dy * dy > 0.01:
            b.h = math.degrees(math.atan2(-dx, dy))
        b.dirty = True
        b.setAnim(anim)
        self.later(random.uniform(0.8, 1.6), b.setAnim, 'neutral')

    def stand(self, radius=(18, 34)):
        cx, cy, cz = BOSS_ARENA[self.kind]
        a = random.uniform(0, 2 * math.pi)
        r = random.uniform(*radius)
        self.moveTo(cx + r * math.cos(a), cy + r * math.sin(a), cz)
        self.faceBoss()

    def tossPie(self, power=None):
        """What every client sees: the toss (DistributedToon.tossPie, ownsend like any toon's own pie)."""
        b = self.bot
        p = b.pos
        ts = globalClockDelta.getFrameNetworkTime(bits=32)
        self.seq = (getattr(self, 'seq', 0) + 1) & 255
        b.send('presentPie', [p[0], p[1], p[2], b.h, 0, 0, ts])
        b.send('tossPie', [p[0], p[1], p[2], b.h, 0, 0, self.seq, power or random.randint(55, 95), ts])

    def hp(self):
        return bb.botHp(self.bot)

    # -- life --------------------------------------------------------------------------------------------------
    def stop(self):
        self.barriers.stop()
        if self.brain is not None and self.brain.phase != 'done':
            self.brain.leave('stop')
        self.brain = None
        self.run.release(self.bot.avId)

    def onDirect(self, fieldName, args):
        if fieldName == 'toonPromoted':
            self.run.log.count('promoted' if args[0] else 'not_promoted')
            if args[0]:
                self.run.promoted.append(self.bot.avId)
        elif fieldName in ('setCogMerits', 'setCogLevels', 'setCogTypes', 'setCogParts'):
            self.bot.ownFields[fieldName] = args
        elif fieldName == 'rejectGrab':
            self.item = None
        elif fieldName == 'setNumPies' and self.kind == 'cj':
            self.pies = args[0]                 # the witness stand's evidence, as the client's pie count
        if self.brain is not None:
            self.brain.onDirect(fieldName, args)

    def step(self, now):
        run, bot = self.run, self.bot
        boss = run.boss()
        if boss is None:
            return True
        if not self.entered:
            # CogHQBossBattle.enter: avatarEnter the moment the boss is there (its 5 s WaitForToons is running)
            self.entered = True
            self.send('avatarEnter', [])
            self.barriers.watch(boss)
            run.log.count('avatarEnter')
            return True
        st = run.state
        if st != self.lastState:
            self.onState(st, now)
            self.lastState = st
        # TEST skip: 'final' / 'skip' in BattleOne, or per state: 'One:skip,Three:final'
        if run.skipTo and now - run.stateT > 4.0 and st and st.startswith('Battle'):
            plan = dict(x.split(':', 1) for x in run.skipTo.split(',')) if ':' in run.skipTo else {'One': run.skipTo}
            word = plan.get(st[len('Battle'):])
            done = getattr(run, 'skipDone', set())
            if word and st not in done:
                db = bot.director.bots
                here = sorted(a for a in run.members if a in db and db[a].zoneId == run.zone)
                if here and here[0] == bot.avId:
                    run.skipDone = done | {st}
                    run.skipped = True
                    ok = sayMagicWord(bot, 'boss %s' % word, self.later)
                    run.log.note('TEST skip in %s: %s says ~boss %s (manager found %s)' % (st, bot.avId, word, ok))
        # battles the AI put me in
        if self.brain is not None:
            if not self.brain.step(now):
                res = self.brain.result
                run.log.count('battle_' + res)
                self.brain = None
                if res == 'died':
                    return self.__sad()
            return True
        for o in run.view.objects.values():
            if bb.isBattle(o) and o.get('setBossCogId') is not None and o.doId != self.lastBattle:
                m = o.get('setMembers')
                if m and bot.avId in m[6]:
                    self.lastBattle = o.doId
                    self.barriers.watch(o)
                    self.brain = bb.BattleBrain(bot, 'boss')
                    # the owner's budget rule: only a fight with a later gag round (VP / CEO round one) keeps
                    # its big gags back; the last gag round of a fight spends them
                    self.brain.lastGagRound = not (self.kind in ('vp', 'ceo') and 'One' in (st or ''))
                    self.brain.attach(o)
                    return True
        hp, mx = self.hp()
        if hp is not None and hp <= 0:
            return self.__sad()
        if st == 'Epilogue':
            if not self.outAt:
                self.outAt = now + random.uniform(12.0, 25.0)
                if self.kind == 'cfo':
                    self.later(random.uniform(3.0, 8.0), self.send, 'applyReward', [])
            elif now >= self.outAt:
                self.leave()
                return False
            return True
        if st in ('Off', 'Frolic', 'Defeat') and now - run.stateT > 15.0:
            self.leave()
            return False
        if now < self.next:
            return True
        fn = getattr(self, '%s_%s' % (self.kind, st), None)
        if fn is not None:
            fn(now)
        return True

    def onState(self, st, now):
        self.next = now + random.uniform(1.0, 3.0)
        self.near = False
        if st in ('BattleThree', 'NearVictory', 'BattleFour') or (self.kind == 'cfo' and st == 'BattleThree'):
            self.stand()
        if st == 'Victory' or st == 'Reward':
            self.run.release(self.bot.avId)
            self.crane = self.table = self.cannon = None

    def __sad(self):
        self.run.log.note('%s went sad in the %s' % (self.bot.avId, self.kind))
        self.run.log.count('sad')
        self.send('avatarExit', [])            # the client's died -> teleportOut leaves the place (CogHQBossBattle.exit)
        self.act.leaveTo(self.bot.home, 'sad')
        return False

    def leave(self):
        self.send('avatarExit', [])
        self.act.leaveTo(self.run.info['hq'], 'won' if self.run.won else 'over')

    # -- zaps: a kid does not dodge everything -----------------------------------------------------------------
    def maybeZap(self, now, dodge=0.6):
        code, avId, t = self.run.attack
        if code is None or now - t > 3.0 or now - self.zapped < 6.0:
            return
        hp, mx = self.hp()
        if hp is None or mx is None or hp < 0.35 * mx:
            return
        mine = code in (DIRECTED_ATTACK, SLOW_DIRECTED, 15, 17, 19) and avId == self.bot.avId
        if (mine and random.random() > dodge) or (code in (AREA_ATTACK, 18) and random.random() < 0.25):
            self.zapped = now
            p, b = self.bot.pos, self.bot
            c = BOSS_ARENA[self.kind]
            v = (p[0] - c[0], p[1] - c[1])
            n = math.hypot(*v) or 1.0
            self.send('zapToon', [p[0], p[1], p[2], b.h, 0, 0, v[0] / n, v[1] / n, code,
                                  globalClockDelta.getFrameNetworkTime()])
            self.run.log.count('zapped')

    # ======== VP =====================================================================================================
    def vp_BattleThree(self, now):
        run = self.run
        if run.damage >= 100:              # SellbotBossMaxDamage: the AI went NearVictory (not broadcast;
            return self.vpNear(now)        # every client sees it from setBossDamage and splats the last pie)
        if not self.pies:
            if not self.cageAt:
                self.cageAt = now + random.uniform(3.0, 7.0)       # run to the cage
                self.moveTo(random.uniform(-6, 6), -85 + random.uniform(-4, 4), -6.5)
            elif now >= self.cageAt:
                self.send('touchCage', [])
                self.pies = 65535
                self.stand((14, 30))
                run.log.count('touchCage')
            self.next = now + 1.0
            return
        if now < getattr(self, 'stunUntil', 0.0):
            self.next = self.stunUntil                       # knocked down: up again first
            return
        self.next = now + random.uniform(0.3, 0.5)          # a player watches the boss all the time
        a, r = self.vpPolar()
        near = r < 26.0
        if near != self.near:
            self.near = near
            self.send('avatarNearEnter' if near else 'avatarNearExit', [])
        # 1. the boss's attacks, each once, as it lands (BossCogAttackTimes lead-ins)
        code, target, t = run.attack
        if t > getattr(self, 'seenAttackT', -1.0):
            self.seenAttackT = t
            self.vpReact(code, target, a, r, now)
        # 2. the gear strafe: out of the fan, and a pie into the open door
        side, _dir, st, window = run.strafe
        if st > getattr(self, 'seenStrafeT', -1.0):
            self.seenStrafeT = st
            self.vpStrafe(side, st, window, a, r, now)
            return
        if now < getattr(self, 'busyUntil', 0.0):
            return
        # 3. dizzy: everyone pies the boss as fast as a player clicks
        if code == DIZZY_NOW:
            # the pie target is the tube on the boss's back (the +y side as it faces -y); the front is its shield
            if angDiff(a, 180.0) > 60.0:
                self.vpGoTo(180.0 + random.uniform(-50.0, 50.0), random.uniform(13.0, 20.0))
                return
            self.faceBoss()
            self.tossPie()
            run.log.count('pie_thrown')
            if random.random() < 0.8:
                self.send('hitBoss', [1])
                run.log.count('boss_hit')
            self.next = now + random.uniform(0.9, 1.5)
            return
        # 4. waiting for the next door: my spot off both gear fans, facing the boss; heal a low teammate
        if angDiff(a, self.vpSlot()) > 20.0 or not 14.0 <= r <= 30.0:
            self.vpGoTo(self.vpSlot(), random.uniform(17.0, 24.0))
            return
        self.faceBoss()
        low = self.__lowTeammate()
        if low is not None and random.random() < 0.15:
            self.tossPie(40)
            self.send('hitToon', [low])
            run.log.count('pie_heal')
            run.log.count('pie_thrown')
            self.next = now + random.uniform(1.5, 2.5)

    # -- VP helpers --------------------------------------------------------------------------------------------
    def vpBossPos(self):
        s = min(1.0, self.run.damage / 100.0) * (VP_SEG1 + VP_SEG2)
        if s <= VP_SEG1:
            f = s / VP_SEG1
            return Point3(0.0, VP_START[1] + f * (VP_BOTTOM[1] - VP_START[1]), VP_START[2] + f * (VP_BOTTOM[2] - VP_START[2]))
        f = (s - VP_SEG1) / VP_SEG2
        return Point3(0.0, VP_BOTTOM[1] + f * (VP_DEATH[1] - VP_BOTTOM[1]), VP_BOTTOM[2])

    def vpPolar(self):
        """My angle from the boss's front (it faces -y) and my distance."""
        c, p = self.vpBossPos(), self.bot.pos
        dx, dy = p[0] - c[0], p[1] - c[1]
        return math.degrees(math.atan2(dx, -dy)), math.hypot(dx, dy)

    def vpSlot(self):
        db = self.bot.director.bots
        here = sorted(a for a in self.run.members if a in db and db[a].zoneId == self.run.zone)
        i = here.index(self.bot.avId) if self.bot.avId in here else 0
        return VP_SLOTS[i % len(VP_SLOTS)]

    def vpGoTo(self, angle, dist):
        c = self.vpBossPos()
        x = c[0] + dist * math.sin(math.radians(angle))
        y = c[1] - dist * math.cos(math.radians(angle))
        self.moveTo(x, y, c[2])                        # up the ramp onto the platform with it, or on the floor
        self.faceBoss()
        self.busyUntil = globalClock.getRealTime() + random.uniform(0.8, 1.6)

    def faceBoss(self):
        if self.kind == 'vp' and self.run.state in ('BattleThree', 'NearVictory'):
            self.bot.faceTo(self.vpBossPos())
            return
        o = self.run.boss()
        if o is not None and (o.pos[0] or o.pos[1]):
            self.bot.faceTo(o.pos)
        else:
            self.bot.faceTo(BOSS_ARENA[self.kind])

    def vpZap(self, code, what, delay=0.0):
        """Hit: the client's zapToon (the AI takes the laff), knocked down for a moment, then up again."""
        def hit():
            b = self.bot
            c, p = self.vpBossPos(), b.pos
            v = (p[0] - c[0], p[1] - c[1])
            n = math.hypot(*v) or 1.0
            self.send('zapToon', [p[0], p[1], p[2], b.h, 0, 0, v[0] / n, v[1] / n, code,
                                  globalClockDelta.getFrameNetworkTime()])
            self.run.log.count(what)
            self.run.log.count('stunned')
            self.stunUntil = globalClock.getRealTime() + random.uniform(2.0, 3.2)
        self.later(delay, hit)

    def vpReact(self, code, target, a, r, now):
        run = self.run
        if code == AREA_ATTACK:                          # the jump: jump in time or get shocked
            def jump():
                if random.random() < 0.85:
                    self.bot.setAnim('jump', force=True)
                    self.later(1.1, self.bot.setAnim, 'neutral')
                    run.log.count('jumped')
                else:
                    self.vpZap(AREA_ATTACK, 'area_hit')
            self.later(random.uniform(2.6, 3.3), jump)       # the stomp lands ~3.5 s into its 4.2 s
        elif code == FRONT_ATTACK and angDiff(a, 0.0) < 50.0 and r < 30.0:
            if random.random() < 0.55:
                self.vpGoTo(self.vpSlot(), random.uniform(18.0, 24.0))
                run.log.count('front_dodge')
            else:
                self.vpZap(FRONT_ATTACK, 'front_hit', random.uniform(1.2, 1.8))
        elif code in (DIRECTED_ATTACK, SLOW_DIRECTED) and target == self.bot.avId:
            if random.random() < 0.65:                   # sidestep the thrown gear
                self.vpGoTo(a + random.choice((-35.0, 35.0)), r)
                run.log.count('directed_dodge')
            else:
                self.vpZap(code, 'directed_hit', random.uniform(1.5, 2.5))
        elif code == RECOVER_DIZZY and r < 16.0 and random.random() < 0.5:
            self.vpZap(RECOVER_DIZZY, 'recover_hit', random.uniform(0.5, 1.2))
        elif code == DIZZY_NOW:
            run.log.count('dizzy')

    def vpStrafe(self, side, st, window, a, r, now):
        """A door opens (side 1 = the front, side 0 = the back) and gears fan out +-30 degrees for `window` s:
        whoever is in the fan steps aside (or is hit); whoever sees the door pies it (one hit makes it dizzy)."""
        run = self.run
        door = 0.0 if side == 1 else 180.0
        if angDiff(a, door) < 32.0 and r < 52.0:
            if random.random() < 0.7:
                self.vpGoTo(door + random.choice((-48.0, 48.0)), max(15.0, min(r, 24.0)))
                run.log.count('gear_dodge')
            else:
                self.vpZap(STRAFE_ATTACK, 'gear_hit', random.uniform(0.8, 0.7 + window))
                return
        if angDiff(a, door) > 80.0:
            return                                       # the door is round the other side

        def pieDoor():
            if run.attack[0] == DIZZY_NOW or globalClock.getRealTime() - st > 0.7 + window \
                    or globalClock.getRealTime() < getattr(self, 'stunUntil', 0.0):
                return
            self.faceBoss()
            self.tossPie()
            run.log.count('pie_thrown')
            run.log.count('door_throw')
            if random.random() < 0.45 and run.insidesT < st:
                run.insidesT = globalClock.getRealTime()
                self.send('hitBossInsides', [])
                run.log.count('insides_hit')
        self.later(random.uniform(0.8, min(2.2, 0.6 + window)), pieDoor)
        if random.random() < 0.6:
            self.later(random.uniform(1.6, min(3.4, 0.7 + window)), pieDoor)
        self.busyUntil = now + min(3.5, 0.7 + window)

    def vp_NearVictory(self, now):
        self.vpNear(now)

    def __lowTeammate(self):
        boss = self.run.boss()
        ids = (boss.get('setToonIds') or ([],))[0] if boss is not None else []
        best = None
        for a in ids:
            if a == self.bot.avId:
                continue
            hp, mx = bb.toonHp(self.bot.air, a)
            if hp is not None and mx and 0 < hp < 0.5 * mx and (best is None or hp < best[0]):
                best = (hp, a)
        return best[1] if best else None

    def vpNear(self, now):
        """NearVictory: the boss is beaten; the kid whose pie lands last sends finalPieSplat (no sender check).
        With a real player in the fight his client gets the first chance."""
        run = self.run
        if not getattr(run, 'nearT', 0):
            run.nearT = now
        self.next = now + 1.0
        if getattr(self, 'finalSent', False) or now - run.nearT < (10.0 if run.realIn() else random.uniform(2.5, 5.0)):
            return
        db = self.bot.director.bots
        bots = sorted(a for a in run.members if a in db and db[a].zoneId == run.zone)
        if bots and bots[0] == self.bot.avId:
            self.finalSent = True
            self.tossPie()
            self.send('finalPieSplat', [])
            run.log.count('finalPieSplat')
    # ======== CFO ====================================================================================================
    def cfo_BattleThree(self, now):
        run = self.run
        self.maybeZap(now, dodge=0.7)
        self.next = now + random.uniform(0.8, 1.6)
        # real toons first: a free crane is kept for every real toon without one; if none is free, a bot steps off
        cranes = run.ofClass('DistributedCashbotBossCrane')
        boss = run.boss()
        ids = (boss.get('setToonIds') or ([],))[0] if boss is not None else []
        real = [a for a in ids if a not in self.bot.director.bots]
        onCrane = set((c.get('setState') or ('F', 0))[1] for c in cranes if (c.get('setState') or ('F', 0))[0] == 'C')
        realNeed = [a for a in real if a not in onCrane]
        free = [c for c in cranes if (c.get('setState') or ('F', 0))[0] == 'F']
        if self.crane is not None and realNeed and not free:
            mine = [a for a in onCrane if a in self.bot.director.bots]
            if mine and max(mine) == self.bot.avId and now - getattr(run, 'stepOffT', 0.0) > 4.0:
                run.stepOffT = now
                c = run.view.objects.get(self.crane)
                if c is not None:
                    self.send('requestFree', [], c)
                    run.log.count('crane_stepoff')
                    run.log.note('bot %s steps off crane %s for real toon %s' % (self.bot.avId, self.crane, realNeed[0]))
                run.release(self.bot.avId)
                self.crane = None
                self.item = None
                return
        if self.crane is None:
            if len(free) <= len(realNeed):
                if realNeed and free:
                    run.log.count('crane_kept_for_real')
                self.__stomp(now)
                return
            for c in sorted(cranes, key=lambda o: o.doId):
                st = c.get('setState') or ('F', 0)
                if st[0] == 'F' and run.claim(('crane', c.doId), self.bot.avId):
                    self.crane = c.doId
                    self.item = None
                    self.moveTo(c.pos[0] or BOSS_ARENA['cfo'][0], c.pos[1] or BOSS_ARENA['cfo'][1] - 20, 0)
                    self.send('requestControl', [], c)
                    self.craneAt = now + random.uniform(2.0, 4.0)
                    run.log.count('crane_take')
                    return
            self.__stomp(now)
            return
        crane = run.view.objects.get(self.crane)
        if crane is None:
            self.crane = None
            return
        st = crane.get('setState') or ('F', 0)
        if st[0] != 'C' or st[1] != self.bot.avId:
            if now > getattr(self, 'craneAt', 0) + 6.0:
                run.release(self.bot.avId)
                self.crane = None
            return
        if now < getattr(self, 'craneAt', 0):
            return
        self.__craneWork(now)

    def cfoHelmetTick(self, boss):
        on = self.__helmetOn(boss)
        run = self.run
        if on != getattr(run, 'helmet', False):
            run.helmet = on
            run.log.count('helmet_on' if on else 'helmet_off')
            if run.lastT0 is not None:
                run.log.note('CFO helmet %s at %d s (damage %d)' % ('ON' if on else 'OFF', now_() - run.lastT0, run.damage))
        return on

    def cfoHelmetBot(self):
        """The one crane bot whose job is the helmet: the lowest avId holding a crane."""
        cranes = [a for k, a in self.run.claimed.items() if k[0] == 'crane']
        return min(cranes) if cranes else None

    def __helmetOn(self, boss):
        for s in self.run.ofClass('DistributedCashbotBossSafe'):
            os_ = s.get('setObjectState') or ('I', 0, 0)
            if os_[0] == 'G' and os_[1] == boss.doId:
                return True
        return False

    def __craneWork(self, now):
        run = self.run
        boss = run.boss()
        me = self.bot.avId
        crane = self.crane
        if self.item is not None:
            obj = run.view.objects.get(self.item[0])
            ost = obj.get('setObjectState') if obj is not None else None
            if obj is None or ost is None or ost[1] != me:
                if now > self.item[2]:
                    self.item = None
                return
            if ost[0] != 'G':
                return
            if now < self.item[2]:
                return
            # swing it at the boss: a good swing most of the time
            dizzy = run.attack[0] == DIZZY
            kind = self.item[1]
            helmet = self.cfoHelmetTick(boss)
            if kind == 'goon' and helmet and not dizzy and now < self.item[2] + 12.0:
                return                                   # a goon only bounces off the helmet: hold it till it's off
            if kind == 'safe' and not helmet and not dizzy:
                # a safe on a bare, awake head becomes its new helmet: keep it swinging until it's dizzy
                if now < self.item[2] + 15.0:
                    return
                self.send('requestDrop', [], obj)
                self.later(random.uniform(0.8, 1.5), self.__dropAndFree, obj.doId, obj.className)
                run.log.count('safe_held_back')
                self.item = None
                return
            good = random.random() < (0.7 if kind == 'goon' else 0.75)
            if good and (kind == 'goon' and not helmet or kind == 'safe'):
                impact = random.uniform(0.6, 1.0)
                self.send('hitBoss', [impact], obj)
                run.log.count('cfo_hit_' + kind + ('_helmet' if helmet and kind == 'safe' else ''))
                self.item = None
                self.next = now + random.uniform(3.0, 6.0)
                if kind == 'safe':
                    self.later(random.uniform(1.5, 3.0), self.__dropAndFree, obj.doId, obj.className)
            else:
                self.send('requestDrop', [], obj)
                self.later(random.uniform(0.8, 1.5), self.__dropAndFree, obj.doId, obj.className)
                run.log.count('cfo_miss')
                self.item = None
                self.next = now + random.uniform(2.0, 4.0)
            return
        # pick something up: a safe when it is dizzy; the helmet bot takes a safe for the helmet; else a goon
        helmet = self.cfoHelmetTick(boss)
        want = 'safe' if (run.attack[0] == DIZZY or (helmet and self.cfoHelmetBot() == me)) else 'goon'
        cands = []
        if want == 'safe':
            for s in run.ofClass('DistributedCashbotBossSafe'):
                idx = (s.get('setIndex') or (0,))[0]
                ost = s.get('setObjectState') or ('I', 0, 0)
                if idx != 0 and ost[0] in ('I', 'F') and run.claim(('obj', s.doId), me):
                    cands.append((s, 'safe'))
        else:
            for g in run.ofClass('DistributedCashbotBossGoon'):
                ost = g.get('setObjectState') or ('W', 0, 0)
                if ost[0] in ('W', 'a', 'b', 'S', 's') and run.claim(('obj', g.doId), me):
                    cands.append((g, 'goon'))
        heldByReal = [o for o in run.ofClass('DistributedCashbotBossSafe') + run.ofClass('DistributedCashbotBossGoon')
                      if (o.get('setObjectState') or ('I', 0))[0] == 'G'
                      and (o.get('setObjectState') or ('I', 0))[1] not in self.bot.director.bots
                      and (o.get('setObjectState') or ('I', 0))[1] != (boss.doId if boss is not None else 0)]
        if heldByReal:
            run.log.count('left_for_real_magnet')        # a real toon's magnet has it: never ours to take
        cands = [(o, k) for o, k in cands if o not in heldByReal]
        if not cands:
            return
        obj, kind = random.choice(cands)
        self.send('requestGrab', [], obj)
        self.item = (obj.doId, kind, now + random.uniform(3.5, 7.0))     # the swing takes a moment
        run.log.count('cfo_grab_' + kind)

    def __dropAndFree(self, doId, cls):
        o = self.run.view.objects.get(doId) if self.run.view is not None else None
        if o is None:
            return
        ost = o.get('setObjectState') or ('F', 0, 0)
        if ost[1] != self.bot.avId:
            return
        if ost[0] == 'G':
            self.bot.send('requestDrop', [], doId=doId, className=cls)
        self.later(0.8, self.bot.send, 'hitFloor', [], doId, cls)
        x, y = o.pos[0] or BOSS_ARENA['cfo'][0] + random.uniform(-30, 30), o.pos[1] or BOSS_ARENA['cfo'][1] + random.uniform(-30, 30)
        self.later(2.0, self.bot.send, 'requestFree', [x, y, 0, random.uniform(0, 359)], doId, cls)
        self.run.claimed.pop(('obj', doId), None)

    def __stomp(self, now):
        """No crane: stomp a goon near me (my position goes out first: the AI measures the distance)."""
        run = self.run
        goons = [g for g in run.ofClass('DistributedCashbotBossGoon')
                 if (g.get('setObjectState') or ('W',))[0] == 'W']
        treas = [t for t in run.ofClass('DistributedCashbotBossTreasure') if not (t.get('setGrab') or (0,))[0]]
        hp, mx = self.hp()
        if treas and hp is not None and mx and hp < 0.8 * mx and random.random() < 0.5:
            t = random.choice(treas)
            p = t.get('setFinalPosition') or t.get('setPosition') or (0, 0, 0)
            self.moveTo(p[0], p[1], 0)
            self.later(random.uniform(0.6, 1.2), self.send, 'requestGrab', [], t)
            run.log.count('treasure')
            self.next = now + random.uniform(3.0, 5.0)
            return
        if not goons or random.random() < 0.5:
            if random.random() < 0.3:
                self.stand((25, 45))
            return
        g = random.choice(goons)
        gp, since = goonNow(g)
        self.moveTo(gp[0] + random.uniform(-3, 3), gp[1] + random.uniform(-3, 3), 0)
        self.bot.broadcastNow()
        self.later(random.uniform(0.2, 0.5), self.__stun, g.doId, since)
        self.next = now + random.uniform(4.0, 8.0)

    def __stun(self, doId, since):
        g = self.run.view.objects.get(doId) if self.run.view is not None else None
        if g is None:
            return
        gp, since = goonNow(g)
        self.moveTo(gp[0] + random.uniform(-2, 2), gp[1] + random.uniform(-2, 2), 0)
        self.bot.broadcastNow()
        self.bot.send('requestStunned', [min(since, 3000.0)], doId=doId, className=g.className)
        self.run.log.count('goon_stomp')

    # ======== CJ =====================================================================================================
    def cj_BattleTwo(self, now):
        run = self.run
        self.next = now + random.uniform(0.8, 1.5)
        if self.cannon is None:
            # real toons first: one free cannon kept for every real toon without one
            bots = self.bot.director.bots
            cannons = run.ofClass('DistributedLawbotCannon')
            realIn = set(m[1] for m in (c.get('setMovie') for c in cannons) if m and m[1] and m[1] not in bots)
            boss = run.boss()
            ids = (boss.get('setToonIds') or ([],))[0] if boss is not None else []
            realNeed = [a for a in ids if a not in bots and a not in realIn]
            free = [c for c in cannons if ('cannon', c.doId) not in run.claimed
                    and not ((c.get('setMovie') or (0, 0))[1] in realIn)]
            if len(free) <= len(realNeed):
                if realNeed:
                    run.log.count('cannon_kept_for_real')
                self.stand((20, 40))
                self.next = now + 3.0
                return
            for c in sorted(free, key=lambda o: o.doId):
                if run.claim(('cannon', c.doId), self.bot.avId):
                    self.cannon = c.doId
                    ph = c.get('setPosHpr') or (0, 0, 0, 0, 0, 0)
                    self.moveTo(ph[0], ph[1], ph[2])
                    self.later(random.uniform(1.0, 2.5), self.send, 'requestEnter', [], c)
                    self.balls = 12
                    self.aimAt = now + random.uniform(4.0, 7.0)
                    run.log.count('cannon_enter')
                    return
            self.stand((20, 40))
            self.next = now + 10.0
            return
        c = run.view.objects.get(self.cannon)
        if c is None or self.balls <= 0:
            return
        if now < self.aimAt:
            if random.random() < 0.5:
                self.send('setCannonPosition', [random.uniform(-40, 40), random.uniform(20, 60)], c)
            return
        # fire at an empty juror chair nobody else is aiming at (a player lines up one chair, then fires)
        me = self.bot.avId
        realAim = cjRealAimChairs(run, self.bot.director.bots)
        if realAim:
            run.log.count('chair_left_for_real')
        chairs = [ch for ch in run.ofClass('DistributedLawbotChair') if (ch.get('setState') or ('',))[0] == 'E'
                  and run.claimed.get(('chair', ch.doId), me) == me and ch.doId not in realAim]
        if not chairs:
            self.next = now + 2.0
            return
        ch = random.choice(chairs)
        run.claimed[('chair', ch.doId)] = me
        cp = ch.pos if (ch.pos[0] or ch.pos[1]) else Point3(random.uniform(-40, 40), 60, 20)
        bp = self.bot.pos
        z = math.degrees(math.atan2(-(cp[0] - bp[0]), cp[1] - bp[1]))
        a = random.uniform(30, 50)
        self.send('setCannonPosition', [z, a], c)
        self.later(random.uniform(0.6, 1.2), self.send, 'setCannonLit', [z, a], c)
        self.balls -= 1
        run.log.count('cannon_shot')
        idx = (c.get('setIndex') or (0,))[0]
        flight = 2.0 + random.uniform(1.5, 2.5)          # the fuse, then the flight
        if random.random() < 0.65:
            self.later(flight, self.__chairHit, ch.doId, idx)
        else:
            run.log.count('cannon_miss')
            self.later(flight, run.claimed.pop, ('chair', ch.doId), None)
        self.later(flight + random.uniform(0.5, 1.0), self.send, 'setLanded', [], c)
        self.aimAt = now + flight + random.uniform(3.0, 6.0)

    def __chairHit(self, chairDoId, cannonIndex):
        ch = self.run.view.objects.get(chairDoId)
        if ch is None or (ch.get('setState') or ('',))[0] != 'E':
            return
        self.send('hitChair', [(ch.get('setIndex') or (0,))[0], cannonIndex])
        self.run.log.count('juror_seated')

    def cj_BattleThree(self, now):
        """The scale (P8c, the 2013 balance: 1350 -> 2700, one point a pan hit, x2 in the bonus; each lawyer who
        prosecutes adds 2 to the other pan 5.65 s later unless a pie stuns him inside 3.15 s). A competent team:
        pies on the defense pan at a player's throw rate, someone watching the lawyers stuns each one who
        winds up to prosecute, and now and then the whole team stuns every lawyer for the bonus."""
        run = self.run
        if self.pies <= 0:
            if not self.cageAt:
                self.cageAt = now + random.uniform(3.0, 5.5)       # to the witness stand
                self.moveTo(48 + random.uniform(-4, 4), 100 + random.uniform(-6, 6), 0)
            elif now >= self.cageAt:
                self.cageAt = 0.0
                self.send('touchWitnessStand', [])
                self.pies = random.randint(22, 38)                 # until setNumPies says (the AI's ammoCount)
                self.stand((20, 40))
                run.log.count('evidence')
            self.next = now + 0.8
            return
        self.maybeZap(now)
        self.next = now + random.uniform(0.9, 1.5)                 # a player holding the throw key, aiming
        target = cjStunTarget(run, self.bot.avId, now)
        if target is not None:
            o = run.view.objects.get(target)
            if o is not None:
                self.bot.faceTo(o.pos)
                self.tossPie()
                self.pies -= 1
                if random.random() < 0.85:
                    self.bot.send('hitByToon', [], doId=target, className=o.className)
                    run.log.count('lawyer_stun')
                return
        self.faceBoss()
        self.tossPie()
        self.pies -= 1
        if random.random() < 0.8:                                  # the pan is a big target; a few go wide
            self.send('hitBoss', [1])
            run.log.count('pan_hit')
        low = self.__lowTeammate()
        if low is not None and random.random() < 0.15:
            self.later(0.5, self.tossPie, 40)
            self.later(0.9, self.send, 'hitToon', [low])
            run.log.count('pie_heal')

    # ======== CEO ====================================================================================================
    def ceo_BattleTwo(self, now):
        run = self.run
        me = self.bot.avId
        self.next = now + random.uniform(1.0, 2.0)
        if self.item is None:
            belt = random.choice((0, 1))
            run.foodNum[belt] += 1
            self.moveTo(random.choice((-15, 15)) + random.uniform(-3, 3), 260 + random.uniform(-20, 20), 0)
            self.later(random.uniform(2.0, 4.0), self.send, 'requestGetFood', [belt, random.randint(0, 3), run.foodNum[belt]])
            self.item = ('food', now + random.uniform(6.0, 10.0))
            return
        if now < self.item[1]:
            return
        # a hungry (or angry) diner nobody is serving yet
        choices = []
        for t in run.ofClass('DistributedBanquetTable'):
            n = (t.get('setNumDiners') or (0,))[0]
            st = DINERS.get(t.doId)
            for i in range(n):
                s = (st or {}).get(i, 1)
                if s in (1, 3) and run.claim(('diner', t.doId, i), me):
                    choices.append((t, i))
        if not choices:
            self.next = now + 3.0
            return
        t, i = random.choice(choices)
        tix = (t.get('setIndex') or (0,))[0]
        self.send('requestServeFood', [tix, i])
        run.claimed.pop(('diner', t.doId, i), None)
        run.log.count('served')
        self.item = None

    def ceo_BattleFour(self, now):
        """The CEO's last round: seltzer from the banquet tables (hitBoss 1-3, doubled by the AI), golf balls from
        the golf spots to slow it down (ballHitBoss), knocked off a table when it rolls onto it (Flat), back on
        a free one afterwards. Real toons first: a free table and a free golf spot are kept for each real toon
        who has neither. One golf bot per two table bots."""
        run = self.run
        me = self.bot.avId
        bots = self.bot.director.bots
        self.maybeZap(now, dodge=0.65)
        self.next = now + random.uniform(1.2, 2.2)
        tables = run.ofClass('DistributedBanquetTable')
        spots = run.ofClass('DistributedGolfSpot')
        boss = run.boss()
        ids = (boss.get('setToonIds') or ([],))[0] if boss is not None else []
        seated = set((o.get('setState') or ('', 0))[1] for o in tables + spots if (o.get('setState') or ('',))[0] in ('C', 'L'))
        realNeed = [a for a in ids if a not in bots and a not in seated]
        # at my station
        if self.table is not None:
            o = run.view.objects.get(self.table)
            st = o.get('setState') if o is not None else None
            golf = o is not None and o.className == 'DistributedGolfSpot'
            if st is None or st[1] != me or st[0] not in ('C', 'L'):
                if now > getattr(self, 'tableAt', 0) + 5.0:
                    if st is not None and st[0] in ('R', 'F') and getattr(self, 'hadIt', False):
                        run.log.count('knocked_off' if not golf else 'golf_knocked_off')
                    run.release(me)
                    self.table = None
                    self.hadIt = False
                    self.next = now + random.uniform(3.0, 6.0)          # knocked off: back on my feet
                return
            self.hadIt = True
            if st[0] == 'L':
                return                                                  # flattened under the boss: wait
            if realNeed and not [x for x in tables + spots if (x.get('setState') or ('',))[0] in ('R', 'F')]                     and max(a for a in seated if a in bots) == me:
                self.send('requestFree', [0], o)                        # a real toon has nowhere: I step off
                run.log.count('station_stepoff')
                run.log.note('bot %s steps off %s for real toon %s' % (me, o.doId, realNeed[0]))
                run.release(me)
                self.table = None
                return
            if golf:
                self.next = now + random.uniform(3.5, 6.0)              # line up, swing
                self.send('setSwingInfo', [1, random.uniform(-30, 30), random.randint(0, 255)], o)
                if random.random() < 0.6:
                    self.send('ballHitBoss', [random.choice((1, 2))])
                    run.log.count('ball_hit')
                else:
                    run.log.count('ball_miss')
                return
            self.next = now + random.uniform(2.5, 4.0)                  # aim, charge the seltzer, fire
            if random.random() < 0.6:
                self.send('hitBoss', [random.choice((1, 2, 2, 3))])
                run.log.count('squirt_hit')
            else:
                run.log.count('squirt_miss')
            return
        freeT = [t for t in tables if (t.get('setState') or ('',))[0] == 'R' and ('table', t.doId) not in run.claimed]
        freeG = [g for g in spots if (g.get('setState') or ('',))[0] == 'F' and ('table', g.doId) not in run.claimed]
        if realNeed and len(freeT) + len(freeG) <= len(realNeed):
            run.log.count('station_kept_for_real')
            self.stand((25, 45))
            return
        onGolf = len([g for g in spots if (g.get('setState') or ('',))[0] == 'C' and (g.get('setState') or ('', 0))[1] in bots])
        onTable = len([t for t in tables if (t.get('setState') or ('',))[0] in ('C', 'L') and (t.get('setState') or ('', 0))[1] in bots])
        pool = (freeG if freeG and onGolf * 2 < max(1, onTable) else []) or freeT or freeG
        if realNeed:
            pool = freeG                          # a real toon may be walking to any free table: never race him
        for o in pool:
            if run.claim(('table', o.doId), me):
                self.table = o.doId
                self.tableAt = now
                self.moveTo(o.pos[0] or random.uniform(-40, 40), o.pos[1] or random.uniform(280, 360), 0)
                self.later(random.uniform(1.5, 3.0), self.send, 'requestControl', [], o)
                run.log.count('golf_take' if o.className == 'DistributedGolfSpot' else 'table_take')
                return
        self.stand((25, 45))


# ---- what the clients compute from broadcasts (the view keeps only each field's last value) ---------------------
GOONS = {}                 # goon doId -> (legStart realtime, (x0, y0), (x1, y1), legSeconds)
DINERS = {}                # banquet table doId -> {chair: status}


def goonNow(g):
    """Where a CFO goon is now and how long it has walked this leg (DistributedCashbotBossGoon walks the
    straight line from where it was to setTarget, arriving at the given time)."""
    leg = GOONS.get(g.doId)
    if leg is None:
        return (g.pos[0], g.pos[1]), 0.0
    t0, (x0, y0), (x1, y1), span = leg
    el = globalClock.getRealTime() - t0
    f = min(1.0, max(0.0, el / span))
    return (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f), el


def _goonField(obj, fieldName, args):
    if fieldName == 'setTarget':
        here, _ = goonNow(obj)
        try:
            span = globalClockDelta.networkToLocalTime(args[3], bits=16) - globalClock.getRealTime()
        except Exception:
            span = 5.0
        GOONS[obj.doId] = (globalClock.getRealTime(), here, (args[0], args[1]), max(0.3, span))
    elif fieldName in ('setPosHpr', 'setPos') or fieldName == '__exit__':
        GOONS.pop(obj.doId, None)


def _dinerField(obj, fieldName, args):
    if fieldName == 'setDinerStatus':
        DINERS.setdefault(obj.doId, {})[args[0]] = args[1]
    elif fieldName == '__exit__':
        DINERS.pop(obj.doId, None)


LAWYERS = {}               # CJ lawyer doId -> {'pros': when it began to prosecute, 'stun': stunned until, 'by': avId}
BONUS = {}                 # CJ boss doId -> when the last all-stun bonus began (enteredBonusState)


def _lawyerField(obj, fieldName, args):
    now = globalClock.getRealTime()
    if fieldName == 'doProsecute':
        LAWYERS.setdefault(obj.doId, {})['pros'] = now
    elif fieldName == 'doStun':
        e = LAWYERS.setdefault(obj.doId, {})
        e['stun'] = now + 5.0                           # LawbotBossLawyerStunTime
        e.pop('pros', None)
    elif fieldName == '__exit__':
        LAWYERS.pop(obj.doId, None)


def cjChairPos(ch):
    from toontown.toonbase import ToontownGlobals as TG
    i = (ch.get('setIndex') or (0,))[0]
    p = TG.LawbotBossChairPosHprs[i % len(TG.LawbotBossChairPosHprs)]
    return Point3(p[0], p[1], p[2])


def cjRealAimChairs(run, bots):
    """The empty chair each real toon's cannon points at (its heading + the aim it broadcasts)."""
    out = set()
    chairs = [ch for ch in run.ofClass('DistributedLawbotChair') if (ch.get('setState') or ('',))[0] == 'E']
    if not chairs:
        return out
    for c in run.ofClass('DistributedLawbotCannon'):
        u = c.get('updateCannonPosition')
        if not u or u[0] in bots:
            continue
        ph = c.get('setPosHpr') or (0, 0, 0, 0, 0, 0)
        heading = ph[3] + u[1]
        best = min(chairs, key=lambda ch: angDiff(heading, math.degrees(math.atan2(-(cjChairPos(ch)[0] - ph[0]),
                                                                                  cjChairPos(ch)[1] - ph[1]))))
        if best.doId not in out and getattr(run, 'lastRealAim', {}).get(u[0]) != best.doId:
            run.lastRealAim = getattr(run, 'lastRealAim', {})
            run.lastRealAim[u[0]] = best.doId
            run.log.note('real toon %s aims cannon %s at chair %s: bots leave it' % (u[0], c.doId, best.doId))
        out.add(best.doId)
    return out


def cjStunTarget(run, me, now):
    """The lawyer I pie now, or None. One toon answers each lawyer who winds up to prosecute (a human sees it
    after ~0.8-2.5 s); every ~60 s with the bonus ready the team stuns every lawyer at once."""
    boss = run.boss()
    lawyers = list((boss.get('setLawyerIds') or ([],))[0]) if boss is not None else []
    if not lawyers:
        return None
    live = [l for l in lawyers if LAWYERS.get(l, {}).get('stun', 0.0) < now]
    # the bonus: the last one was over 60 s ago (LawbotBossBonusWaitTime) and the team calls it
    lastBonus = BONUS.get(boss.doId, run.stateT)
    call = getattr(run, 'bonusCall', 0.0)
    if now - lastBonus > 62.0 and now - call > 25.0 and random.random() < 0.08:
        run.bonusCall = call = now
        run.log.count('bonus_call')
    if now - call < 6.0 and live:
        mine = [l for l in live if run.claim(('stun', l, int(call)), me)]
        if mine:
            return mine[0]
    for l in live:
        e = LAWYERS.get(l, {})
        pros = e.get('pros')
        if pros is None or now - pros > 3.0:
            continue
        react = e.get('react')
        if react is None or react[0] != pros:
            react = e['react'] = (pros, random.uniform(0.8, 2.5))
        if now - pros >= react[1] and run.claim(('pros', l, int(pros * 10)), me):
            return l
    return None


def _cjBossField(obj, fieldName, args):
    if fieldName == 'enteredBonusState':
        BONUS[obj.doId] = globalClock.getRealTime()
    elif fieldName == '__exit__':
        BONUS.pop(obj.doId, None)


bb.HUB.classHooks.setdefault('DistributedLawbotBossSuit', []).append(_lawyerField)
bb.HUB.classHooks.setdefault('DistributedLawbotBoss', []).append(_cjBossField)
bb.HUB.classHooks.setdefault('DistributedCashbotBossGoon', []).append(_goonField)
bb.HUB.classHooks.setdefault('DistributedBanquetTable', []).append(_dinerField)