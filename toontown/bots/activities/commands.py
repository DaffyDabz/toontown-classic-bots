"""TTBOTS P9: a REAL player commands bots with the game's own 2013 SpeedChat (TTBOTS-RECON 6b: "Bots take
commands from the player, e.g. follow me, can you help with this ToonTask"; "Commands arrive through the
game's own SpeedChat"). Said aloud near bots (setSC / setSCToontask broadcasts in a bot view) or whispered
to one bot, friend or not (setWhisperSCFrom / setWhisperSCToontaskFrom on its owner channel, via
BotToon.COMMAND_HOOKS).

  "Follow me."            aloud: the nearest free bot (sometimes 2, at most 3) says "OK" and follows him;
                          whispered: that bot follows him, from wherever it is (a friend in another zone asks
                          him with teleportQuery, as a client does, and teleports in on his answer).
                          It follows him EVERYWHERE, the way a toon does: walks behind him with a natural gap
                          (never snaps), through the tunnel he took (out of the matching mouth on the other
                          side), in and out of the building door he took (the real door protocol), onto the
                          trolley he boards (a seat, his minigames, his Play Again / Exit choice), into his
                          street battles (toonRequestJoin), into the Cog building / Cog HQ elevator he
                          boards (the P8 group code), and to him when he teleports (TeleportOut / In).
  "Wait here." / "Stay."  stops and stands on the spot until "Follow me." again, "Bye!" or 3 minutes.
  "Bye!"                  the bots he commands go back to their own life.
  ToonTask lines          his ToonTask SpeedChat (setSCToontask: the task he picked in the menu) or "Let's work
                          on that." / "I'm going to look for that." / "I haven't found it yet." / "Can you help
                          me?" / "I need help with the Cogs!": near bots answer; 1-2 follow him and help with the
                          task read from his toon (setQuests, as the menu shows it): they join his battles and,
                          on a street, walk up to a Cog the task counts (Quests' own doesCogCount / holder
                          rules) near him and start the battle for him to join.
  "Let's go ..." lines    the bots following him do it with him: the trolley (1100), fight the Cogs (1102,
                          1702), back to the playground (1101), a playground by name (1105-1111, 1125, 1126).

An ORDER (bot, leader, mode) lives in ORDERS until released; the bot runs the 'command' activity while it is
free and hands over to the real activities (trolley, battle, cogbuilding, coghq, boss) when he does those
things; the order re-attaches when they end. Numbers: <bot-run-dir>/bots-p9.json.
"""
import json
import math
import os
import random
import time
import traceback

from direct.directnotify import DirectNotifyGlobal
from direct.showbase.DirectObject import DirectObject
from panda3d.core import Point3

from toontown.bots import BotToon as BT
from toontown.bots import battlebrain as bb
from toontown.bots.activities import Activity, register
from toontown.bots.activities import lifekit as kit
from toontown.bots.BotToon import RUN_SPEED, WALK_SPEED, gaitFor
from toontown.bots.BotTravel import BotTravel, QUIET, TELEPORT_IN_TIME

notify = DirectNotifyGlobal.directNotify.newCategory('BotCommands')

SC_OK = 3
SC_AFTER = 1011          # "Wait a minute." (sweep fix: whispered in a trolley game, comes after it)
TP_UNANSWERED = 2        # sweep fix: teleportQuery unanswered this many times running = he logged out
SC_FOLLOW = 1006
SC_WAIT = (1010, 21002)
SC_BYE = (200, 207)
SC_TASK = (1201, 1203, 1205, 514, 2205)          # + every setSCToontask line
SC_TROLLEY = 1100
SC_FIGHT = (1102, 1702)
SC_PLAYGROUND = 1101
SC_GOTO = {1105: 2000, 1106: 1000, 1107: 4000, 1108: 5000, 1109: 3000, 1110: 9000, 1111: 8000, 1125: 6000,
           1126: 17000}
COMMAND_IDS = set((SC_FOLLOW, SC_TROLLEY, SC_PLAYGROUND) + SC_WAIT + SC_BYE + SC_TASK + SC_FIGHT) | set(SC_GOTO)
kit.NO_ANSWER.update(set((SC_FOLLOW,) + SC_WAIT + SC_TASK))   # the bots he commands answer these, nobody else

HEAR = 40.0              # ft: "Follow me." aloud reaches bots this close
HEAR_WAIT = 90.0         # "Wait here." / "Bye!" reach his own bots this far
GAP = 6.0                # ft behind him; each extra follower 3 ft further
FAR = 40.0               # beyond this, walk the walk map to him instead of his footsteps
WAIT_MAX = 180.0         # "Wait here." lasts 3 minutes
LOST_MAX = 240.0         # he is nowhere a bot can see for this long: back to normal life
TASK_REACH = 120.0       # a task Cog this close to him gets fought

STATS = {'heard': {}, 'whispered': {}, 'orders': 0, 'released': {}, 'tunnel': 0, 'door_in': 0, 'door_out': 0,
         'teleport': 0, 'tpQuery': 0, 'tpAnswer': {}, 'trolley': 0, 'battle': 0, 'cogbuilding': 0, 'coghq': 0,
         'task_battles': 0, 'task_done': 0, 'goto': 0, 'notes': []}
ORDERS = {}              # bot avId -> Order


def now_():
    return globalClock.getRealTime()


def count(key, sub=None):
    if sub is None:
        STATS[key] = STATS.get(key, 0) + 1
    else:
        d = STATS.setdefault(key, {})
        d[str(sub)] = d.get(str(sub), 0) + 1


def note(text):
    notify.info('[TTBOTS-P9] %s' % text)
    STATS['notes'] = (STATS['notes'] + ['%s %s' % (time.strftime('%H:%M:%S'), text)])[-60:]


def d2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def known(pos):
    return pos is not None and (abs(pos[0]) + abs(pos[1]) + abs(pos[2])) > 0.01


# ---- his ToonTasks (setQuests on his toon, 5 numbers per task, as the SpeedChat ToonTask menu reads them) ------
def questsOf(obj):
    flat = list((obj.get('setQuests') or ([],))[0]) if obj is not None else []
    return [tuple(flat[i:i + 5]) for i in range(0, len(flat) - 4, 5)]


def questNeed(qid, progress):
    """(done, needed) for a task a Cog battle moves on, or None."""
    try:
        from toontown.quest import Quests
        q = Quests.getQuest(qid)
    except Exception:
        return None
    if q is None:
        return None
    if isinstance(q, Quests.CogQuest):
        return progress, q.getNumCogs()
    if isinstance(q, Quests.RecoverItemQuest):
        return progress & 0xFFFF, q.getNumItems()
    return None


def pickTask(obj, qid=None):
    """The task to help with: the one he named, else his first unfinished Cog / recover-from-Cogs task."""
    for q in questsOf(obj):
        if qid is not None and q[0] != qid:
            continue
        need = questNeed(q[0], q[4])
        if need is not None and need[0] < need[1]:
            return q[0]
    return None


def taskProgress(obj, qid):
    for q in questsOf(obj):
        if q[0] == qid:
            return questNeed(qid, q[4])
    return None


def cogCounts(qid, suitObj, zoneId, leaderId):
    """Does this street Cog count for the task (the quest's own rules)?"""
    try:
        from toontown.quest import Quests
        from toontown.suit import SuitDNA
        q = Quests.getQuest(qid)
        dna = SuitDNA.SuitDNA()
        dna.makeFromNetString((suitObj.get('setDNAString') or (b'',))[0])
        from toontown.bots import gagplan
        # setLevelDist is the Cog's table row, not its level (09-25 fix): the quest rules want the level
        level = gagplan.cogLevel((suitObj.get('setDNAString') or (b'',))[0], (suitObj.get('setLevelDist') or (0,))[0])
        skel = (suitObj.get('setSkelecog') or (0,))[0]
        if isinstance(q, Quests.RecoverItemQuest):
            if not q.isLocationMatch(zoneId):
                return False
            holder, how = q.getHolder(), q.getHolderType()
            if holder is Quests.Any:
                return True
            return {'type': dna.name, 'level': level, 'track': dna.dept}.get(how) == holder or \
                (how == 'level' and level >= holder)
        cog = {'type': dna.name, 'level': level, 'track': dna.dept, 'isSkelecog': skel, 'isForeman': 0,
               'isSupervisor': 0, 'isVP': 0, 'isCFO': 0, 'isVirtual': 0, 'hasRevives': 0,
               'activeToons': [leaderId]}
        return bool(q.doesCogCount(leaderId, cog, zoneId, [leaderId]))
    except Exception:
        return False


# ---- an order ------------------------------------------------------------------------------------------------
class Order:
    def __init__(self, bot, leader, mode, why, whisper=False):
        self.botId = bot.avId
        self.leader = leader
        self.mode = mode            # follow / wait / help
        self.why = why
        self.whisper = whisper
        self.task = None
        self.t0 = now_()
        self.waitSince = 0.0
        self.spot = None
        self.lastSeen = now_()
        self.lastPos = None         # where he was when he left the bot's area view
        self.lastZone = None
        self.lastT = 0.0
        self.door = None            # (zoneId, doorDoId) he went in by
        self.doorT = 0.0
        self.tpOut = 0.0            # he played TeleportOut
        self.cool = {}              # key -> time: do not retry before
        self.index = 0              # 0 = first follower (nearest gap)
        self.delegated = None       # name of the real activity doing it for him now

    def __repr__(self):
        return '<Order %s->%s %s>' % (self.botId, self.leader, self.mode)


def ordersOf(leader):
    return [o for o in ORDERS.values() if o.leader == leader]


# ---- travel to him: TeleportOut, the Quiet Zone, TeleportIn next to him (what a client's teleport-to shows) ----
class FollowTravel(BotTravel):
    def __init__(self, director, bot, dest, leaderId, zoneId=None):
        BotTravel.__init__(self, director, bot, dest, why='P9: to %s' % leaderId, via='teleport')
        self.leaderId = leaderId
        self.nearZone = zoneId
        self.arriveBy = 'near'

    def _arrive_near(self):
        a, bot, d = self.dest, self.bot, self.director
        v = d.air.doView.get(self.leaderId)
        o = v.objects.get(self.leaderId) if v is not None else None
        node = None
        if o is not None and known(o.pos) and d.world.zoneToArea.get(v.zoneId) is a:
            node = a.nodeNear(o.pos[0], o.pos[1], 12.0) or a.wm.nearestNode(o.pos[0], o.pos[1], o.pos[2])
        if node is None:
            node = d.spawnNode(a, bot)
        bot.relocate(QUIET)
        bot.area = a
        bot.node = node
        bot.pos = Point3(*a.wm.pos(node))
        bot.h = random.uniform(-180, 180)
        if o is not None and known(o.pos):
            bot.faceTo(o.pos)
        bot.broadcastNow()
        if d.watched(a):
            bot.setAnim('TeleportIn', force=True)
            bot.relocate(a.zoneOfNode(node), area=a)
            d.count('in', 'teleport')
            self.phase, self.until = 'tpin', globalClock.getRealTime() + TELEPORT_IN_TIME
        else:
            bot.setAnim('neutral', force=True)
            bot.relocate(a.zoneOfNode(node), area=a)
            d.count('in', 'silent')
            self.phase, self.until = 'tpin', globalClock.getRealTime()
        count('teleport')


def teleportTo(bot, area, leaderId, zoneId=None):
    d = bot.director
    if bot.state != 'present' or bot.travel is not None:
        return False
    tr = FollowTravel(d, bot, area, leaderId, zoneId)
    bot.travel = tr
    try:
        tr.begin()
    except Exception:
        bot.travel = None
        d.error('P9 teleport begin', traceback.format_exc())
        return False
    bot.nextTick = min(bot.nextTick, now_())
    note('%s teleports to %s in %s (%s)' % (bot.avId, leaderId, area.name, zoneId))
    return True


# ---- the activity -----------------------------------------------------------------------------------------------
@register
class Command(Activity):
    name = 'command'
    weight = 0                 # only from an order
    interruptible = False
    fast = True
    label = 'following'

    @classmethod
    def canRun(cls, bot):
        COMMANDER.ensure(bot.director)
        return False

    def __init__(self, bot, order):
        Activity.__init__(self, bot)
        self.order = order
        self.trail = None
        self.replan = 0.0
        self.sub = None             # tunnel / door / inside / out / tpquery
        self.until = 0.0
        self.nextScan = 0.0
        self.nextLook = 0.0
        self.nextSeek = now_() + 2.0
        # a building visit
        self.doorPlace = None
        self.extDoId = None
        self.extZone = None
        self.intZone = None
        self.intDoId = None
        self.intView = None
        self.layout = None
        self.door = None
        self.reply = None
        self.tunnel = None
        self.tpReply = None

    def start(self):
        bot = self.bot
        bot.path = []
        if bot.node is None and bot.area is not None and bot.zoneId in bot.area.zones:
            bot.node = bot.area.wm.nearestNode(bot.pos[0], bot.pos[1], bot.pos[2])
        self.order.delegated = None
        return True

    # -- where is he -----------------------------------------------------------------------------------------------
    def leader(self):
        v = self.air.doView.get(self.order.leader)
        if v is None:
            return None, None
        o = v.objects.get(self.order.leader)
        return (o, v.zoneId) if o is not None else (None, None)

    def onHeard(self, speaker, msgId):
        return msgId in COMMAND_IDS

    def onDirect(self, fieldName, args):
        if fieldName == 'setOtherZoneIdAndDoId':
            self.reply = ('ok', args[0], args[1])
        elif fieldName == 'rejectEnter':
            self.reply = ('reject',)
        elif fieldName == 'teleportResponse' and args[0] == self.order.leader:
            self.tpReply = tuple(args)
            count('tpAnswer', args[1])

    # -- the tick --------------------------------------------------------------------------------------------------
    def step(self, now):
        o = self.order
        if ORDERS.get(self.bot.avId) is not o:
            return False
        if self.sub is not None:
            return getattr(self, 'sub_' + self.sub)(now)
        lead, zone = self.leader()
        bot = self.bot
        if lead is not None and zone != QUIET:
            o.lastSeen = now
        if o.mode != getattr(self, 'lastMode', None):
            self.lastMode = o.mode
            self.trail = None            # back from waiting: his footsteps from before are stale
        if o.mode == 'wait':
            return self.__wait(now, lead)
        a = bot.area
        if a is None:
            return True
        if lead is not None and zone in a.zones:
            if now >= self.nextScan:
                self.nextScan = now + 0.5
                if self.__scan(now, lead, zone) or bot.activity is not self:
                    return True
            if o.mode == 'help' and (self.__seek(now, lead, zone) or bot.activity is not self):
                return True
            self.__follow(now, lead)
            return True
        return self.__gone(now, lead, zone)

    def __wait(self, now, lead):
        bot = self.bot
        if bot.path:
            bot.stopWalking()
        if bot.anim != 'neutral':
            bot.setAnim('neutral')
        if lead is not None and now >= self.nextLook and bot.zoneId is not None:
            self.nextLook = now + random.uniform(3.0, 7.0)
            if known(lead.pos) and d2(lead.pos, bot.pos) < 80.0:
                bot.faceTo(lead.pos)
        return True

    # -- walking behind him --------------------------------------------------------------------------------------------
    def __follow(self, now, lead):
        bot, a = self.bot, self.bot.area
        lp = Point3(lead.pos)
        if not known(lp):
            return
        gap = GAP + 3.0 * self.order.index
        dist = d2(lp, bot.pos)
        if self.trail is None or dist > FAR * 1.5:
            # far from him (just back from the trolley, a door, a teleport): the walk map to him
            if dist < 25.0:
                self.trail = [lp]
                bot.path = []
                bot.node = None
            elif now >= self.replan or not bot.path:
                self.replan = now + 1.5
                self.trail = None
                start = a.wm.nearestNode(bot.pos[0], bot.pos[1], bot.pos[2])
                goal = a.wm.nearestNode(lp[0], lp[1], lp[2])
                bot.node = start
                if start is None or goal is None or not bot.walkTo(goal, RUN_SPEED, 'run'):
                    self.trail = [lp]
                    bot.node = None
            self.__visgroup()
            return
        trail = self.trail
        if d2(lp, trail[-1]) > 1.0:
            trail.append(lp)
        # drop footsteps already walked past; a trail that doubles back is cut to a straight line
        for i in range(len(trail) - 1, 0, -1):
            if d2(trail[i], bot.pos) < gap:
                del trail[:i]
                break
        if len(trail) > 1 and dist < gap * 3:
            walk, prev = 0.0, bot.pos
            for p in trail:
                walk += d2(p, prev)
                prev = p
            if walk > dist * 2 + gap:
                trail[:] = [lp]
        # owner 09-25: a follower runs to keep up (run cycle at the player run speed), walks only the last feet
        speed, gait = gaitFor(dist - gap, bot.anim)
        if not speed or (bot.anim == 'neutral' and dist <= gap + 1.5):
            bot.path = []
            bot.setAnim('neutral')
            if now >= self.nextLook:
                self.nextLook = now + 1.0
                bot.faceTo(lp)
            while len(trail) > 1 and d2(trail[0], bot.pos) < gap:
                trail.pop(0)
            self.__visgroup()
            return
        pts = list(trail)
        if d2(pts[-1], lp) < gap:
            pts = pts[:-1] or [pts[-1]]
        bot.path = [(p[0], p[1], p[2], None) for p in pts]
        bot.speed = speed
        bot.setAnim(gait)
        self.__visgroup()

    def __visgroup(self):
        """On a street the toon stands in the visgroup under its feet (as a client's street loader does)."""
        bot, a = self.bot, self.bot.area
        if a is None or a.kind != 'street' or bot.state != 'present':
            return
        z = a.wm.zoneAt(bot.pos[0], bot.pos[1])
        if z is not None and z != bot.zoneId and z in a.zones:
            bot.relocate(z)

    # -- what is he doing: the trolley, a battle, an elevator -----------------------------------------------------------
    def __scan(self, now, lead, zone):
        bot, a, d, o = self.bot, self.bot.area, self.director, self.order
        me = bot.avId
        # the trolley
        if a.kind == 'playground' and now >= o.cool.get('trolley', 0.0):
            v = d.viewOf(a.id)
            t = v.first('DistributedTrolley') if v is not None else None
            if t is not None:
                seats = [t.get('fillSlot%d' % i, (0,))[0] for i in range(4)]
                if o.leader in seats and me not in seats and 0 in seats:
                    o.cool['trolley'] = now + 20.0
                    if self.__trolley():
                        return True
        # his street battle
        if a.kind == 'street' and now >= o.cool.get('battle', 0.0):
            from toontown.bots.activities import battle as bmod
            for b in bmod.streetBattles(d, a):
                m = b.get('setMembers')
                if m and o.leader in m[6] and me not in m[6] and len(m[6]) < 4:
                    o.cool['battle'] = now + 8.0
                    if bot.startActivity(bmod.StreetBattle(bot, mode='help', battle=b)):
                        o.delegated = 'battle'
                        count('battle')
                        note('%s joins %s\'s battle %s' % (me, o.leader, b.doId))
                        return True
        # a Cog building elevator he sits in
        if a.kind == 'street' and now >= o.cool.get('bldg', 0.0):
            for z in a.zones:
                v = d.viewOf(z)
                if v is None:
                    continue
                for e in v.ofClass('DistributedElevatorExt'):
                    seats = [e.get('fillSlot%d' % i, (0,))[0] for i in range(4)]
                    if o.leader in seats and me not in seats:
                        o.cool['bldg'] = now + 15.0
                        if self.__building(e):
                            return True
        # a Cog HQ facility / boss elevator he sits in
        if a.kind == 'coghq' and now >= o.cool.get('hq', 0.0):
            if self.__cogHQ(now):
                return True
        return False

    def __trolley(self):
        from toontown.bots.activities import trolley as tmod
        bot, d = self.bot, self.director
        for attempt in range(2):
            act = tmod.Trolley(bot)
            act.hurry = True
            act.mirror = self.order.leader
            if bot.startActivity(act):
                self.order.delegated = 'trolley'
                count('trolley')
                note('%s boards the trolley in %s with %s' % (bot.avId, bot.zoneId, self.order.leader))
                return True
            # every rider place is taken by bots still walking up: the last of them gives way to his friend
            for i in range(tmod.RIDERS):
                b = d.claims.get(('trolley', bot.zoneId, i))
                if b is not None and b is not bot and isinstance(b.activity, tmod.Trolley) \
                        and b.activity.phase in ('walk', 'wait') and b.avId not in ORDERS:
                    b.endActivity('yield to a follower')
                    break
            else:
                break
            if bot.activity is None:
                bot.startActivity(Command(bot, self.order))
        return False

    def __building(self, e):
        from toontown.bots.activities import cogbuilding as cb
        bot, d = self.bot, self.director
        bldgId = (e.get('setBldgDoId') or (0,))[0]
        g = cb.RUNS.get(bldgId)
        if g is None:
            blds = [x for x in cb.suitBuildings(d, bot.area) if x[0].doId == bldgId]
            if not blds:
                return False
            b, el, door, floors = blds[0]
            g = cb.Group(b, el, door, floors)
            g.withPlayer = True
            g.go = True
            cb.RUNS[bldgId] = g
        if bot.startActivity(cb.CogBuilding(bot, role='member', group=g)):
            self.order.delegated = 'cogbuilding'
            count('cogbuilding')
            note('%s boards Cog building %s with %s' % (bot.avId, bldgId, self.order.leader))
            return True
        return False

    def __cogHQ(self, now):
        from toontown.bots.activities import coghq
        from toontown.bots.coghq import common as cm
        bot, d, o = self.bot, self.director, self.order
        v = d.viewOf(bot.zoneId)
        if v is None:
            return False
        for e in list(v.objects.values()):
            kind = cm.ELEV_CLASSES.get(e.className)
            if kind is None:
                continue
            n = 8 if kind in cm.BOSSES else 4
            seats = cm.seats(e, n)
            if o.leader not in seats or bot.avId in seats or 0 not in seats:
                continue
            o.cool['hq'] = now + 15.0
            g = next((x for x in coghq.RUNS if x.elevId == e.doId and x.run is None), None)
            if g is None:
                return False
            if kind in cm.BOSSES:
                from toontown.bots.activities import boss
                from toontown.bots.coghq import suits
                if not suits.readyForPromotion(bot, cm.BOSSES[kind]['dept']):
                    return False          # the elevator's own check would refuse it: it waits in the lobby
                act = boss.BossActivity(bot, g)
            else:
                act = coghq.FacilityActivity(bot, g)
            if bot.startActivity(act):
                o.delegated = 'coghq'
                count('coghq')
                note('%s boards the %s elevator with %s' % (bot.avId, kind, o.leader))
                return True
        return False

    # -- ToonTask help: a Cog his task counts, near him, is fought ----------------------------------------------------
    def __seek(self, now, lead, zone):
        bot, a, d, o = self.bot, self.bot.area, self.director, self.order
        if now < self.nextSeek:
            return False
        self.nextSeek = now + 3.0
        if o.task is None:
            return False
        prog = taskProgress(lead, o.task)
        if prog is None or prog[0] >= prog[1]:
            count('task_done')
            note('%s: %s\'s task %s is done (%s): back to following' % (bot.avId, o.leader, o.task, prog))
            if random.random() < 0.7:
                bot.say(random.choice((1405, 1404, 500)))
            o.mode, o.task = 'follow', None
            return False
        if a.kind != 'street' or not known(lead.pos):
            return False
        from toontown.bots.activities import battle as bmod
        battles = bmod.streetBattles(d, a)
        for b in battles:
            m = b.get('setMembers')
            if m and (o.leader in m[6] or any(x.botId in m[6] for x in ordersOf(o.leader))):
                return False          # a battle with him or his helpers is on: the scan joins it
        if any(b is not bot and b.activity is not None and b.activity.name == 'battle' and b.avId in ORDERS
               and ORDERS[b.avId].leader == o.leader for b in d.bots.values()):
            return False              # another helper is already walking into one
        if not bmod.canFight(bot, 0.4, 4):
            return False
        cands = []
        bpos = [b.get('setPosition') for b in battles]
        for z in a.zones:
            v = d.viewOf(z)
            if v is None:
                continue
            for s in v.ofClass('DistributedSuit'):
                pos, h, walking = bmod.suitNow(d, a, s)
                if pos is None or not walking or d2(pos, lead.pos) > TASK_REACH:
                    continue
                if any(p and d2(p, pos) < 40.0 for p in bpos):
                    continue
                if cogCounts(o.task, s, z, o.leader):
                    cands.append((d2(pos, lead.pos), s))
        if not cands:
            return False
        cands.sort(key=lambda c: c[0])
        target = cands[0][1]
        act = bmod.StreetBattle(bot, suitFilter=lambda s, t=target.doId: s.doId == t)
        if bot.startActivity(act):
            o.delegated = 'battle'
            count('task_battles')
            note('%s walks into task Cog %s (%.0f ft from %s) for task %s' % (
                bot.avId, target.doId, cands[0][0], o.leader, o.task))
            if random.random() < 0.6:
                bot.say(random.choice((1102, 1702, 508)))
            return True
        return False

    # -- he is not in my area: where did he go ------------------------------------------------------------------------
    def __gone(self, now, lead, zone):
        bot, a, d, o = self.bot, self.bot.area, self.director, self.order
        if bot.path and self.trail is not None:
            bot.stopWalking()
        self.trail = None
        # the building door he walked in by (seen as the door's avatarEnter broadcast)
        if o.door is not None and o.door[0] in a.zones and now - o.doorT < 60.0:
            found = self.__doorPlace(o.door[1])
            o.door = None
            if found is not None and self.__startDoor(found, now):
                return True
        herArea = d.world.zoneToArea.get(zone) if zone is not None else None
        # the tunnel he walked into: walk into it too, out of the matching mouth on the other side
        if o.lastPos is not None and now - o.lastT < 45.0 and d.world.zoneToArea.get(o.lastZone) is a \
                and now - o.tpOut > 10.0:
            best = None
            for nbId, place in a.tunnels.items():
                dd = d2(place['pos'], o.lastPos)
                if dd < 40.0 and (best is None or dd < best[0]):
                    best = (dd, nbId, place)
            if best is not None and (herArea is None or herArea.id == best[1]):
                self.tunnel = (d.world.areas[best[1]], best[2])
                x, y, z = best[2]['pos']
                if bot.walkToPos(x, y, z, RUN_SPEED, 'run') or bot.walkTo(a.placeNode(best[2]), RUN_SPEED, 'run'):
                    self.sub, self.until = 'tunnel', now + 60.0
                    o.lastPos = None
                    note('%s walks into the tunnel to %s after %s' % (bot.avId, best[1], o.leader))
                    return True
        if herArea is not None and herArea is not a:
            if o.leader in bot.friendIds():
                return self.__askTeleport(now)
            return teleportTo(bot, herArea, o.leader, zone) or True
        # nowhere I can see (loading, a trolley game, an interior, a facility): stand and wait for him
        if bot.path:
            bot.stopWalking()
        elif bot.anim not in ('neutral',):
            bot.setAnim('neutral')
        if zone is not None and zone not in (QUIET,) and herArea is None and o.leader in bot.friendIds() \
                and now >= o.cool.get('tpq', 0.0):
            return self.__askTeleport(now)
        return True

    def __askTeleport(self, now):
        """A friend's teleport, as the client does it: teleportQuery -> his client answers teleportResponse."""
        bot, o = self.bot, self.order
        if now < o.cool.get('tpq', 0.0):
            return True
        o.cool['tpq'] = now + 12.0
        self.tpReply = None
        bot.send('teleportQuery', [bot.avId], doId=o.leader)
        count('tpQuery')
        note('%s asks %s for a teleport (teleportQuery)' % (bot.avId, o.leader))
        self.sub, self.until = 'tpquery', now + 8.0
        return True

    def sub_tpquery(self, now):
        bot, d, o = self.bot, self.director, self.order
        r = self.tpReply
        if r is None:
            if now > self.until:
                self.sub = None
                # sweep fix: a logged-in friend's client always answers; silence twice = he logged out
                o.tpMiss = getattr(o, 'tpMiss', 0) + 1
                if o.tpMiss >= TP_UNANSWERED:
                    COMMANDER.release(o, 'he logged out (no teleport answer)')
                    return False
            return True
        self.sub = None
        o.tpMiss = 0
        avId, available, shard, hood, zone = r
        area = d.world.zoneToArea.get(zone)
        if shard != self.air.districtId and shard:
            COMMANDER.release(o, 'he left the district')
            return False
        if available != 1 or area is None or shard != self.air.districtId:
            note('%s: %s cannot be visited now (%s)' % (bot.avId, o.leader, r))
            return True
        if area is bot.area:
            return True               # he is here after all: walk to him
        teleportTo(bot, area, o.leader, zone)
        return True

    def sub_tunnel(self, now):
        bot, d, o = self.bot, self.director, self.order
        if bot.path and now < self.until:
            return True
        dest, place = self.tunnel
        self.sub = None
        if d.travel(bot, dest, why='P9: follows %s through the tunnel' % o.leader, via='tunnel'):
            count('tunnel')
            return False
        return True

    # -- the building door he took ----------------------------------------------------------------------------------------
    def __doorPlace(self, doId):
        a, d = self.bot.area, self.director
        for p in a.doors:
            ex = p['extra']
            if d.doorDOs.get((a.id, ex['block'], ex.get('door', 0))) == doId:
                return p, doId
        return None

    def __startDoor(self, found, now):
        bot, a = self.bot, self.bot.area
        place, doId = found
        node = a.placeNode(place)
        start = a.wm.nearestNode(bot.pos[0], bot.pos[1], bot.pos[2])
        bot.node = start
        if node is None or not (bot.walkTo(node, RUN_SPEED, 'run') or start == node):
            return False
        self.doorPlace, self.extDoId = place, doId
        self.extZone = self.director.world.doorZone(a, place)
        self.sub, self.until = 'todoor', now + 60.0
        note('%s follows %s to the door of block %s' % (bot.avId, self.order.leader, place['extra'].get('block')))
        return True

    def sub_todoor(self, now):
        bot = self.bot
        if bot.path:
            if now > self.until:
                bot.stopWalking()
                self.sub = None
            return True
        bot.faceTo(self.doorPlace['pos'])
        bot.setAnim('neutral')
        if not bot.settled(now):
            return True                      # smoothness: the client's door walk starts where it drew me stop
        self.reply = None
        bot.send('requestEnter', [], doId=self.extDoId, className='DistributedDoor')
        self.sub, self.until = 'doorwait', now + 4.0
        return True

    def sub_doorwait(self, now):
        bot = self.bot
        if self.reply and self.reply[0] == 'ok':
            self.intZone, self.intDoId = self.reply[1], self.reply[2]
            x, y, z = self.doorPlace['pos']
            bot.pos = Point3(x, y, z)             # the client's door track walks me in; no sample of mine
            self.sub, self.until = 'doorin', now + 1.6
        elif self.reply or now > self.until:
            note('%s: door %s refused' % (bot.avId, self.extDoId))
            self.sub = None
        return True

    def sub_doorin(self, now):
        if now < self.until:
            return True
        bot = self.bot
        self.intView = self.air.openView(self.air.districtId, self.intZone)
        self.layout = kit.layoutFor(self.doorPlace['kind'], self.intZone)
        doors = self.layout['doors']
        idx = 0
        if len(doors) > 1:
            v = self.director.viewOf(self.extZone)
            ob = v.objects.get(self.extDoId) if v is not None else None
            di = (ob.get('setDoorIndex') or (0,))[0] if ob is not None else 0
            idx = di if di < len(doors) else 0
        self.door = doors[idx]
        ex, ey, ez = self.door['entry']
        bot.path = []
        bot.relocate(QUIET)
        bot.pos = Point3(ex, ey, ez)
        bot.h = self.door['h'] + 180.0
        bot.broadcastNow()
        bot.setAnim('neutral', force=True)
        bot.relocate(self.intZone)
        bot.send('requestExit', [], doId=self.intDoId, className='DistributedDoor')
        self.at = tuple(self.door['entry'])
        count('door_in')
        note('%s went in after %s (zone %s)' % (bot.avId, self.order.leader, self.intZone))
        self.sub, self.until = 'inside', now + 2.2
        self.seenInside = now
        return True

    def sub_inside(self, now):
        bot, o = self.bot, self.order
        if now < self.until:
            return True
        if bot.anim == 'walk' and not bot.path:
            bot.setAnim('neutral')
        v = self.intView
        lead = v.objects.get(o.leader) if v is not None else None
        if lead is not None:
            self.seenInside = now
        if o.mode == 'wait':
            return True
        if lead is None and now - self.seenInside > 3.0:
            # he left (his door walk-out, or a teleport): out the way I came in
            return self.__leaveInside(now)
        if lead is None or not known(lead.pos) or bot.path or now < getattr(self, 'nextSpot', 0.0):
            return True
        self.nextSpot = now + 2.0
        spots = [tuple(s) for s in (self.door.get('spots') or [])] + [tuple(self.door['entry'])]
        gap = GAP + 3.0 * o.index
        spots.sort(key=lambda s: abs(d2(s, lead.pos) - gap))
        best = spots[0]
        if d2(best, self.at) < 1.0:
            if now >= self.nextLook:
                self.nextLook = now + 1.5
                bot.faceTo(lead.pos)
            return True
        legs = ([tuple(self.door['entry'])] if d2(self.at, self.door['entry']) > 0.5 else []) + \
            ([best] if d2(best, self.door['entry']) > 0.5 else [])
        bot.path = [(p[0], p[1], p[2], None) for p in legs]
        bot.speed = WALK_SPEED
        bot.setAnim('walk')
        self.at = best
        return True

    def __leaveInside(self, now):
        bot = self.bot
        e = tuple(self.door['entry'])
        bot.path = [(e[0], e[1], e[2], None)] if d2(self.at, e) > 0.5 else []
        bot.speed = RUN_SPEED
        if bot.path:
            bot.setAnim('run')
        self.at = e
        self.sub = 'leavewalk'
        return True

    def sub_leavewalk(self, now):
        bot = self.bot
        if bot.path:
            return True
        bot.faceTo(self.door['pos'])
        bot.setAnim('neutral')
        self.reply = None
        bot.send('requestEnter', [], doId=self.intDoId, className='DistributedDoor')
        self.sub, self.until = 'leaveenter', now + 3.0
        return True

    def sub_leaveenter(self, now):
        bot = self.bot
        if self.reply or now > self.until:
            x, y, z = self.door['pos']
            bot.path = [(x, y, z, None)]
            bot.speed = WALK_SPEED
            bot.setAnim('walk')
            self.sub, self.until = 'doorout', now + 1.6
        return True

    def sub_doorout(self, now):
        if now < self.until:
            return True
        self.__comeOut()
        self.sub, self.until = 'outside', now + 2.0
        return True

    def sub_outside(self, now):
        if now < self.until:
            return True
        self.bot.setAnim('neutral', force=True)
        self.sub = None
        self.trail = None
        return True

    def __comeOut(self):
        bot, a = self.bot, self.bot.area
        node = a.placeNode(self.doorPlace)
        bot.path = []
        bot.relocate(QUIET)
        bot.node = node
        bot.pos = Point3(*a.wm.pos(node))
        x, y, z = self.doorPlace['pos']
        bot.faceTo((2 * bot.pos[0] - x, 2 * bot.pos[1] - y))
        bot.broadcastNow()
        bot.setAnim('neutral', force=True)
        bot.relocate(self.extZone)
        bot.send('requestExit', [], doId=self.extDoId, className='DistributedDoor')
        count('door_out')
        note('%s came back out of block %s' % (bot.avId, self.doorPlace['extra'].get('block')))
        self.__closeView()

    def __closeView(self):
        if self.intView is not None:
            self.air.closeView(self.intView)
            self.intView = None

    def stop(self, why):
        bot = self.bot
        if self.sub in ('inside', 'leavewalk', 'leaveenter', 'doorout') and bot.state == 'present' \
                and why not in ('logout', 'district down', 'deleted by the server', 'activation timeout'):
            if self.intDoId is not None:
                bot.send('requestEnter', [], doId=self.intDoId, className='DistributedDoor')
            self.__comeOut()
        self.__closeView()
        if bot.path and why not in ('travel',):
            bot.stopWalking()
        if bot.node is None and bot.area is not None and bot.zoneId in bot.area.zones:
            bot.node = bot.area.wm.nearestNode(bot.pos[0], bot.pos[1], bot.pos[2])


# ---- the commander: hears him, keeps the orders -----------------------------------------------------------------------
class Commander(DirectObject):
    def __init__(self):
        DirectObject.__init__(self)
        self.director = None
        self.lastWrite = 0.0

    def ensure(self, director):
        if self.director is not None:
            return
        self.director = director
        bb.HUB.ensure(director)
        self.accept('botview-field', self.__field)
        self.accept('botview-exit', self.__exit)
        if self.__whisper not in BT.COMMAND_HOOKS:
            BT.COMMAND_HOOKS.append(self.__whisper)
        taskMgr.doMethodLater(0.25, self.__tick, 'bots-p9-orders')
        notify.info('[TTBOTS-P9] commands up')

    # -- heard aloud ------------------------------------------------------------------------------------------------------
    def __field(self, view, obj, fieldName, args, sender):
        d = self.director
        try:
            if obj.className == 'DistributedToon' and obj.doId not in d.bots:
                if fieldName == 'setSC':
                    self.aloud(view, obj, args[0])
                elif fieldName == 'setSCToontask':
                    self.aloud(view, obj, 'task', taskId=args[0])
                elif fieldName == 'setAnimState' and args and args[0] == 'TeleportOut':
                    for o in ordersOf(obj.doId):
                        o.tpOut = now_()
            elif fieldName == 'avatarEnter' and obj.className == 'DistributedDoor' and args:
                for o in ordersOf(args[0]):
                    o.door = (view.zoneId, obj.doId)
                    o.doorT = now_()
        except Exception:
            d.error('P9 heard', traceback.format_exc())

    def __exit(self, view, obj, deleted=False):
        if obj.className != 'DistributedToon':
            return
        for o in ordersOf(obj.doId):
            if deleted:
                o.lastPos = None          # he logged out (deleted), not into a tunnel: nothing to walk after
                self.release(o, 'he logged out')      # sweep fix: his orders end with him
            elif known(obj.pos) and self.director.world.zoneToArea.get(view.zoneId) is not None:
                o.lastPos, o.lastZone, o.lastT = Point3(obj.pos), view.zoneId, now_()

    def aloud(self, view, speaker, msgId, taskId=None):
        d = self.director
        area = d.world.zoneToArea.get(view.zoneId)
        count('heard', msgId)
        mine = ordersOf(speaker.doId)
        near = [o for o in mine if self.__botNear(o, speaker, HEAR_WAIT)]
        if msgId == SC_FOLLOW:
            # the bots already with him (waiting or following) say OK and come along; sweep fix: one he
            # whispered that is still busy (fishing, in a trolley game) does not answer for everyone
            near = [o for o in near if self.__withHim(o)]
            for o in near:
                self.__ack(o, 'follow')
            if near or area is None:
                return
            cands = [b for b in self.__free(view.zoneId, area) if d2(b.pos, speaker.pos) <= HEAR]
            cands.sort(key=lambda b: d2(b.pos, speaker.pos))
            n = 1 + (random.random() < 0.3) + (random.random() < 0.1)
            for i, b in enumerate(cands[:n]):
                self.order(b, speaker.doId, 'follow', 'aloud', index=i + len(mine))
            return
        if msgId in SC_WAIT:
            for o in near:
                if o.mode != 'wait':
                    self.__ack(o, 'wait')
            return
        if msgId in SC_BYE:
            for o in near:
                self.release(o, 'bye', answer=True)
            return
        if msgId == 'task' or msgId in SC_TASK:
            self.__task(view, speaker, area, near, taskId)
            return
        if msgId == SC_TROLLEY or msgId in SC_FIGHT or msgId == SC_PLAYGROUND or msgId in SC_GOTO:
            for o in near:
                self.__letsGo(o, speaker, msgId)

    def __withHim(self, o):
        b = self.director.bots.get(o.botId)
        return b is not None and (b.activity is None or isinstance(b.activity, Command) or o.delegated is not None)

    def __botNear(self, o, speaker, r):
        b = self.director.bots.get(o.botId)
        return b is not None and b.zoneId is not None and (
            b.zoneId == speaker.zoneId or self.director.world.zoneToArea.get(b.zoneId) is
            self.director.world.zoneToArea.get(speaker.zoneId)) and d2(b.pos, speaker.pos) <= r

    def __free(self, zoneId, area):
        from toontown.bots.activities import trolley as tmod
        d = self.director
        out = []
        for b in d.bots.values():
            if b.state != 'present' or b.travel is not None or b.area is not area or b.avId in ORDERS \
                    or b.zoneId not in area.zones:
                continue
            if b.activity is not None and not b.activity.interruptible:
                continue
            if isinstance(b.activity, tmod.Trolley) and b.activity.seat is not None:
                continue
            out.append(b)
        return out

    def __task(self, view, speaker, area, near, taskId):
        d = self.director
        qid = pickTask(speaker, taskId)
        helpers = [o for o in near if o.mode in ('follow', 'help')]
        want = random.choice((1, 2, 2))
        if area is not None and len(helpers) < want:
            cands = [b for b in self.__free(view.zoneId, area) if d2(b.pos, speaker.pos) <= 60.0
                     and bb.healthy(b, 0.5) and bb.gagCount(b, bb.ATTACK_TRACKS) >= 4]
            cands.sort(key=lambda b: d2(b.pos, speaker.pos))
            for b in cands[:want - len(helpers)]:
                o = self.order(b, speaker.doId, 'help', 'task', index=len(ordersOf(speaker.doId)), ack=None)
                helpers.append(o)
        for o in helpers:
            o.mode = 'help'
            o.task = qid
            b = d.bots.get(o.botId)
            if b is not None:
                self.__later(b, 0.6, 2.0, b.say, random.choice((1201, 508, 1201)) if qid is not None else 1200)
        # a toon or two more nearby answers too
        if area is not None:
            others = [b for b in self.__free(view.zoneId, area) if d2(b.pos, speaker.pos) <= 40.0]
            for b in others[:random.choice((0, 1, 1, 2))]:
                self.__later(b, 1.0, 3.0, b.say, random.choice((513, 508, 1200)))
        note('%s asked for ToonTask help (task %s, from %s): helpers %s' % (
            speaker.doId, qid, taskId, [o.botId for o in helpers]))

    def __letsGo(self, o, speaker, msgId):
        d = self.director
        b = d.bots.get(o.botId)
        if b is None or b.state != 'present':
            return
        self.__later(b, 0.4, 1.4, b.say, SC_OK)
        o.mode = 'follow' if o.mode == 'wait' else o.mode
        act = b.activity if isinstance(b.activity, Command) else None
        if msgId == SC_TROLLEY and b.area is not None and b.area.kind == 'playground' and act is not None:
            act._Command__trolley()
        elif msgId in SC_FIGHT and b.area is not None and b.area.kind == 'street' and act is not None:
            from toontown.bots.activities import battle as bmod
            if bmod.canFight(b, 0.4, 4):
                near = []
                for z in b.area.zones:
                    v = d.viewOf(z)
                    for s in (v.ofClass('DistributedSuit') if v is not None else ()):
                        pos, h, walking = bmod.suitNow(d, b.area, s)
                        if pos is not None and walking and d2(pos, speaker.pos) < TASK_REACH:
                            near.append((d2(pos, speaker.pos), s.doId))
                if near:
                    t = min(near)[1]
                    if b.startActivity(bmod.StreetBattle(b, suitFilter=lambda s, t=t: s.doId == t)):
                        o.delegated = 'battle'
        elif msgId == SC_PLAYGROUND and b.area is not None and b.area.kind == 'street':
            pg = d.world.areas.get(b.area.hood)
            if pg is not None and act is not None and pg.id in b.area.tunnels:
                act.tunnel = (pg, b.area.tunnels[pg.id])
                x, y, z = act.tunnel[1]['pos']
                if b.walkToPos(x, y, z, RUN_SPEED, 'run'):
                    act.sub, act.until = 'tunnel', now_() + 90.0
        elif msgId in SC_GOTO:
            dest = d.world.areas.get(SC_GOTO[msgId])
            if dest is not None and dest is not b.area and b.activity is not None and b.activity.interruptible is False \
                    and isinstance(b.activity, Command):
                count('goto')
                b.endActivity('travel')
                if not d.travel(b, dest, why='P9: let\'s go to %s' % dest.name, via='teleport'):
                    b.startActivity(Command(b, o))
        count('letsgo', msgId)

    # -- whispered to one bot ----------------------------------------------------------------------------------------------
    def __whisper(self, bot, fieldName, args):
        d = self.director
        fromId = args[0]
        if fromId in d.bots:
            return False
        if fieldName == 'setWhisperSCToontaskFrom':
            msgId, taskId = 'task', args[1]
        elif fieldName == 'setWhisperSCFrom':
            msgId, taskId = args[1], None
        else:
            return False
        if msgId not in COMMAND_IDS and msgId != 'task':
            return False
        count('whispered', msgId)
        o = ORDERS.get(bot.avId)
        if o is not None and o.leader != fromId:
            if msgId in (SC_FOLLOW, 'task') + SC_TASK:
                bot.later(1.0, 2.5, 'p9busy', bot.whisperBack, fromId, 813)      # "Sorry, I'm helping a friend!"
                return True
            return False
        if msgId == SC_FOLLOW:
            if o is None:
                self.order(bot, fromId, 'follow', 'whisper', whisper=True, index=len(ordersOf(fromId)))
            elif o.mode == 'wait':
                self.__ack(o, 'follow', whisper=True)
            else:
                bot.later(0.8, 2.0, 'p9ok', bot.whisperBack, fromId, SC_OK)
            return True
        if o is None and msgId != 'task' and msgId not in SC_TASK:
            return False
        if msgId in SC_WAIT:
            self.__ack(o, 'wait', whisper=True)
        elif msgId in SC_BYE:
            self.release(o, 'bye (whisper)')
            bot.later(1.0, 2.5, 'p9bye', bot.whisperBack, fromId, random.choice((200, 202)))
        elif msgId == 'task' or msgId in SC_TASK:
            if o is None:
                o = self.order(bot, fromId, 'help', 'task whisper', whisper=True, index=len(ordersOf(fromId)), ack=None)
            v = self.director.air.doView.get(fromId)
            speaker = v.objects.get(fromId) if v is not None else None
            o.mode = 'help'
            o.task = pickTask(speaker, taskId) if speaker is not None else taskId
            bot.later(1.0, 2.5, 'p9task', bot.whisperBack, fromId, 1201 if o.task is not None else 1200)
            note('%s: whispered ToonTask help from %s (task %s)' % (bot.avId, fromId, o.task))
        else:
            v = self.director.air.doView.get(fromId)
            speaker = v.objects.get(fromId) if v is not None else None
            if speaker is not None:
                self.__letsGo(o, speaker, msgId)
        return True

    # -- orders --------------------------------------------------------------------------------------------------------------
    def order(self, bot, leader, mode, why, whisper=False, index=0, ack=SC_OK):
        o = Order(bot, leader, mode, why, whisper)
        o.index = index
        ORDERS[bot.avId] = o
        STATS['orders'] += 1
        note('%s %s %s (%s)' % (bot.avId, 'follows' if mode == 'follow' else 'helps', leader, why))
        if bot.activity is not None and bot.activity.interruptible:
            bot.endActivity('command')
        elif bot.activity is not None and whisper and hasattr(bot.activity, 'releaseFor'):
            # sweep fix: a friend's whisper ends fishing / hops off a waiting trolley now; in a game he
            # hears "Wait a minute." and the bot Exits at the purchase screen, then comes
            try:
                how = bot.activity.releaseFor('command')
            except Exception:
                how = None
                self.director.error('P9 releaseFor', traceback.format_exc())
            count('freed', how)
            note('%s leaves %s for %s: %s' % (bot.avId, type(bot.activity).__name__ if bot.activity else '-', leader, how))
            if how == 'after' and ack == SC_OK:
                ack = SC_AFTER
        if bot.activity is None and bot.travel is None and bot.state == 'present':
            bot.startActivity(Command(bot, o))
            bot.nextTick = min(bot.nextTick, now_())
        if ack is not None:
            if whisper:
                bot.later(0.8, 2.0, 'p9ok', bot.whisperBack, leader, ack)
            else:
                self.__later(bot, 0.4, 1.2, bot.say, ack)
        return o

    def __ack(self, o, mode, whisper=False):
        b = self.director.bots.get(o.botId)
        o.mode = mode if mode != 'follow' or o.task is None else 'help'
        if mode == 'wait':
            o.waitSince = now_()
            if b is not None:
                o.spot = Point3(b.pos)
                if b.path and isinstance(b.activity, Command):
                    b.stopWalking()
        if b is not None:
            if whisper:
                b.later(0.8, 2.0, 'p9ok', b.whisperBack, o.leader, SC_OK)
            else:
                self.__later(b, 0.4, 1.2, b.say, SC_OK)
        note('%s: %s for %s' % (o.botId, mode, o.leader))

    def __later(self, bot, lo, hi, fn, *args):
        def run(task, bot=bot):
            if bot.state == 'present':
                fn(*args)
        taskMgr.doMethodLater(random.uniform(lo, hi), run, 'p9-%d-%d' % (bot.avId, random.randint(0, 1 << 30)))

    def release(self, o, why, answer=False):
        if ORDERS.get(o.botId) is not o:
            return
        del ORDERS[o.botId]
        count('released', why.split(' ')[0])
        note('%s released (%s) after %.0f s' % (o.botId, why, now_() - o.t0))
        d = self.director
        b = d.bots.get(o.botId)
        if b is None:
            return
        if isinstance(b.activity, Command):
            b.endActivity('released')
        if answer and b.state == 'present':
            self.__later(b, 0.5, 2.0, b.say, random.choice((200, 202, 200)))
        # a bot that followed him somewhere it does not belong goes home (a pinned regular to its own place)
        if b.state == 'present' and b.travel is None and b.area is not None and not d.eligible(b, b.area) \
                and (b.activity is None or b.activity.interruptible):
            home = d.world.areas.get(b.home)
            if home is not None and home is not b.area:
                d.travel(b, home, why='P9: home after following', via='teleport')

    def __tick(self, task):
        d = self.director
        now = now_()
        try:
            for botId, o in list(ORDERS.items()):
                b = d.bots.get(botId)
                if b is None or b.state == 'offline':
                    self.release(o, 'bot offline')
                    continue
                if o.mode == 'wait' and now - o.waitSince > WAIT_MAX:
                    self.release(o, 'waited 3 min')
                    continue
                v = d.air.doView.get(o.leader)
                if v is not None and o.leader in v.objects and v.zoneId != QUIET:
                    o.lastSeen = now
                elif now - o.lastSeen > LOST_MAX:
                    self.release(o, 'lost him')
                    continue
                if b.state == 'present' and b.travel is None and b.activity is None:
                    if o.delegated:
                        o.delegated = None
                    b.startActivity(Command(b, o))
                    b.nextTick = min(b.nextTick, now)
            if now - self.lastWrite > 10.0:
                self.lastWrite = now
                self.__write()
        except Exception:
            d.error('P9 orders', traceback.format_exc())
        return task.again

    def __write(self):
        d = self.director
        data = dict(STATS)
        data['time'] = time.strftime('%Y-%m-%d %H:%M:%S')
        data['live'] = [{'bot': o.botId, 'leader': o.leader, 'mode': o.mode, 'task': o.task, 'age': round(now_() - o.t0),
                         'activity': (d.bots[o.botId].activity.name if d.bots.get(o.botId) is not None
                                      and d.bots[o.botId].activity is not None else None),
                         'zone': d.bots[o.botId].zoneId if d.bots.get(o.botId) is not None else None}
                        for o in ORDERS.values()]
        try:
            path = os.path.join(d.runDir, 'bots-p9.json')
            os.makedirs(d.runDir, exist_ok=True)
            with open(path + '.new', 'w') as f:
                json.dump(data, f, indent=1)
            os.replace(path + '.new', path)
        except Exception:
            d.error('P9 status file', traceback.format_exc())


COMMANDER = Commander()


def _boot(task):
    """Loaded by the director's start(): hook up once simbase.air.botDirector is there."""
    import builtins
    sb = getattr(builtins, 'simbase', None)
    d = getattr(getattr(sb, 'air', None), 'botDirector', None)
    if d is None:
        return task.again
    COMMANDER.ensure(d)
    return task.done


taskMgr.doMethodLater(1.0, _boot, 'bots-p9-boot')
