"""TTBOTS P6: the trolley ride, the way a kid takes it.

  walk      to the trolley (the walk-map node at its station), claiming one of 4 rider places
  wait      standing by it: board when it is waiting (empty or counting down) and a seat is free;
            a second-to-last seat is left free while a real player is close by
  board     requestBoard (DistributedTrolley.handleEnterTrolley sends exactly that); fillSlotN(me)
            = seated, rejectBoard = walk off
  seated    ride. A real player walks up to a FULL trolley: the last bot to board gets off
            (requestExit, the client's hop-off button), so he gets the seat.
  hop       setMinigameZone(zone, id) arrives on the bot's puppet channel: the bot moves into the
            minigame zone like a client (setLocation) and opens a view of it
  game      base.GameSession runs the framework (join / ready / exit acks) + the game's brain
  purchase  the PurchaseManager (or the bot's own NewbiePurchaseManager): browse 2-9 s, buy gags
            with a VALID setInventory(done=0), requestPlayAgain (sometimes) or requestExit;
            setInventory(done=1) on setPurchaseExit (right away after Exit, as the client does)
  home      back to the playground by teleport (the client's purchase-done teleportIn), view closed

The activity is interruptible only while walking/waiting: once seated nothing pulls the bot away
(a silent bot would abort a real player's game). Numbers: STATS, written to <bot-run-dir>/p6_trolley.json.
"""
import json
import os
import random
import time
import traceback

from direct.directnotify import DirectNotifyGlobal
from panda3d.core import Point3

from otp.otpbase import OTPGlobals
from toontown.bots import minigames
from toontown.bots.activities import Activity, register
from toontown.bots.minigames.base import GameSession
from toontown.bots.BotToon import RUN_SPEED, WALK_SPEED, tripSpeed

notify = DirectNotifyGlobal.directNotify.newCategory('BotTrolley')
QUIET = OTPGlobals.QuietZone
RIDERS = 4                   # bots heading for one trolley at once (it has 4 seats)
REAL_NEAR = 30.0             # ft: a real player this close to the trolley may want a seat
TELEPORT_IN_TIME = 1.8
PURCHASE = ('PurchaseManager', 'NewbiePurchaseManager')
# PurchaseManagerConstants
P_WAITING, P_PLAYAGAIN, P_EXIT = 1, 2, 3

STATS = {'rides': 0, 'boarded': 0, 'rejected': 0, 'yielded': 0, 'games': {}, 'aborts': [], 'purchases': 0,
         'purchaseClosed': 0, 'playAgain': 0, 'exits': 0, 'returned': 0, 'newbie': 0, 'gagsBought': 0,
         'invalidBuy': 0, 'stuck': 0, 'byHood': {}}
RECORDS = {}                 # minigame doId -> record (shared by every bot in that game)
_task = {'on': False}


def _record(obj, act):
    r = RECORDS.get(obj.doId)
    if r is None:
        r = RECORDS[obj.doId] = {'game': minigames.gameName(obj.className), 'doId': obj.doId,
                                 'zone': obj.zoneId, 'trolleyZone': act.trolleyZone,
                                 'players': list(obj.get('setParticipants', ([],))[0]),
                                 'bots': [], 'seen': time.strftime('%H:%M:%S'), 'brain': bool(
                                     minigames.brainFor(obj.className))}
    return r


def _ensureTask(director):
    if _task['on']:
        return
    _task['on'] = True

    def write(task):
        try:
            done = {}
            for r in list(RECORDS.values()):
                if 'ended' not in r and time.time() - r.get('t', time.time()) < 900:
                    continue
                g = done.setdefault(r['game'], {'n': 0, 'aborted': 0, 'points': [], 'realIn': 0})
                g['n'] += 1
                g['aborted'] += 1 if 'aborted' in r else 0
                g['realIn'] += 1 if r.get('real') else 0
                g['points'] += [p for a, p in r.get('points', {}).items() if a in r['bots']]
            STATS['games'] = done
            hist = {}
            for v in list(STATS.get('departures', {}).values()):
                if time.time() - v['t'] > 20:      # everyone who rode it has arrived
                    hist[v['riders']] = hist.get(v['riders'], 0) + 1
            STATS['botRidersPerDeparture'] = hist
            path = os.path.join(director.runDir, 'p6_trolley.json')
            with open(path + '.new', 'w') as f:
                json.dump({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'stats': STATS,
                           'last': sorted(RECORDS.values(), key=lambda r: r.get('t', 0))[-40:]},
                          f, indent=1, default=str)
            os.replace(path + '.new', path)
        except Exception:
            notify.warning('[TTBOTS-P6] stats write: %s' % traceback.format_exc())
        return task.again
    taskMgr.doMethodLater(10.0, write, 'bots-p6-stats')


def _trolleyPlace(area):
    ps = area.wm.places('trolley') if area is not None else []
    return ps[0] if ps else None


@register
class Trolley(Activity):
    name = 'trolley'
    progress = True          # PROGRESSION (owner 1a): none starts while no real player is online
    weight = 2.0
    kinds = ('playground',)

    @classmethod
    def canRun(cls, bot):
        d = bot.director
        _ensureTask(d)
        if not d.clockSynced or bot.node is None or globalClock.getRealTime() < getattr(bot, '_trolleyAgain', 0.0):
            return False
        place = _trolleyPlace(bot.area)
        if place is None or bot.area.placeNode(place) is None or not d.canWalkTo(bot, place):
            return False
        v = bot.view
        if v is None or v.first('DistributedTrolley') is None:
            return False
        return any(d.claims.get(('trolley', bot.zoneId, i)) is None for i in range(RIDERS))

    def __init__(self, bot):
        Activity.__init__(self, bot)
        self.phase = None
        self.until = 0.0
        self.trolleyId = None
        self.trolleyZone = bot.zoneId
        self.area = bot.area
        self.seat = None
        self.boardedAt = 0.0
        self.fast = False
        self.mgZone = None
        self.mgView = None
        self.sessions = {}       # minigame doId -> GameSession
        self.pm = None           # the PurchaseManager doId I act on
        self.pmPhase = None
        self.pmUntil = 0.0
        self.purchase = None
        self.gamesPlayed = 0
        self.maxGames = random.choice((1, 1, 2, 2, 3, 4))
        self.lastSeen = 0.0      # last time a minigame or purchase manager of mine was in the zone
        self.why = ''

    # ---- start: walk to the trolley ----------------------------------------------------------------
    def start(self):
        bot = self.bot
        for i in range(RIDERS):
            if self.claim(('trolley', bot.zoneId, i)):
                break
        else:
            return False
        t = bot.view.first('DistributedTrolley')
        self.trolleyId = t.doId
        place = _trolleyPlace(bot.area)
        self.place = place
        if not bot.walkTo(bot.area.placeNode(place), RUN_SPEED if getattr(self, 'hurry', False) else tripSpeed()[0]):
            return False
        self.phase, self.until = 'walk', globalClock.getRealTime() + 90.0
        return True

    def trolley(self):
        v = self.director.viewOf(self.trolleyZone)
        return v.objects.get(self.trolleyId) if v is not None else None

    def seats(self, t):
        return [t.get('fillSlot%d' % i, (0,))[0] for i in range(4)]

    def realNearTrolley(self, t):
        seated = set(self.seats(t))
        from panda3d.core import Point3 as P3
        at = P3(*self.place['pos'])       # the trolley DO has no position field: its station on the map
        return [o for o in self.director.realPlayersNear(self.trolleyZone, at, REAL_NEAR) if o.doId not in seated]

    def step(self, now):
        try:
            return getattr(self, 'step_' + self.phase)(now)
        except Exception:
            self.director.error('trolley %s' % self.phase, traceback.format_exc())
            return self.bail('error in %s' % self.phase)

    def bail(self, why):
        """Only before the ride: give up quietly (never once seated)."""
        self.why = why
        self.bot._trolleyAgain = globalClock.getRealTime() + random.uniform(60.0, 180.0)
        return False

    def step_walk(self, now):
        if self.bot.path:
            if now > self.until:
                return self.bail('walk timeout')
            return True
        self.bot.faceTo(self.place['pos'])
        self.phase, self.until = 'wait', now + random.uniform(60.0, 90.0)
        self.boardAt = now + random.uniform(0.5, 3.0)
        self.patience = now + random.uniform(40.0, 70.0)
        return True

    def step_wait(self, now):
        t = self.trolley()
        if t is None or now > self.until:
            return self.bail('no trolley')
        state = t.get('setState', ('?',))[0]
        seats = self.seats(t)
        free = seats.count(0)
        if getattr(self, 'mirror', None) in seats:
            self.boardAt = min(self.boardAt, now)       # P9: the toon it follows is seated: get on now
        if state not in ('waitEmpty', 'waitCountdown') or free == 0 or now < self.boardAt:
            return True
        self.recruit(now)
        if state == 'waitEmpty' and not self.realNearTrolley(t):
            # kids wait for their friends: the first one gets on once another rider is here
            # (or it got bored waiting), so the 10 s countdown fills the trolley
            # only the ones already standing here count (a friend still running over would miss the
            # 10 s countdown the first boarder starts)
            here = [b for b in self.riders() if getattr(b.activity, 'phase', None) == 'wait']
            if len(here) < getattr(self, 'group', 2) and now < getattr(self, 'patience', 0.0):
                return True
        if self.realNearTrolley(t) and free <= 1:
            return True           # leave the last seat for the real player walking up
        self.bot.send('requestBoard', [], doId=self.trolleyId, className='DistributedTrolley')
        self.interruptible = False
        self.phase, self.until = 'board', now + 4.0
        return True

    def riders(self):
        d = self.director
        return [d.claims[k] for k in [('trolley', self.trolleyZone, i) for i in range(RIDERS)] if d.claims.get(k) is not None]

    def recruit(self, now):
        """A kid at the trolley calls its friends over: now and then a free toon nearby comes along,
        until the group this kid wants (2-4) is there - trolleys leave full, like on a busy server."""
        if now < getattr(self, 'nextRecruit', 0.0):
            return
        self.nextRecruit = now + random.uniform(1.5, 3.0)
        if not hasattr(self, 'group'):
            # a slammed server: most trolleys leave with 3-4 (P6b)
            self.group = random.choice((3, 4, 4, 4, 4))
        t = self.trolley()
        counting = t is not None and t.get('setState', ('?',))[0] == 'waitCountdown'
        if len(self.riders()) >= self.group and not counting:
            return
        if counting and (t is None or self.seats(t).count(0) <= len(
                [b for b in self.riders() if getattr(b.activity, 'phase', None) in ('walk', 'wait', 'board')])):
            return
        me = self.bot
        # while it counts down only a kid close enough to run over in time comes along
        reach = 70.0 if counting else 200.0
        cands = [b for b in self.director.bots.values()
                 if b is not me and b.state == 'present' and b.travel is None and b.zoneId == self.trolleyZone
                 and b.area is me.area and not b.pinned
                 and (b.activity is None or (b.activity.interruptible and b.activity.name in ('stroll', 'hangout', 'playground', 'wander', 'idle')))
                 and globalClock.getRealTime() >= getattr(b, '_trolleyAgain', 0.0)
                 and (b.pos - me.pos).length() < reach and Trolley.canRun(b)]
        if cands:
            b = min(cands, key=lambda c: (c.pos - me.pos).length() + random.uniform(0, 30))
            a = Trolley(b)
            a.hurry = counting            # runs to catch the counting-down trolley
            b.startActivity(a)

    def step_board(self, now):
        if self.seat is not None:
            self.phase = 'seated'
            self.boardedAt = now
            STATS['boarded'] += 1
            return True
        if now > self.until:
            self.interruptible = True
            self.phase, self.until = 'wait', now + 20.0
            self.boardAt = now + random.uniform(1.0, 3.0)
        return True

    def step_seated(self, now):
        self.interruptible = False
        t = self.trolley()
        if t is None:
            return True
        seats = self.seats(t)
        state = t.get('setState', ('?',))[0]
        if state in ('waitEmpty', 'waitCountdown') and seats.count(0) == 0 and self.realNearTrolley(t):
            # he wants on and it is full of bots: the last bot to board hops off
            bots = self.director.bots
            mine = [(getattr(bots.get(a).activity, 'boardedAt', 0.0), a) for a in seats
                    if a in bots and isinstance(bots.get(a).activity, Trolley)]
            if mine and max(mine)[1] == self.bot.avId and self.claim(('trolley-yield', self.trolleyZone)):
                self.bot.send('requestExit', [], doId=self.trolleyId, className='DistributedTrolley')
                STATS['yielded'] += 1
                notify.info('[TTBOTS-P6] %s hops off the full trolley in %s for a real player' % (
                    self.bot.avId, self.trolleyZone))
                self.phase, self.until = 'hopoff', now + 3.0
        elif state == 'waitCountdown' and seats.count(0) > 0 and not self.realNearTrolley(t):
            self.recruit(now)             # kids on board wave their friends over
        elif now - self.boardedAt > 60.0 and self.mgZone is None:
            STATS['stuck'] += 1
            notify.warning('[TTBOTS-P6] %s seated 60 s on trolley %s with no minigame (state %s)' % (
                self.bot.avId, self.trolleyId, state))
            self.boardedAt = now
        return True

    def step_hopoff(self, now):
        if now < self.until:
            return True
        self.interruptible = True
        self.bot._trolleyAgain = now + random.uniform(60.0, 120.0)
        return False

    # ---- fields ------------------------------------------------------------------------------------
    def onField(self, obj, fieldName, args):
        if obj.doId == self.trolleyId and fieldName.startswith('fillSlot'):
            if args[0] == self.bot.avId and self.phase in ('board', 'wait'):
                # seated (even when the fillSlot came after the board wait gave up): from here nothing
                # may pull the bot away - it would never join the game and the game aborts (P6b)
                self.interruptible = False
                self.seat = int(fieldName[-1])
                if self.phase == 'wait':
                    self.phase = 'board'
            return
        if obj.doId == self.trolleyId and fieldName.startswith('emptySlot') and args[0] == self.bot.avId:
            self.seat = None
            return
        s = self.sessions.get(obj.doId)
        if s is not None:
            s.onField(fieldName, args)
            return
        if obj.className in PURCHASE and obj.doId == self.pm:
            self.pmField(obj, fieldName, args)
            return
        if self.mgView is not None and obj.doId in self.mgView.objects:
            # the game's other objects (treasures, ...) and the other toons in the game zone
            for s in list(self.sessions.values()):
                if s.state != 'closed' and s.ended is None:
                    s.onObject(obj, fieldName, args)

    def onDirect(self, fieldName, args):
        if fieldName == 'rejectBoard':
            STATS['rejected'] += 1
            if self.phase in ('board', 'wait'):
                self.interruptible = True
                self.phase, self.until = 'wait', globalClock.getRealTime() + 15.0
                self.boardAt = globalClock.getRealTime() + random.uniform(2.0, 5.0)
            return
        if fieldName == 'setMinigameZone':
            self.hop(args[0], args[1])
            return
        for s in self.sessions.values():
            if s.state != 'closed' and s.ended is None:
                s.onDirect(fieldName, args)

    # ---- into the minigame zone ------------------------------------------------------------------
    def hop(self, zoneId, mgId):
        bot = self.bot
        if self.mgZone is not None:
            return
        self.interruptible = False
        self.mgZone = zoneId
        self.seat = None
        # off the trolley: its places are free for the next group while this one plays (P6b)
        for key in [k for k in self.claims if k[0] in ('trolley', 'trolley-yield')]:
            self.director.release(key, bot)
            self.claims.remove(key)
        dep = STATS.setdefault('departures', {})
        if zoneId not in dep:
            dep[zoneId] = {'t': time.time(), 'hood': self.trolleyZone, 'riders': 0}
            for k in [k for k, v in dep.items() if time.time() - v['t'] > 3600]:
                del dep[k]
        dep[zoneId]['riders'] += 1
        STATS['rides'] += 1
        h = STATS['byHood'].setdefault(str(self.trolleyZone), 0)
        STATS['byHood'][str(self.trolleyZone)] = h + 1
        self.mgView = self.air.openView(self.air.districtId, zoneId)
        bot.path = []
        bot.relocate(zoneId)
        self.phase = 'game'
        self.fast = True
        self.lastSeen = globalClock.getRealTime() + 20.0
        notify.info('[TTBOTS-P6] %s rides the trolley in %s to minigame %s (zone %s)' % (
            bot.avId, self.trolleyZone, mgId, zoneId))

    def mine(self, obj):
        return self.bot.avId in obj.get('setParticipants', ([],))[0]

    def step_game(self, now):
        v = self.mgView
        if v is None:
            return self.home(now, 'no view')
        seen = False
        for obj in list(v.objects.values()):
            if obj.dclass.getFieldByName('setGameReady') is not None:
                if not self.mine(obj):
                    continue
                seen = True
                if obj.doId not in self.sessions:
                    rec = _record(obj, self)
                    rec['t'] = time.time()
                    if self.bot.avId not in rec['bots']:
                        rec['bots'].append(self.bot.avId)
                    rec['real'] = [a for a in rec['players'] if a not in self.director.bots]
                    self.sessions[obj.doId] = GameSession(self, obj, minigames.brainFor(obj.className), rec)
                    self.gamesPlayed += 1
                    self.pm = None
                    self.pmPhase = None
                    notify.info('[TTBOTS-P6] %s joins %s (%s) with %s' % (
                        self.bot.avId, rec['game'], obj.doId, rec['players']))
            elif obj.className in PURCHASE and self.pm is None:
                ids = obj.get('setPlayerIds', ())
                if self.bot.avId not in ids:
                    continue
                if obj.className == 'NewbiePurchaseManager':
                    if obj.get('setOwnedNewbieId', (0,))[0] != self.bot.avId:
                        continue
                elif self.bot.avId in obj.get('setNewbieIds', ([],))[0]:
                    continue          # a newbie acts on its own NewbiePurchaseManager
                self.pmOpen(obj, now)
        if self.pm is not None:
            seen = True
            if self.pm not in v.objects:
                # shut down: play again (a new game comes in this zone) or everyone left
                STATS['purchaseClosed'] += 1
                self.pm = None
                if self.pmPhase == 'exit':
                    return self.home(now, 'exit')
                self.pmPhase = None
                self.lastSeen = now + 15.0
            else:
                self.pmStep(now)
        for doId, s in list(self.sessions.items()):
            if doId in v.objects:
                seen = True
            elif s.state != 'closed' and s.exitAt is None:
                s.close()
        if seen:
            self.lastSeen = max(self.lastSeen, now)
        elif now - self.lastSeen > 45.0:
            notify.warning('[TTBOTS-P6] %s: nothing of mine in minigame zone %s for 45 s: going home' % (
                self.bot.avId, self.mgZone))
            STATS['stuck'] += 1
            return self.home(now, 'nothing left')
        return True

    def gameEnded(self, session, how):
        r = session.record
        notify.info('[TTBOTS-P6] %s: %s %s %s' % (self.bot.avId, r['game'], session.doId, how))
        if how == 'abort' and not r.get('abortLogged'):
            r['abortLogged'] = True
            STATS['aborts'].append({'game': r['game'], 'doId': session.doId, 'stage': r.get('aborted'),
                                    'players': r['players'], 'real': r.get('real'), 'time': time.strftime('%H:%M:%S')})
            notify.warning('[TTBOTS-P6] ABORT %s %s in stage %s (players %s, real %s)' % (
                r['game'], session.doId, r.get('aborted'), r['players'], r.get('real')))

    # ---- the purchase screen ---------------------------------------------------------------------------
    def pmOpen(self, obj, now):
        from toontown.bots.activities.lifekit import planPurchase
        self.pm = obj.doId
        self.pmClass = obj.className
        STATS['purchases'] += 1
        newbie = obj.className == 'NewbiePurchaseManager'
        STATS['newbie'] += 1 if newbie else 0
        ids = list(obj.get('setPlayerIds', ()))
        pts = list(obj.get('setMinigamePoints', ()))
        game = None
        for s in self.sessions.values():
            if s.record.get('ended') and not s.record.get('pmSeen'):
                game = s.record
        if game is not None:
            game['pmSeen'] = True
            game['points'] = dict((a, p) for a, p in zip(ids, pts) if a)
            notify.info('[TTBOTS-P6] POINTS %s %s hood %s bots %s real %s' % (
                game['game'], game['doId'], game['trolleyZone'],
                [game['points'].get(a) for a in game['bots']], [game['points'].get(a) for a in game.get('real', [])]))
        # a kid looks at the gags a few seconds, then picks; faster when a real player has decided
        self.pmPhase = 'browse'
        self.pmUntil = now + random.uniform(2.0, 9.0)
        self.purchase = None
        withReal = any(a and a not in self.director.bots for a in ids)
        if withReal:
            # kids like to keep playing with the other kid on the trolley
            self.playAgain = not newbie and self.gamesPlayed < 8 and random.random() < 0.85
        else:
            self.playAgain = not newbie and self.gamesPlayed < self.maxGames and random.random() < 0.6
        self.pmIds = ids

    def pmStep(self, now):
        from toontown.bots.activities.lifekit import planPurchase
        v = self.mgView
        obj = v.objects.get(self.pm)
        if obj is None:
            return
        if self.pmPhase == 'browse':
            states = list(obj.get('setPlayerStates', ()))
            realDecided = any(st in (P_PLAYAGAIN, P_EXIT) for a, st in zip(self.pmIds, states)
                              if a and a not in self.director.bots)
            if realDecided:
                self.pmUntil = min(self.pmUntil, now + random.uniform(0.5, 2.0))
            mirror = getattr(self, 'mirror', None)       # P9: a bot following him plays again when he does
            if mirror in self.pmIds:
                his = states[self.pmIds.index(mirror)] if self.pmIds.index(mirror) < len(states) else None
                if his not in (P_PLAYAGAIN, P_EXIT) and now < self.pmUntil + 90.0:
                    return
                self.playAgain = his == P_PLAYAGAIN
            if now < self.pmUntil:
                return
            plan = planPurchase(self.bot) if random.random() < 0.8 else None
            if plan is not None and plan[2] > 0:
                self.purchase = plan
                self.send(obj, 'setInventory', [plan[0], plan[1], 0])
                STATS['gagsBought'] += plan[2]
            elif plan is None:
                STATS['noBuy'] = STATS.get('noBuy', 0) + 1
            if self.playAgain and not getattr(self, 'leaveAfter', False):
                self.send(obj, 'requestPlayAgain', [])
                STATS['playAgain'] += 1
                self.pmPhase = 'playAgain'
            else:
                self.send(obj, 'requestExit', [])
                STATS['exits'] += 1
                self.sendDone(obj)           # the client's Exit button reports right away
                self.pmPhase = 'exit'
                self.pmUntil = now + random.uniform(0.8, 1.5)
        elif self.pmPhase == 'exit' and now >= self.pmUntil:
            self.pmPhase = 'gone'
            self.home(now, 'exit')

    def pmField(self, obj, fieldName, args):
        if fieldName == 'setPurchaseExit' and self.pmPhase in ('browse', 'playAgain'):
            if self.pmPhase == 'browse':
                # the 120 s timer ran out before we picked (never: we pick in < 10 s): the client reports
                self.pmPhase = 'exit' if not self.playAgain else 'playAgain'
            self.sendDone(obj)
            if self.pmPhase == 'playAgain':
                self.pmPhase = 'waitNext'

    def sendDone(self, obj):
        f = self.bot.ownFields
        if self.purchase is not None:
            blob, money = self.purchase[0], self.purchase[1]
        else:
            blob = (f.get('setInventory') or (b'',))[0]
            money = (f.get('setMoney') or (0,))[0]
        self.send(obj, 'setInventory', [blob, money, 1])

    def send(self, obj, fieldName, args):
        self.bot.send(fieldName, args, doId=obj.doId, className=obj.className)

    # ---- home ---------------------------------------------------------------------------------------
    def home(self, now, why):
        """The client's purchase-done: teleportIn to the playground (through the quiet zone)."""
        bot, a = self.bot, self.area
        for s in self.sessions.values():
            s.close()
        self.sessions = {}
        if self.mgView is not None:
            self.air.closeView(self.mgView)
            self.mgView = None
        if a is None:
            return False
        node = self.director.spawnNode(a, bot)
        bot.relocate(QUIET)
        bot.node = node
        bot.pos = Point3(*a.wm.pos(node))
        bot.h = random.uniform(-180, 180)
        bot.broadcastNow()
        bot.setAnim('TeleportIn', force=True)
        bot.relocate(a.zoneOfNode(node))
        STATS['returned'] += 1
        self.why = why
        self.phase, self.until = 'tpin', now + TELEPORT_IN_TIME
        self.fast = False
        return True

    def step_tpin(self, now):
        if now < self.until:
            return True
        self.bot.setAnim('neutral')
        self.interruptible = True
        self.mgZone = None
        self.bot.arrivedAt = now
        self.bot._trolleyAgain = now + random.uniform(90.0, 300.0)
        return False

    def releaseFor(self, why):
        """Sweep fix (P9 whisper): a friend whispered an order. Not on the car yet, or on it while it still
        waits: hop off now ('now', the activity ends). The car is leaving or a game is on: finish this
        game, Exit at the purchase screen instead of Play Again ('after')."""
        if self.mgZone is not None or self.mgView is not None or self.phase in ('tpin', 'hopoff'):
            self.leaveAfter = True
            return 'after'
        if self.seat is not None or self.phase == 'seated':
            t = self.trolley()
            if t is None or t.get('setState', ('?',))[0] not in ('waitEmpty', 'waitCountdown'):
                self.leaveAfter = True
                return 'after'
        self.bot.endActivity(why)
        return 'now'

    def stop(self, why):
        # never called mid-game by the director (not interruptible); logout / district down only
        for s in self.sessions.values():
            s.close()
        self.sessions = {}
        if self.mgView is not None:
            self.air.closeView(self.mgView)
            self.mgView = None
        if self.phase in ('board', 'seated') and why != 'done' and self.bot.state == 'present':
            self.bot.send('requestExit', [], doId=self.trolleyId, className='DistributedTrolley')
        if why not in ('done', 'replaced'):
            notify.info('[TTBOTS-P6] %s trolley activity stopped in %s: %s' % (self.bot.avId, self.phase, why))
