"""PROGRESSION (owner 09-30): a free bot with a goal (progress.py / taskplan.py) goes and does it, like a player
working through his ToonTasks: "They're trying to do their toon tasks and level up and compete with the player".

  ToonTask   the director's pick when progress.wantsWork(bot): start() hands the bot straight to the activity that
             serves its goal, in the area the goal is in (a travel first when it is somewhere else):
               cogs      battle.StreetBattle on a Cog the task counts (the quest's own rule, commands.cogCounts) that
                         it can handle (battle.canTake), else a street walk looking for one
               building  cogbuilding.CogBuilding with the task's track / floors: it waits at the door asking for help
               trolley / beans   trolley.Trolley (beans: fishing when the trolley is busy)
               fish      fishing.Fishing / StreetFishing in a pond the task counts
               restock   battle.Restock (the gag shop)
               heal      a playground hangout (the AI heals every 30 s there)
               facility / boss   coghq / faclobby / boss (a Cog HQ place it can go to)
               phone / mailbox   home.GoHome (its estate)
               visit / gettask / buygag / friend   TaskRun below
  TaskRun    walks into the building of the task's NPC (lifekit.Visit, the real door protocol), talks to that NPC or
             an HQ officer (avatarEnter; the officer's offer is picked by progress.chooseQuest / chooseTrack), buys the
             gags a Deliver Gag task wants, or makes a friend of a toon standing near (social.makeFriend).
A start that fails is counted (progress.failed): three in ten minutes and the goal is set aside for a while.
"""
import math
import random

from toontown.bots import progress
from toontown.bots.activities import Activity, register
from toontown.bots.activities import lifekit as kit

TRAVEL_WHY = 'toontask'
HQ_TASK_CAP = 8         # toons in a Toon HQ at once on a task trip (4 officers + toons waiting for one)


def _areas(goal):
    if goal.get('areas'):
        return list(goal['areas'])
    if goal.get('area') is not None:
        return [goal['area']]
    return []


def serve(bot, goal):
    """The activity that works on this goal here, or None (progress.failed is the caller's)."""
    from toontown.bots.activities import battle as bmod
    d, a, k = bot.director, bot.area, goal['kind']
    areas = _areas(goal)
    if k in ('phone', 'mailbox'):
        try:
            from toontown.bots.activities import home
        except ImportError:
            return None
        return home.GoHome(bot, want=k)
    if k in ('facility', 'boss'):
        return hqServe(bot, goal)
    if areas and a.id not in areas:
        return TaskRun(bot, goal, 'travel')
    if k == 'visit' and goal.get('block') is not None:
        taken, act = cogTakenNpc(bot, goal)
        if taken:
            return act             # None (a run already there / not ready): progress.failed, later
    if k in ('visit', 'gettask', 'buygag', 'friend', 'treat'):
        return TaskRun(bot, goal, k)
    if k == 'heal':
        from toontown.bots.activities.playground import Hangout
        return Hangout(bot) if Hangout.runsIn(bot) else None
    if k == 'restock':
        return bmod.Restock(bot) if bmod.Restock.runsIn(bot) else None
    if k in ('trolley', 'beans'):
        from toontown.bots.activities.trolley import Trolley
        if Trolley.runsIn(bot):
            return Trolley(bot)
        if k == 'beans':
            from toontown.bots.activities.fishing import Fishing
            if Fishing.runsIn(bot):
                return Fishing(bot)
        # the trolley is full (4 seats) or just left: wait around the playground for the next one, as a player does
        from toontown.bots.activities.playground import Hangout
        return Hangout(bot) if Hangout.runsIn(bot) else None
    if k == 'fish':
        from toontown.bots.activities.fishing import Fishing, StreetFishing
        cls = StreetFishing if a.kind == 'street' else Fishing
        return cls(bot) if cls.runsIn(bot) else None
    if k == 'cogs':
        qid = goal['qid']
        want = cogFilter(bot, qid)
        from toontown.bots import battlebrain as bb
        if bmod.StreetBattle.runsIn(bot):
            # a fight already going with a Cog my task counts: join it (every toon in it gets the Cogs)
            fight = taskBattle(bot, qid)
            if fight is not None:
                bb.STATS.count('task_cogs_join')
                return bmod.StreetBattle(bot, mode='join', battle=fight)
            if matchingCog(bot, want):
                bb.STATS.count('task_cogs_fight')
                return bmod.StreetBattle(bot, suitFilter=want)
            bb.STATS.count('task_cogs_none_here')
        else:
            bb.STATS.count('task_cogs_cant_fight')
        from toontown.bots.activities.street import StreetWalk
        return StreetWalk(bot) if StreetWalk.runsIn(bot) else None      # walk the street looking for one
    if k == 'building':
        from toontown.bots.activities import cogbuilding as cb
        pick = buildingFilter(goal)
        if cb.CogBuilding.canRunFor(bot, pick):
            return cb.CogBuilding(bot, task=goal, want=pick)
        from toontown.bots.activities.street import StreetWalk
        return StreetWalk(bot) if StreetWalk.runsIn(bot) else None
    return None


def cogTakenNpc(bot, goal):
    """The task's NPC sits in a building the Cogs have taken (no door to walk into: the 09-30 advance soak, Oak St
    block 21 a 4-floor Cashbot building): take it back first, as a player does - the building run waits at its door
    asking for help. (taken, activity or None); taken False = a toon building (walk in)."""
    from toontown.bots.activities import cogbuilding as cb
    blk = goal.get('block')
    hit = next((x for x in cb.suitBuildings(bot.director, bot.area)
                if (x[0].get('setBlock') or (None,))[0] == blk), None)
    if hit is None:
        return False, None
    target = hit[0].doId
    g = cb.RUNS.get(target)
    if g is not None and not g.go and bot.avId != g.leader and len(g.members) < 3:
        # another toon is gathering a group at that door (the same NPC's task, say): go and help
        return True, cb.CogBuilding(bot, role='member', group=g)
    want = lambda bldgObj, floors: bldgObj.doId == target
    task = dict(goal, kind='building', floors=None, text='take back a building')
    if cb.CogBuilding.canRunFor(bot, want):
        from toontown.bots import battlebrain as bb
        bb.STATS.count('task_npc_taken_back')
        return True, cb.CogBuilding(bot, task=task, want=want)
    return True, None


def cogFilter(bot, qid):
    """A street Cog this task counts that this bot can handle alone (the owner 09-25: no fights it can't take)."""
    from toontown.bots.activities import battle as bmod
    from toontown.bots.activities.commands import cogCounts

    mates = []                # counted once per pick: the free toons near that would answer "Can you help me?"

    def want(o):
        if not cogCounts(qid, o, o.zoneId, bot.avId):
            return False
        if bmod.canTake(bot, o)[0] is not None:
            return True
        if not mates:
            mates.append(bmod.teamMates(bot))
        return mates[0] > 0 and bmod.canTake(bot, o, mates[0])[0] is not None
    return want


def taskBattle(bot, qid):
    """A street battle on this street with a free spot and a Cog in it that this task counts (a bot's or a player's:
    players help each other that way)."""
    from toontown.bots.activities import battle as bmod
    from toontown.bots.activities.commands import cogCounts
    d = bot.director
    best = None
    for o in bmod.streetBattles(d, bot.area):
        m = o.get('setMembers')
        st = (o.get('setState') or ('',))[0]
        if not m or len(m[6]) >= 4 or st not in ('FaceOff', 'WaitForInput', 'MakeMovie', 'PlayMovie', 'WaitForJoin'):
            continue
        p = o.get('setPosition')
        dist = math.hypot(p[0] - bot.pos[0], p[1] - bot.pos[1]) if p else 1e9
        if dist > 300.0:
            continue
        for sid in m[0]:
            so = next((v.objects.get(sid) for v in (d.viewOf(z) for z in bot.area.zones)
                       if v is not None and sid in v.objects), None)
            if so is not None and cogCounts(qid, so, so.zoneId, bot.avId):
                if best is None or dist < best[0]:
                    best = (dist, o)
                break
    return best[1] if best else None


def matchingCog(bot, want):
    """A Cog my task counts that StreetBattle can pick right now: walking its path (not in a battle, not at a
    door) and within the chase (the pick's own test: a Cog in someone's fight made 110 empty trips in 15 min)."""
    from toontown.bots.activities import battle as bmod
    d, a = bot.director, bot.area
    battles = [o.get('setPosition') for o in bmod.streetBattles(d, a)]
    for z in a.zones:
        v = d.viewOf(z)
        if v is None:
            continue
        for o in v.objects.values():
            if o.className != 'DistributedSuit':
                continue
            pos, h, walking = bmod.suitNow(d, a, o)
            if pos is None or not walking:
                continue
            if math.hypot(pos[0] - bot.pos[0], pos[1] - bot.pos[1]) >= bmod.TASK_CHASE:
                continue
            if any(p and math.hypot(p[0] - pos[0], p[1] - pos[1]) < 40.0 for p in battles):
                continue
            if want(o):
                return True
    return False


def buildingFilter(goal):
    """A Cog building the task counts: its track (Any = every one) and at least its floors."""
    from toontown.quest import Quests
    track, floors = goal.get('track'), goal.get('floors') or 1

    def pick(bldgObj, numFloors):
        data = bldgObj.get('setSuitData') or (0, 0, 1)
        if track not in (None, Quests.Any) and chr(data[0]) != track:
            return False
        return numFloors >= floors
    return pick


def hqServe(bot, goal):
    """A factory / mint task or a boss task: the Cog HQ place (the existing facility / lobby activities run there)."""
    from toontown.bots import taskplan
    d, a = bot.director, bot.area
    if goal['kind'] == 'facility':
        # the facility's own entrance (the Factory Exterior, the mint lobby ...): wait there asking for help
        from toontown.bots.activities import faclobby
        from toontown.bots.coghq import common as cm
        kind = faclobby._kindOf(goal)
        f = cm.FACILITIES.get(kind)
        ext = d.world.areas.get(f['ext']) if f is not None else None
        if ext is None or not d.eligible(bot, ext):
            return None
        if a is ext:
            return faclobby.TaskFacWait(bot, goal, kind)
        return TaskRun(bot, dict(goal, area=ext.id), 'travel')
    hq = taskplan.FACILITY_OF.get(goal.get('facility')) or taskplan.BOSS_OF.get(goal.get('boss'))
    if hq is None:
        return None
    dests = [x for x in d.world.areas.values() if x.hood == hq and d.eligible(bot, x)]
    if not dests:
        return None
    if a in dests:
        return None          # there already: the Cog HQ activities (faclobby waits, runs, boss lobbies) take it
    return TaskRun(bot, dict(goal, area=dests[0].id), 'travel')


@register
class ToonTask(Activity):
    """The director's pick for a bot that works its goal now; it hands over to the serving activity at once."""
    name = 'toontask'
    weight = 40.0
    progress = True
    anchored = True
    label = 'toontask'

    @classmethod
    def canRun(cls, bot):
        if bot.area is None or bot.node is None or getattr(bot, 'crew', False):
            return False
        return progress.wantsWork(bot)

    def start(self):
        bot, goal = self.bot, self.bot.goal
        if goal is None:
            return False
        act = serve(bot, goal)
        if act is None:
            progress.failed(bot, 'nothing serves %s in %s' % (goal['kind'], bot.area.id))
            return False
        act.taskGoal = goal
        act.progress = True
        act.anchored = True
        ok = bot.startActivity(act)          # this one ends ('replaced'); the serving one runs from now
        if ok:
            progress.worked(bot)
        elif not getattr(act, 'full', False):
            progress.failed(bot, '%s would not start (%s)' % (getattr(act, 'label', act.name), getattr(act, 'why', '?')))
        return ok


class TaskRun(Activity):
    """Travel to the goal's area, a talk with the task's NPC / an HQ officer, a gag buy for a task, a new friend."""
    name = 'toontask'
    progress = True
    anchored = True

    def __init__(self, bot, goal, mode):
        Activity.__init__(self, bot)
        self.goal = goal
        self.mode = mode
        self.visit = None
        self.until = 0.0
        self.label = 'toontask-' + mode
        self.friend = None
        self.full = False
        self.why = ''

    # -- start -------------------------------------------------------------------------------------------
    def start(self):
        bot, d = self.bot, self.director
        if self.mode == 'travel':
            dest = d.world.areas.get(_areas(self.goal)[0])
            if dest is None:
                return False
            if random.random() < 0.3:
                progress.say(bot, 'busy')
            return d.travel(bot, dest, why=TRAVEL_WHY) or False
        if self.mode == 'friend':
            return self.__startFriend()
        place, doId = self.__door()
        if place is None:
            self.why = 'no door'
            return False
        zone = kit.interiorZone(d.world.doorZone(bot.area, place), place['extra']['block'])
        cap = kit.CAP.get(place['kind'], 2) + 1
        if place['kind'] == 'hq':
            cap = max(cap, HQ_TASK_CAP)          # 4 officers and a few toons waiting for one
        if next((i for i in range(cap) if self.claim(('interior', zone, i))), None) is None:
            self.full = True                     # a crowded HQ / shop: later, not a failure
            return False
        v = kit.Visit(self, place, doId, stay=random.uniform(25.0, 45.0))
        if self.mode == 'buygag':
            bot.mustBuy = (self.goal['track'], self.goal['level'], self.goal['num'])
        elif self.mode == 'treat':                 # HALLOWEEN: "Trick or Treat!" in the shop (halloween.py)
            v.wantMode = 'treat'
        else:
            v.want = self.goal.get('npc', 'hq')
            v.wantMode = self.mode
        if not v.begin():
            self.releaseAll()
            self.why = 'no walk to the door'
            return False
        self.visit = v
        if self.mode == 'gettask' and random.random() < 0.3:
            progress.say(bot, 'gettask')
        return True

    def __door(self):
        """(place, door doId) of the building: the task NPC's block, an HQ (the nearest), or the gag shop."""
        bot, d, a = self.bot, self.director, self.bot.area
        if self.mode == 'buygag':
            cands = [p for p in a.doors if p['kind'] == 'gagshop']
        elif self.goal.get('npc', 'hq') == 'hq':
            cands = [p for p in a.doors if p['kind'] == 'hq']
        else:
            cands = [p for p in a.doors if p['extra'].get('block') == self.goal.get('block')]
        found = []
        for p in cands:
            ex = p['extra']
            doId = d.doorDOs.get((a.id, ex['block'], ex.get('door', 0)))
            if doId is not None and d.canWalkTo(bot, p):
                found.append((math.hypot(p['pos'][0] - bot.pos[0], p['pos'][1] - bot.pos[1]), p, doId))
        if not found:
            return None, None
        found.sort(key=lambda f: f[0])
        return found[0][1], found[0][2]

    def __startFriend(self):
        """FriendQuest: a toon standing near that is not a friend yet (another bot: it accepts, as bots do)."""
        bot, d = self.bot, self.director
        mine = set(bot.friendIds()) if hasattr(bot, 'friendIds') else set()
        cands = [b for b in d.bots.values() if b is not bot and b.state == 'present' and b.zoneId == bot.zoneId
                 and b.avId not in mine and b.travel is None and not getattr(b, 'crew', False)
                 and math.hypot(b.pos[0] - bot.pos[0], b.pos[1] - bot.pos[1]) < 120.0]
        if not cands:
            return False
        self.friend = min(cands, key=lambda b: math.hypot(b.pos[0] - bot.pos[0], b.pos[1] - bot.pos[1]))
        node = bot.area.nodeNear(self.friend.pos[0], self.friend.pos[1], 6.0)
        if node is None or not bot.walkTo(node):
            return False
        self.until = globalClock.getRealTime() + 40.0
        return True

    # -- steps ---------------------------------------------------------------------------------------------
    def step(self, now):
        if self.visit is not None:
            ok = self.visit.step(now)
            self.label = self.visit.label
            return ok
        if self.mode == 'friend':
            return self.__friendStep(now)
        return False

    def __friendStep(self, now):
        bot, other = self.bot, self.friend
        if bot.path:
            return now < self.until
        if other.state != 'present' or other.zoneId != bot.zoneId:
            return False
        bot.faceTo(other.pos)
        try:
            from toontown.bots import social
        except ImportError:
            progress.failed(bot, 'no social module')
            return False
        kit.say(bot, random.choice((1, 100, 105)), now, force=True)
        ok = social.makeFriend(bot, other)
        progress.log(bot, 'FRIEND %s -> %s %s' % (bot.avId, other.avId, 'asked' if ok else 'failed'))
        return False

    def onDirect(self, fieldName, args):
        if self.visit is not None:
            self.visit.onDirect(fieldName, args)

    def onField(self, obj, fieldName, args):
        if self.visit is not None:
            self.visit.onField(obj, fieldName, args)

    def stop(self, why):
        self.bot.mustBuy = None
        v = self.visit
        if v is not None:
            if v.phase not in ('walk', 'out', 'enter'):
                v.abort(why)
            elif self.bot.path and why != 'travel':
                self.bot.stopWalking()
