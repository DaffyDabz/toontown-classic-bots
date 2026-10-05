"""PROGRESSION (owner 09-30): every bot is a player working up from level 1 - its own ToonTasks, beans and gag XP.

  tick(bot, now)     BotToon.tick calls it; every UPDATE s the bot re-reads its own toon (the owner fields the AI sends
                     it, as a client's ShtikerBook does) and taskplan.plan() names what it works on: bot.goal, bot.goalText.
  wantsWork(bot)     the director's free-bot pick: does the bot work its goal now (activities/toontask.py) or just play?
                     By style: a grinder almost always, a regular mostly, a casual now and then.
  chooseQuest / chooseTrack   an HQ officer's offer: the bot picks like a player (the easiest; a grinder the quickest).
  failed / worked    a goal that keeps failing to start is set aside for a while (taskplan.STUCK_FOR).
  say(bot, what)     the lines a player says about it (SpeedChat only, owner 5a): out of gags 1413, beans 1601,
                     laff 410, a ToonTask line from the ToonTask menu (setSCToontask), help 514 ...

Every goal change and every level-up (tier, laff, a new track) goes to <run>/bots-progress.log (proof numbers).
"""
import os
import random
import time

from direct.directnotify import DirectNotifyGlobal

from toontown.bots import taskplan

notify = DirectNotifyGlobal.directNotify.newCategory('BotProgress')
UPDATE = 5.0                 # s between goal re-reads
WORK = {'grinder': 0.92, 'regular': 0.65, 'casual': 0.35}    # share of free picks spent on the goal
FAIL_LIMIT = 3               # starts that failed within FAIL_WINDOW -> set the goal aside
FAIL_WINDOW = 600.0
STALE = 480.0                # s on one goal with no task progress: try its next street
CHAT = 0.06                  # chance per goal re-read (~5 s) of a line about it
LINES = {                    # OTPLocalizer SpeedChatStaticText ids a player says about what he is doing
    'beans': (1601, 1601, 803),                 # "I need more jellybeans." / "Sorry, I'm busy getting Jellybeans!"
    'restock': (1413, 1413),                    # "I need more gags."
    'heal': (410, 1414),                        # "I need more Laff points." / "I need a Toon-Up."
    'gettask': (1299,),                         # "I need to get a ToonTask."
    'help': (514, 1410),                        # "Can you help me?" / "Help!"
    'busy': (804,),                             # "Sorry, I'm busy completing a ToonTask!"
    'ask': (1200,),                             # "What ToonTask are you working on?"
}
_LOG = None


def runDir(bot):
    return getattr(bot.director, 'runDir', '.')


def log(bot, text):
    global _LOG
    try:
        if _LOG is None:
            _LOG = open(os.path.join(runDir(bot), 'bots-progress.log'), 'a')
        _LOG.write('%s %s %s\n' % (time.strftime('%m-%d %H:%M:%S'), bot.avId, text))
        _LOG.flush()
    except Exception:
        pass


def own(bot, field, default=None):
    v = bot.ownFields.get(field)
    return v if v is not None else default


def attach(bot):
    if getattr(bot, 'prog', None) is None:
        bot.prog = Brain(bot)
    return bot.prog


def tick(bot, now):
    if getattr(bot, 'crew', False) or bot.state != 'present' or not bot.ownFields:
        return
    b = attach(bot)
    if now >= b.next:
        b.next = now + UPDATE * random.uniform(0.8, 1.2)
        b.update(now)


def wantsWork(bot):
    b = getattr(bot, 'prog', None)
    if b is None or bot.goal is None:
        return False
    if b.goalKey is not None and b.blocked.get(b.goalKey, 0.0) > time.time():
        return False
    return random.random() < WORK.get(getattr(bot, 'style', 'regular'), 0.65)


def failed(bot, why):
    b = getattr(bot, 'prog', None)
    if b is not None:
        b.failed(why)


def worked(bot):
    b = getattr(bot, 'prog', None)
    if b is not None:
        b.fails = []


def say(bot, what, now=None):
    from toontown.bots.activities import lifekit as kit
    ids = LINES.get(what)
    if ids:
        return kit.say(bot, random.choice(ids), now)
    return False


def sayTask(bot, qid=None):
    """A ToonTask line from the SpeedChat ToonTask menu ("I need to defeat 3 Cogs." ...), like a player shows it."""
    from toontown.bots.activities import lifekit as kit
    for q in questsOf(bot):
        if qid is None or q[0] == qid:
            bot.send('setSCToontask', [q[0], q[2], q[4], 0])
            now = globalClock.getRealTime()
            bot._lifeNextSay = now + random.uniform(*kit.BOT_GAP)       # the zone's line budget, as kit.say keeps it
            kit.LIFE.zoneLast[bot.zoneId] = now
            kit.LIFE.count('sc_task_lines')
            return True
    return False


def questsOf(bot):
    flat = list((own(bot, 'setQuests', ([],)))[0])
    return [tuple(flat[i:i + 5]) for i in range(0, len(flat) - 4, 5)]


_TAKEN = {}                  # (street, block) -> (checked at, taken)


def npcTaken(bot, goal):
    """Is the task NPC's building a Cog building now? (the street's building view, checked every 30 s)"""
    key, now = (goal.get('area'), goal.get('block')), time.time()
    hit = _TAKEN.get(key)
    if hit is not None and now - hit[0] < 30.0:
        return hit[1]
    taken = False
    try:
        from toontown.bots.activities import cogbuilding as cb
        area = bot.director.world.areas.get(key[0])
        if area is not None and area.kind == 'street':
            taken = any((x[0].get('setBlock') or (None,))[0] == key[1] for x in cb.suitBuildings(bot.director, area))
    except Exception:
        taken = False
    _TAKEN[key] = (now, taken)
    return taken


# ---- an HQ officer's offer ---------------------------------------------------------------------------------
def chooseQuest(bot, offered):
    """offered = the flat [questId, rewardId, toNpcId, ...] of QUEST_CHOICE. Returns the questId to take."""
    ids = [offered[i] for i in range(0, len(offered) - 2, 3)] or list(offered[:1])
    if not ids:
        return 0
    b = getattr(bot, 'prog', None)
    style = getattr(bot, 'style', 'regular')
    if b is None or style == 'casual' and random.random() < 0.5:
        return random.choice(ids)
    s = b.snapshot(time.time())

    def cost(qid):
        g = taskplan.taskGoal(bot.director.world, s, (qid, 0, 0, 0, 0))
        if g is None:
            return 99
        c = taskplan.ORDER.get(g['kind'], 9)
        q = taskplan.questOf(qid)
        try:
            c += 0.1 * (q.getNumQuestItems() or 0)
        except Exception:
            pass
        here = g.get('area') == s.areaId or s.areaId in g.get('areas', ())
        return c - (1.0 if here else 0.0)
    return min(ids, key=lambda q: (cost(q), random.random()))


def chooseTrack(bot, tracks):
    """A gag track choice (TRACK_CHOICE): each regular has its favourites, the same every time (its seed)."""
    if not tracks:
        return -1
    rng = random.Random(bot.avId * 13)
    order = [2, 3, 6, 0, 1]          # lure, sound, drop, toon-up, trap: shuffled per toon
    rng.shuffle(order)
    return min(tracks, key=lambda t: order.index(t) if t in order else 9)


# ---- the brain -----------------------------------------------------------------------------------------------
class Brain:
    def __init__(self, bot):
        self.bot = bot
        self.next = 0.0
        self.goalKey = None
        self.goalSince = 0.0
        self.progressAt = 0.0
        self.lastProgress = None
        self.blocked = {}
        self.noTaskUntil = 0.0
        self.fails = []
        self.areaTurn = {}           # goal key -> index into its areas (a stale street: the next one)
        self.seen = None             # (tier, maxHp, tracks) last logged
        bot.goal = None
        bot.goalText = ''

    def snapshot(self, now):
        from toontown.bots import battlebrain as bb
        bot = self.bot
        s = taskplan.Snapshot()
        s.avId = bot.avId
        s.now = now
        s.quests = questsOf(bot)
        rh = own(bot, 'setRewardHistory', (0, []))
        s.tier = rh[0]
        s.carry = own(bot, 'setQuestCarryLimit', (1,))[0]
        s.hp = own(bot, 'setHp', (15,))[0]
        s.maxHp = own(bot, 'setMaxHp', (15,))[0]
        s.money = own(bot, 'setMoney', (0,))[0]
        inv, exp = bb.loadInventory(bot)
        if inv is not None:
            s.attack = sum(inv.numItem(t, l) for t in bb.ATTACK_TRACKS for l in range(7))
            s.numItem = inv.numItem
            s.stocked = bb.stocked(bot)
        s.friends = len(list(own(bot, 'setFriendsList', ([],))[0]))
        s.areaId = bot.area.id if bot.area is not None else taskplan.BotWorld.TTC
        hoods = set(own(bot, 'setTeleportAccess', ([],))[0]) | {taskplan.BotWorld.TTC, taskplan.hoodOfTier(s.tier)}
        s.hoods = [h for h in taskplan.BotWorld.HOODS if h in hoods]
        s.style = getattr(bot, 'style', 'regular')
        s.blocked = self.blocked
        s.noTaskUntil = self.noTaskUntil
        try:                                           # HALLOWEEN: Trick-or-Treat shops it has not found yet
            from toontown.bots import halloween
            s.treats = halloween.treatsFor(bot)
        except Exception:
            s.treats = []
        return s

    def update(self, now):
        bot = self.bot
        wall = time.time()
        s = self.snapshot(wall)
        self.__levels(s)
        try:
            goal = taskplan.plan(s, bot.director.world)
        except Exception:
            import traceback
            bot.director.error('progress plan', traceback.format_exc())
            goal = None
        if goal is not None and goal['kind'] == 'visit' and goal.get('block') is not None and npcTaken(bot, goal):
            # the NPC's building is a Cog building: ready up for taking it back (activities/toontask.cogTakenNpc)
            goal = taskplan.ready(s, goal, taskplan.playgroundOf(bot.director.world, taskplan.hoodOf(bot.director.world, s)))
        if goal is not None:
            k = taskplan.key(goal)
            areas = goal.get('areas')
            if areas and len(areas) > 1:            # a stale street: its next one
                turn = self.areaTurn.get(k, 0) % len(areas)
                goal['areas'] = areas[turn:] + areas[:turn]
        k = taskplan.key(goal) if goal is not None else None
        prog = [q[4] for q in s.quests]
        if prog != self.lastProgress:
            self.lastProgress = prog
            self.progressAt = wall
        if k != self.goalKey:
            self.goalKey = k
            self.goalSince = wall
            self.progressAt = wall
            log(bot, 'GOAL %s tier %d laff %d/%d beans %d gags %d tasks %s' % (
                goal['kind'] + (' q%s' % goal['qid'] if goal.get('qid') else '') if goal else 'none',
                s.tier, s.hp, s.maxHp, s.money, s.attack, [q[0] for q in s.quests]))
        elif goal is not None and goal.get('areas') and wall - self.progressAt > STALE:
            self.areaTurn[k] = self.areaTurn.get(k, 0) + 1         # nothing here for a while: the next street
            self.progressAt = wall
            log(bot, 'STALE %s: next street' % (k,))
        bot.goal = goal
        bot.goalText = goal['text'] if goal else ''
        if goal is not None:
            self.__chatter(now, goal)

    def __chatter(self, now, goal):
        """Now and then, what a player says about what he is doing (the zone's line budget still applies)."""
        bot = self.bot
        act = bot.activity.name if bot.activity is not None else None
        if bot.travel is not None or act in ('battle', 'trolley', 'coghq', 'boss') or random.random() > CHAT:
            return
        k = goal['kind']
        if k in ('cogs', 'building', 'fish') and bot.area is not None and bot.area.kind == 'street':
            r = random.random()
            if r < 0.5:
                from toontown.bots.activities import lifekit as kit
                if kit.canSay(bot, now):
                    sayTask(bot, goal.get('qid'))
            elif r < 0.75:
                say(bot, 'help', now)
            else:
                say(bot, 'ask', now)
        elif k in ('beans', 'restock', 'heal', 'gettask'):
            say(bot, k, now)

    def __levels(self, s):
        """Level-ups (what the kids see move): laff, tier, tracks."""
        bot = self.bot
        tracks = tuple(own(bot, 'setTrackAccess', ((0,) * 7,))[0])
        now = (s.tier, s.maxHp, tracks)
        if self.seen is not None and now != self.seen:
            log(bot, 'LEVELUP tier %d->%d laff %d->%d tracks %s->%s' % (
                self.seen[0], s.tier, self.seen[1], s.maxHp, list(self.seen[2]), list(tracks)))
        self.seen = now

    def failed(self, why):
        wall = time.time()
        self.fails = [t for t in self.fails if wall - t < FAIL_WINDOW] + [wall]
        if self.goalKey is not None and len(self.fails) >= FAIL_LIMIT:
            self.blocked[self.goalKey] = wall + taskplan.STUCK_FOR
            log(self.bot, 'SETASIDE %s: %s' % (self.goalKey, why))
            self.fails = []
            self.next = 0.0

    def noTasks(self, why):
        """An HQ officer had nothing (finish your tier / no more tasks): not again for a while."""
        self.noTaskUntil = time.time() + taskplan.STUCK_FOR
        log(self.bot, 'NOTASK %s' % why)
        self.next = 0.0
