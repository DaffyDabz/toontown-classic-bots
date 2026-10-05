"""PROGRESSION (owner 09-30): what a bot works on next, from its own toon - like a player reading his ShtikerBook.

"All of them should start at level one and level up with the player ... They're trying to do their toon tasks and level
up and compete with the player ... They need jelly beans to buy gags ... They need everything a player would need."

Pure rules, no networking: plan(state, world) -> a goal dict, or None (nothing to work on: just play).
  state   Snapshot of the toon (its owner fields, read by progress.py) - quests, tier, laff, beans, pouch, friends,
          where it stands, which hoods it has reached, its style.
  world   BotWorld.World (areas by id, their doors / places).

A goal:  {'kind': ..., 'text': what a player would say he is doing, 'qid': the task (or None), plus per kind:}
  heal      area            walk to a playground and stand about (the AI heals every 30 s there)
  beans     area            ride the trolley (or fish) until the jar has enough for gags
  restock   area            the gag shop
  gettask   area, npc='hq'  talk to an HQ officer (a free ToonTask slot)
  visit     area, npc, block  talk to the task's NPC (a finished task, a Visit / Deliver / Track Choice task)
  buygag    area, track, level, num   a Deliver Gag task: buy them first (then 'visit')
  cogs      areas, qid      defeat the Cogs the task counts on those streets (the quest's own doesCogCount)
  building  areas, track, floors   take back a Cog building: wait at its door asking for help
  facility  facility ('factory' | 'mint' | 'da' | 'cgc')   a factory / mint task, a Cog suit part task
  boss      boss ('vp' | 'cfo' | 'cj' | 'ceo')
  trolley   area            a Trolley task
  fish      areas           recover an item from the water
  phone / mailbox           go home to the estate
  friend                    make a friend
"""
from toontown.bots import BotWorld

# the hood of a reward tier (Quests.TT_TIER ...): where a toon on that tier does its tasks
TIER_HOODS = ((0, BotWorld.TTC), (4, BotWorld.DD), (7, BotWorld.DG), (8, BotWorld.MML), (11, BotWorld.BR),
              (14, BotWorld.DDL))
LOW_LAFF = 0.4          # below this share of its laff a toon heals before anything else
FIGHT_LAFF = 0.6        # ... and before a fight below this (battle.canFight: a bot starts no fight under it)
MIN_ATTACK = 8          # fewer attack gags than this: the gag shop (or beans for it) - battle.canFight's own 8
SHOP_BEANS = 15         # the least worth a gag shop trip (battle.Restock's own 15)
BROKE_ATTACK = 2        # a toon with no beans for the shop fights with what it has (a new toon: its cupcake and
                        # squirting flower), as a player does; battle.fightGags keeps the same rule
STUCK_FOR = 600.0       # s: a goal that could not start this often is set aside for a while
FACILITY_OF = {'factory': 11000, 'mint': 12000, 'da': 13000, 'cgc': 10000}
FACILITY_IN = dict((hq, kind) for kind, hq in FACILITY_OF.items())    # a Cog HQ hood -> its facility
BOSS_OF = {'vp': 11000, 'cfo': 12000, 'cj': 13000, 'ceo': 10000}


def hoodOfTier(tier):
    hood = BotWorld.TTC
    for t, h in TIER_HOODS:
        if tier >= t:
            hood = h
    return hood


class Snapshot:
    """What plan() reads. progress.py fills it from the bot's owner fields; the sim fills it by hand."""
    def __init__(self):
        self.avId = 0
        self.quests = []            # [(questId, fromNpcId, toNpcId, rewardId, progress)]
        self.tier = 0
        self.carry = 1              # questCarryLimit
        self.hp = 15
        self.maxHp = 15
        self.money = 0
        self.attack = 2             # attack gags in the pouch
        self.stocked = False
        self.numItem = lambda track, level: 0
        self.friends = 0
        self.areaId = BotWorld.TTC
        self.hoods = [BotWorld.TTC]  # hoods it has reached (teleport access + its tier hood)
        self.style = 'regular'
        self.blocked = {}           # goal key -> until (a goal that kept failing: set aside)
        self.noTaskUntil = 0.0      # an HQ officer said 'finish your tier first' / 'no more tasks'
        self.treats = []            # HALLOWEEN: [(goal, area, block)] Trick-or-Treat shops not found yet
        self.now = 0.0


class _Av:
    """Just enough of a toon for Quest.getCompletionStatus (DeliverGag reads the pouch, Friend the friends list)."""
    def __init__(self, s):
        self.s = s
        self.inventory = self

    def numItem(self, track, level):
        return self.s.numItem(track, level)

    def getFriendsList(self):
        return [0] * self.s.friends

    def getDoId(self):
        return self.s.avId


def _quests():
    from toontown.quest import Quests
    return Quests


def questOf(qid):
    try:
        return _quests().getQuest(qid)
    except Exception:
        return None


def isDone(s, desc):
    Q = _quests()
    q = questOf(desc[0])
    if q is None:
        return False
    try:
        return q.getCompletionStatus(_Av(s), desc) == Q.COMPLETE
    except Exception:
        return False


# ---- where things are ------------------------------------------------------------------------------------
def npcPlace(world, npcId):
    """(area id, block) of a quest NPC's building, or None (HQ officers: 'hq')."""
    try:
        from toontown.toon import NPCToons
        zone = NPCToons.getNPCZone(npcId)
    except Exception:
        return None
    if not zone:
        return None
    block = zone % 100
    base = zone - block - 500
    if base in world.areas:
        return base, block
    return None


def streetsOf(world, hood):
    return [a.id for a in world.areas.values() if a.kind == 'street' and a.hood == hood]


def playgroundOf(world, hood):
    a = world.areas.get(hood)
    return a.id if a is not None else BotWorld.TTC


def hqArea(world, s):
    """The nearest Toon HQ: the one on the street / playground it stands in, else its tier hood's playground."""
    a = world.areas.get(s.areaId)
    if a is not None and a.hood in s.hoods and any(p['kind'] == 'hq' for p in a.doors):
        return a.id
    return playgroundOf(world, hoodOfTier(s.tier) if hoodOfTier(s.tier) in s.hoods else BotWorld.TTC)


def locationStreets(world, s, loc):
    """Streets where a location-based task counts: Anywhere = the streets of the hoods it knows (its tier hood's
    first), a hood = that hood's streets, a street = that street."""
    Q = _quests()
    if loc == Q.Anywhere:
        home = hoodOfTier(s.tier)
        out = streetsOf(world, home) if home in s.hoods else []
        for h in reversed(s.hoods):
            out += [z for z in streetsOf(world, h) if z not in out]
        return out
    from toontown.hood import ZoneUtil
    if ZoneUtil.isPlayground(loc):
        return streetsOf(world, loc)
    branch = ZoneUtil.getCanonicalBranchZone(loc)
    return [branch] if branch in world.areas else []


def near(s, areas):
    """The current area first, when it is one of them."""
    return sorted(areas, key=lambda z: z != s.areaId)


# ---- one task -> a goal --------------------------------------------------------------------------------
def visitGoal(world, s, desc, why):
    Q = _quests()
    qid, toNpc = desc[0], desc[2]
    if toNpc in (Q.ToonHQ, Q.Any, Q.ToonTailor):
        return {'kind': 'visit', 'qid': qid, 'npc': 'hq', 'area': hqArea(world, s), 'text': why}
    where = npcPlace(world, toNpc)
    if where is None:
        return {'kind': 'visit', 'qid': qid, 'npc': 'hq', 'area': hqArea(world, s), 'text': why}
    return {'kind': 'visit', 'qid': qid, 'npc': toNpc, 'area': where[0], 'block': where[1], 'text': why}


def taskGoal(world, s, desc):
    """The goal for one unfinished task, or None when a bot cannot do it (it waits: another task first)."""
    Q = _quests()
    qid, fromNpc, toNpc, reward, progress = desc
    q = questOf(qid)
    if q is None:
        return None
    if isDone(s, desc):
        return visitGoal(world, s, desc, 'finish a ToonTask')
    if isinstance(q, (Q.VisitQuest, Q.DeliverItemQuest, Q.TrackChoiceQuest)):
        return visitGoal(world, s, desc, 'deliver / visit')
    if isinstance(q, Q.DeliverGagQuest):
        track, level = q.getGagType()
        if s.numItem(track, level) >= q.getNumGags():
            return visitGoal(world, s, desc, 'deliver gags')
        return {'kind': 'buygag', 'qid': qid, 'track': track, 'level': level, 'num': q.getNumGags(),
                'area': playgroundOf(world, hoodOfTier(s.tier) if hoodOfTier(s.tier) in s.hoods else BotWorld.TTC),
                'text': 'buy gags for a task'}
    if isinstance(q, Q.TrolleyQuest):
        return {'kind': 'trolley', 'qid': qid, 'area': playgroundOf(world, hoodOf(world, s)), 'text': 'ride the trolley'}
    if isinstance(q, Q.FriendQuest):
        return {'kind': 'friend', 'qid': qid, 'text': 'make a friend'}
    if isinstance(q, Q.PhoneQuest):
        return {'kind': 'phone', 'qid': qid, 'text': 'call Clarabelle'}
    if isinstance(q, Q.MailboxQuest):
        return {'kind': 'mailbox', 'qid': qid, 'text': 'check the mailbox'}
    if isinstance(q, Q.MinigameNewbieQuest):
        return {'kind': 'trolley', 'qid': qid, 'area': playgroundOf(world, BotWorld.TTC), 'text': 'play a game'}
    if isinstance(q, (Q.VPQuest, Q.RescueQuest)):
        return {'kind': 'boss', 'qid': qid, 'boss': 'vp', 'text': 'fight the VP'}
    if isinstance(q, Q.CFOQuest):
        return {'kind': 'boss', 'qid': qid, 'boss': 'cfo', 'text': 'fight the CFO'}
    if isinstance(q, (Q.MintQuest, Q.SupervisorQuest)):
        return {'kind': 'facility', 'qid': qid, 'facility': 'mint', 'text': 'do a mint'}
    if isinstance(q, (Q.FactoryQuest, Q.CogPartQuest, Q.ForemanQuest)):
        return {'kind': 'facility', 'qid': qid, 'facility': 'factory', 'text': 'do the factory'}
    if isinstance(q, (Q.SkelecogQBase, Q.SkeleReviveQBase)):
        return {'kind': 'facility', 'qid': qid, 'facility': 'factory', 'text': 'skelecogs in the factory'}
    if isinstance(q, Q.LocationBasedQuest) and q.getLocation() != Q.Anywhere:
        # a task in a Cog HQ (its courtyard, a factory, a mint): the Cogs inside its facility count for it
        from toontown.hood import ZoneUtil
        kind = FACILITY_IN.get(ZoneUtil.getCanonicalHoodId(q.getLocation()))
        if kind is not None:
            return {'kind': 'facility', 'qid': qid, 'facility': kind, 'text': 'Cogs in a Cog HQ'}
    if isinstance(q, Q.BuildingQuest):
        streets = locationStreets(world, s, q.getLocation())
        if not streets:
            return None
        return {'kind': 'building', 'qid': qid, 'areas': near(s, streets), 'track': q.getBuildingTrack(),
                'floors': q.getNumFloors(), 'text': 'take a Cog building'}
    if isinstance(q, Q.RecoverItemQuest):
        loc = q.getLocation()
        if q.getHolder() == Q.AnyFish:
            areas = fishAreas(world, s, loc)
            return {'kind': 'fish', 'qid': qid, 'areas': near(s, areas), 'text': 'fish for a task'} if areas else None
        streets = locationStreets(world, s, loc)
        return {'kind': 'cogs', 'qid': qid, 'areas': near(s, streets), 'text': 'recover an item from Cogs'} \
            if streets else None
    if isinstance(q, Q.CogQuest):
        streets = locationStreets(world, s, q.getLocation())
        return {'kind': 'cogs', 'qid': qid, 'areas': near(s, streets), 'text': 'defeat Cogs'} if streets else None
    return None


def hoodOf(world, s):
    a = world.areas.get(s.areaId)
    return a.hood if a is not None and a.hood in s.hoods else BotWorld.TTC


def fishAreas(world, s, loc):
    """Ponds where a fish-up task counts: the task's location (a hood: its playground and streets)."""
    Q = _quests()
    from toontown.hood import ZoneUtil
    if loc == Q.Anywhere:
        hoods = list(s.hoods)
    elif ZoneUtil.isPlayground(loc):
        hoods = [loc]
    else:
        branch = ZoneUtil.getCanonicalBranchZone(loc)
        return [branch] if branch in world.areas and hasPond(world, branch) else []
    out = []
    for h in hoods:
        out += [a.id for a in world.areas.values() if a.hood == h and hasPond(world, a.id)]
    return out


def hasPond(world, areaId):
    a = world.areas.get(areaId)
    try:
        return a is not None and bool(a.wm.places('fishing'))
    except Exception:
        return False


# ---- the plan -----------------------------------------------------------------------------------------------
ORDER = {'visit': 0, 'gettask': 1, 'buygag': 1, 'trolley': 2, 'friend': 2, 'treat': 2.5, 'phone': 3, 'mailbox': 3,
         'cogs': 4, 'fish': 5,
         'building': 6, 'facility': 7, 'boss': 8}
NEEDS_GAGS = ('cogs', 'building', 'facility', 'boss')
TREAT_QID = 90000       # HALLOWEEN: Trick-or-Treat goals' key (90000 + the hunt's goal)
PARTY_KINDS = ('building', 'facility', 'boss')
PARTY_LAFF = 0.8        # the laff a toon brings to a building / factory (the building run's own check is 0.75)


def key(goal):
    return (goal['kind'], goal.get('qid'))


def plan(s, world):
    """The one thing to work on now."""
    playground = playgroundOf(world, hoodOf(world, s))
    if s.maxHp and s.hp < LOW_LAFF * s.maxHp:
        return {'kind': 'heal', 'area': playground, 'text': 'heal up'}
    goals = []
    for desc in s.quests:
        g = taskGoal(world, s, tuple(desc))
        if g is not None and s.blocked.get(key(g), 0.0) <= s.now:
            goals.append(g)
    for gid, area, block in getattr(s, 'treats', ()):       # HALLOWEEN: a Trick-or-Treat shop in a hood it reaches
        g = {'kind': 'treat', 'qid': TREAT_QID + gid, 'goal': gid, 'npc': 0, 'area': area, 'block': block,
             'text': 'trick-or-treat'}
        ar = world.areas.get(area)
        if ar is not None and ar.hood in s.hoods and s.blocked.get(key(g), 0.0) <= s.now:
            goals.append(g)
    if len(s.quests) < s.carry and s.noTaskUntil <= s.now and s.blocked.get(('gettask', None), 0.0) <= s.now:
        goals.append({'kind': 'gettask', 'npc': 'hq', 'area': hqArea(world, s), 'qid': None,
                      'text': 'get a ToonTask'})
    # a toon with no gags to fight with gets gags (or the beans for them) before a fighting task - when its pouch
    # has room: a pouch full of traps and lures cannot take more (the shop's own limit, battle.Restock refuses it;
    # the 09-30 advance soak: restock -> 'nothing serves restock' x3 -> set aside, again and again)
    if s.attack < MIN_ATTACK and not s.stocked and any(g['kind'] in NEEDS_GAGS for g in goals) and \
            not any(g['kind'] in ('visit', 'gettask', 'trolley', 'buygag') for g in goals):
        if s.money >= SHOP_BEANS:
            return {'kind': 'restock', 'area': playground, 'text': 'buy gags'}
        if s.attack < BROKE_ATTACK:
            return {'kind': 'beans', 'area': playground, 'text': 'earn jellybeans'}
    if not goals:
        return None
    # the most pressing kind first; a task on the street it already stands on beats one of the next kind elsewhere
    def rank(g):
        here = g.get('area') == s.areaId or s.areaId in g.get('areas', ())
        return (ORDER.get(g['kind'], 9) - (1.5 if here else 0.0), g.get('qid') or 0)
    goal = min(goals, key=rank)
    if goal['kind'] in NEEDS_GAGS and s.maxHp and s.hp < FIGHT_LAFF * s.maxHp:
        return {'kind': 'heal', 'area': playground, 'text': 'heal up'}
    if goal['kind'] in PARTY_KINDS:
        return ready(s, goal, playground)
    return goal


def ready(s, goal, playground):
    """A building / factory / boss: full laff and a full pouch first, like a player getting ready (the owner's gag
    check, battlebrain.stocked - the building and facility runs refuse a toon without it). Also progress.py's
    visit to an NPC whose building the Cogs have taken (it has to take that building back first)."""
    if s.maxHp and s.hp < PARTY_LAFF * s.maxHp:
        return {'kind': 'heal', 'area': playground, 'text': 'heal up'}
    if not s.stocked:
        if s.money >= SHOP_BEANS:
            return {'kind': 'restock', 'area': playground, 'text': 'buy gags'}
        return {'kind': 'beans', 'area': playground, 'text': 'earn jellybeans'}
    return goal
