"""The bot DIRECTOR (TTBOTS P2): one process-wide brain for the whole bot pool.

ONE central task ('bots-tick', every TICK s) drives every bot: a bot is ticked when it is due
(BotToon.tick), and once a second the director itself runs:
  - the live Enabled switch (on/off written into <run>/bots.enabled),
  - per-area targets (TTC playground 30-50, other playgrounds 15-30, each street 4-10, cap 420 (POP); counted as SEEN (POP); a street he is on fills his view to 9-13),
  - warm-up: the area a REAL player is in, or is loading into, and its neighbours fill first,
  - arrivals/departures (BotTravel): silent only where no real player is; where one is, bots
    come and go the way real toons do (teleport hole, tunnel, building door),
  - rotation: the same regulars move between their home hood's playground and streets and the
    shared places (Acorn Acres, Speedway, Golf) over time, log out and come back,
  - free bots get an activity from the registry (toontown/bots/activities),
  - the status file (<run>/bots-status.txt + .json).

Views are SHARED and refcounted (BotAIRepository.openView): one per location zone, not per bot.
Real players are counted from those views: a DistributedToon that is not one of our bots is a
client's toon (bots never count).
"""
import json
import math
import os
import random
import time
import traceback

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.ClockDelta import globalClockDelta
from direct.showbase.DirectObject import DirectObject
from direct.task.Task import Task
from panda3d.core import Point3

from otp.distributed.OtpDoGlobals import OTP_ZONE_ID_MANAGEMENT, OTP_ZONE_ID_DISTRICTS_STATS
from toontown.bots import activities
from toontown.bots.BotPool import BotPool
from toontown.bots.BotToon import BotToon
from toontown.bots.BotTravel import BotTravel, QUIET
from toontown.bots.BotWorld import World, SHARED, ELIGIBLE, AREA_TARGETS
from toontown.bots import BotVis
from toontown.bots import progress_pool
from toontown.bots import halloween
from toontown.bots import space

TICK = 0.05              # smoothness: a watched moving bot is ticked every pass, sends every ~0.1 s
SYNC_EVERY = 30.0        # P10: s between clock resync bursts
SYNC_SAMPLES = 6         # requests per burst (one a second); the smallest round trip wins
SYNC_MAX_RTT = 0.3       # a burst whose best round trip is worse (an AI stall) is not applied
PEER_WINDOW = 16         # a player's last stamps looked at (the least delayed one counts)
PEER_BEHIND = 0.08       # s: a player's clock this far behind ours gets a suggestResync
PEER_AHEAD = 0.04        # s: ... or this far ahead
PEER_EVERY = 20.0        # s between suggestions to one player (the client ignores peers within 10 s of a sync)
# (min, max) toons per area; street = the whole street (all its visgroups)
DEFAULT_TARGETS = {'ttc': (34, 50),   # sweep fix: 34 so a kid's view stays >= 30 while bots come and go
                   'playground': (17, 30),   # POP: counted as SEEN (17 so a kid's view stays >= 15)
                   'street': (4, 10),        # POP: an unwatched street runs lean; a watched one fills his view
                   'coghq': (2, 5)}                                               # P8b: a handful per HQ place
VISIBLE_KINDS = ('playground', 'street')  # POP: these count only bots a client there would see (not in shops,
                                          # Toon HQ, trolley games, a door's quiet zone); Cog HQ keeps P8b's count
WATCHED_VIEW = (9, 13)   # POP: bots in the visgroups a real player on a street has open (law: 8-14 in view)
STREET_HARD_MAX = 26     # POP: a watched street never holds more than this in all (view counting has no max)
HIDDEN_SHARE = 0.12      # POP: floor of the pool kept for bots out of sight (shops, trolley games) when rolling
FILL_SLACK = 2           # POP: toons an unwatched place may be short before the director refills it
VIEW_CREDIT = 3          # POP: toons on their way into his view counted as in it, at most (the rest must show up)
LEAN = 0.6               # POP: an unwatched area may lend bots to a watched one down to this share of its min
EXTERIOR_DOOR_TYPES = (1, 3, 5, 7, 9, 11, 13)
PLAYING_ON_AFTER = 10.0     # PROGRESSION (owner 1a): s of a real player online before the bots level up
PLAYING_OFF_AFTER = 120.0   # s of nobody before they stop starting progress activities
STYLE_RANK = {'grinder': 0, 'regular': 1, 'casual': 2}     # logins prefer grinders, then regulars
PLACE_KINDS = ('fountain', 'gazebo', 'trolley', 'gagshop', 'hq', 'fishing', 'clothes', 'petshop', 'statue',
               'toonhall', 'bank', 'library', 'school', 'partygate', 'gametable', 'picnictable', 'golfkart',
               'racingpad', 'viewingpad', 'leaderboard', 'kartshop')


class BotDirector(DirectObject):
    notify = DirectNotifyGlobal.directNotify.newCategory('BotDirector')

    def __init__(self, air):
        DirectObject.__init__(self)
        self.air = air
        self.pool = BotPool(air)
        self.world = None
        self.bots = {}               # avId -> BotToon (every bot with a DB toon)
        self.views = {}              # zoneId -> BotZoneView (shared)
        self.mgmtView = None
        self.statsView = None        # PROGRESSION: the district's ToontownDistrictStats (avatar count)
        self.playing = False         # PROGRESSION (owner 1a): a real player is online -> bots may level up
        self.playingWhy = 'start'
        self.realOnline = 0
        self._paceOnSince = None
        self._paceOffSince = None
        self.lastProgress = 0.0
        self.halloween = halloween.Halloween(self)     # HALLOWEEN (owner 10-01): costumes + Halloween SpeedChat
        self.lastReset = 0.0
        self.players = {}            # real toon avId -> location zone
        self.claims = {}
        self.warm = {}               # areaId -> until (priority fill)
        self.enabled = True
        self.clockSynced = False
        self.syncStart = 0.0
        self.lastDirector = 0.0
        self.lastStatus = 0.0
        self.lastRoll = 0.0
        self.lastFit = 0.0
        self.lastResync = 0.0
        self.lastChurn = 0.0
        self.started = time.time()
        self.stats = {'in': {}, 'out': {}}
        self.errors = 0
        self.errorLog = []
        self.tickMs = []
        self.sent = 0
        self.runDir = config.GetString('bot-run-dir', '../run')
        self.cap = config.GetInt('bot-cap', 420)      # P8c: 300 + 60 Cog HQ regulars; POP: +60 for bots out of sight
        self.loginsPerSec = config.GetInt('bot-logins-per-sec', 5)
        self.movesPerSec = config.GetInt('bot-moves-per-sec', 3)
        self.rotateEvery = config.GetFloat('bot-rotate-every', 2.0)     # s between rotation moves (whole pool)
        self.doorDOs = {}            # (areaId, block, doorIndex) -> doId
        self.enabledMtime = None
        self.streetLog = None        # STREET LIFE sampler: [(t, avId, zoneId, msgId)] while bots-street.on exists
        from toontown.bots.BotLoad import LoadGuard      # P10b: login robustness + unwatched-zone traffic
        self.load = LoadGuard(self)

    # ---- life --------------------------------------------------------------------------------
    def start(self):
        activities.loadAll()
        self.notify.info('[TTBOTS] activities: %s' % [c.name for c in activities.REGISTRY])
        self.pool.load()
        self.world = World()
        self.accept('botai-district-up', self.__districtUp)
        self.accept('botai-district-down', self.__districtDown)
        self.accept('botview-enter', self.__viewEnter)
        self.accept('botview-exit', self.__viewExit)
        self.accept('botview-field', self.__viewField)
        try:                                                        # P10: per-bot per-round battle delivery
            from toontown.bots.BotViewAudit import BattleDelivery
            self.delivery = BattleDelivery(self.runDir)
            self.air.stampHook = self.peerStamp
        except ImportError:
            self.delivery = None
        taskMgr.doMethodLater(TICK, self.__tick, 'bots-tick')

    def __districtUp(self, districtId):
        self.mgmtView = self.air.openView(districtId, OTP_ZONE_ID_MANAGEMENT)
        self.statsView = self.air.openView(districtId, OTP_ZONE_ID_DISTRICTS_STATS)     # PROGRESSION: pace switch
        zones = [QUIET]
        for a in self.world.areas.values():
            zones += a.zones
        if hasattr(self.air, 'openViews'):          # P10b: batched zone queries (one relay per 100 zones)
            for z, v in zip(zones, self.air.openViews(districtId, zones)):
                self.views[z] = v
        else:
            for z in zones:
                self.views[z] = self.air.openView(districtId, z)
        self.notify.info('[TTBOTS] director: district %s, %d areas, %d shared zone views' % (
            districtId, len(self.world.areas), len(self.views)))
        for spec in self.pool.specs:
            if self.pool.isReady(spec):
                self.__addBot(spec)
        self.load.sweep(list(self.bots.values()))           # P10b: toons left live by an earlier process
        for a in self.world.areas.values():                  # POP: street visgroup vis lists (a player's view)
            if a.kind == 'street':
                BotVis.loadStreet(a.id)
        self.notify.info('[TTBOTS] POP: street views from the DNA: %d visgroups' % len(BotVis._VIS))
        try:                                                  # PROGRESSION: fresh toons before anyone logs in
            progress_pool.resetOffline(self)
        except Exception:
            self.error('progress reset', traceback.format_exc())
        self.pool.onReady = self.__addBot
        self.pool.start()
        self.__rollTargets()

    def __districtDown(self):
        self.pool.stop()
        for bot in self.bots.values():
            bot.gone('district down')
        for v in self.views.values():
            self.air.closeView(v)
        self.views = {}
        self.players = {}
        if self.mgmtView:
            self.air.closeView(self.mgmtView)
            self.mgmtView = None
        if self.statsView:
            self.air.closeView(self.statsView)
            self.statsView = None

    def __addBot(self, spec):
        e = self.pool.entry(spec['key'])
        if e and e.get('avId') and e['avId'] not in self.bots:
            self.bots[e['avId']] = BotToon(self, spec, e)
            self.air.botSenders.add(self.air.botChannel(e['accountId'], e['avId']))

    def viewOf(self, zoneId):
        return self.views.get(zoneId)

    # ---- clock: the main AI's network time, asked the way a client asks (P0) ------------------
    # P10: one delta serves every bot, so it must be good. Each request carries its own context and send
    # time; a resync is a burst of SYNC_SAMPLES requests (one a second) and only the smallest round trip is
    # applied: its midpoint is the least skewed by the main AI's queue and this process's frame (a single
    # sample is skewed by half its round trip's asymmetry). Replies nobody is waiting for are ignored.
    def syncClock(self, bot):
        tm = self.mgmtView.first('TimeManager') if self.mgmtView else None
        if tm is None:
            return False
        self.syncCtx = (getattr(self, 'syncCtx', 0) + 1) & 0xFF
        self.syncSent = getattr(self, 'syncSent', {})
        self.syncSent[self.syncCtx] = globalClock.getRealTime()
        self.air.sendAsBot(bot.channel, tm.doId, 'TimeManager', 'requestServerTime', [self.syncCtx])
        return True

    def gotServerTime(self, bot, context, timestamp):
        end = globalClock.getRealTime()
        start = getattr(self, 'syncSent', {}).pop(context, None)
        if start is None or end - start > 5.0:
            return
        rtt = end - start
        if not self.clockSynced:
            # the first answer lets bots start broadcasting; the burst then refines it
            globalClockDelta.resynchronize((start + end) / 2.0, timestamp, rtt / 2.0)
            self.notify.info('[TTBOTS] clock synced to the main AI (round trip %.1f ms)' % (rtt * 1000))
            self.clockSynced = True
        best = getattr(self, 'syncBest', None)
        if best is None or rtt < best[0]:
            self.syncBest = (rtt, (start + end) / 2.0, timestamp)

    def __syncStep(self, now):
        """1 Hz from __direct: a burst of SYNC_SAMPLES requests every SYNC_EVERY s, then apply the best."""
        n = getattr(self, 'syncN', 0)
        if n == 0 and self.clockSynced and now - self.lastResync < SYNC_EVERY:
            return
        if n < SYNC_SAMPLES:
            anyBot = next((b for b in self.bots.values() if b.state == 'present'), None)
            if anyBot is not None and self.syncClock(anyBot):
                if n == 0:
                    self.syncBest = None
                self.syncN = n + 1
            return
        self.syncN = 0
        self.lastResync = now
        best = getattr(self, 'syncBest', None)
        if best is None:
            return
        rtt, mid, ts = best
        if rtt > SYNC_MAX_RTT:
            self.notify.info('[TTBOTS] clock resync skipped: best round trip %.1f ms' % (rtt * 1000))
            return
        old = globalClockDelta.getDelta()
        # trustNew: the best of a burst replaces the old window outright (no narrowing onto a stale one)
        globalClockDelta.resynchronize(mid, ts, rtt / 2.0, trustNew=1)
        self.clockStats = {'rtt_ms': round(rtt * 1000, 1), 'shift_ms': round((globalClockDelta.getDelta() - old) * 1000, 1)}
        self.notify.info('[TTBOTS] clock resync: best round trip %.1f ms of %d, delta moved %.1f ms' % (
            rtt * 1000, SYNC_SAMPLES, self.clockStats['shift_ms']))

    def answerResync(self, bot, args):
        """A client saw one of my timestamps too far off and suggested a resync (DistributedSmoothNode.
        suggestResync, ownrecv). Answer as a real client does (returnResync on the asker's avatar) with
        this process's server-time estimate: the asker narrows onto it, or asks the AI again."""
        if not self.clockSynced:
            return
        avId, tsA, tsB = args[0], args[1], args[2]
        dclass = self.air.dclassOf.get(avId)
        if dclass is None or avId in self.bots:
            return
        realTime = globalClock.getRealTime()
        serverTime = realTime - globalClockDelta.getDelta()
        sec = math.floor(serverTime)
        unc = min(600.0, globalClockDelta.getUncertainty() or 0.05)
        self.air.sendAsBot(bot.channel, avId, dclass.getName(), 'returnResync',
                           [bot.avId, tsB, int(sec), int((serverTime - sec) * 10000.0), unc])
        self.resyncAnswers = getattr(self, 'resyncAnswers', 0) + 1

    def peerStamp(self, view, obj, ts):
        """P10: a real player's position timestamp, seen by the bots in his zone. A client syncs its clock
        once at login, often in a loading hitch (round trips of 0.4-4 s measured), and stays that far off:
        bot toons then run 0.2-0.9 s 'in the future' on his screen. A real client that sees a peer's stamp
        off asks that peer to resync (DistributedSmoothNode.suggestResync); a bot does the same, on the
        least delayed of the player's last PEER_WINDOW stamps, and the client narrows its clock onto the
        bot's (or asks the AI again). Once per PEER_EVERY s per player."""
        if obj.doId in self.bots or obj.className != 'DistributedToon' or not self.clockSynced:
            return
        now = globalClock.getRealTime()
        off = globalClockDelta.networkToLocalTime(ts, now, bits=16) - now
        pc = self.__dict__.setdefault('peerClock', {})
        rec = pc.setdefault(obj.doId, {'offs': [], 'last': 0.0})
        rec['offs'] = (rec['offs'] + [off])[-PEER_WINDOW:]
        if len(rec['offs']) < PEER_WINDOW // 2 or now - rec['last'] < PEER_EVERY:
            return
        best = max(rec['offs'])               # transit and our frame only ever make a stamp look older
        if -PEER_BEHIND <= best <= PEER_AHEAD:
            return
        unc = globalClockDelta.getUncertainty()
        if unc is None or unc > 0.1:
            return
        bot = next((b for b in self.bots.values() if b.zoneId == view.zoneId and b.state == 'present'), None)
        if bot is None:
            return
        rec['last'] = now
        rec['offs'] = []
        serverTime = now - globalClockDelta.getDelta()
        sec = math.floor(serverTime)
        self.air.sendAsBot(bot.channel, obj.doId, obj.className, 'suggestResync',
                           [bot.avId, ts, globalClockDelta.localToNetworkTime(now, bits=16), int(sec),
                            int((serverTime - sec) * 10000.0), unc])
        self.peerSuggests = getattr(self, 'peerSuggests', 0) + 1
        self.notify.info('[TTBOTS] clock: player %s stamps run %.0f ms %s ours: suggested a resync (bot %s)' % (
            obj.doId, abs(best) * 1000, 'behind' if best < 0 else 'ahead of', bot.avId))

    # ---- bookkeeping used by bots / travel / activities --------------------------------------
    def setArea(self, bot, area):
        bot.area = area

    def count(self, direction, method):
        d = self.stats[direction]
        d[method] = d.get(method, 0) + 1

    def error(self, what, detail):
        self.errors += 1
        self.errorLog = (self.errorLog + ['%s %s: %s' % (time.strftime('%H:%M:%S'), what,
                                                           detail.strip().splitlines()[-1][:160])])[-5:]
        self.notify.warning('[TTBOTS] error in %s: %s' % (what, detail))

    def claim(self, key, bot):
        holder = self.claims.get(key)
        if holder is not None and holder is not bot and holder.state != 'offline':
            return False
        self.claims[key] = bot
        return True

    def release(self, key, bot):
        if self.claims.get(key) is bot:
            del self.claims[key]

    def watched(self, area):
        return bool(area is not None and area.players)

    def realPlayersIn(self, zoneId):
        return [a for a, z in self.players.items() if z == zoneId]

    def realPlayersNear(self, zoneId, pos, radius):
        v = self.views.get(zoneId)
        out = []
        if v is None:
            return out
        for avId in self.realPlayersIn(zoneId):
            o = v.objects.get(avId)
            if o is not None and (o.pos - pos).length() <= radius:
                out.append(o)
        return out

    def canWalkTo(self, bot, place):
        a = bot.area
        k = a.placeNode(place)
        if k is None or bot.node is None:
            return False
        a.wm
        return a._comp[k] == a._comp[bot.node]

    def spawnNode(self, area, bot=None):
        """Where a toon shows up (personal space: a node no toon stands on or walks to, space.freeNode)."""
        k = self.__spawnPick(area)
        try:
            space.STATS['arrivals'] += 1
            return space.freeNode(self, area, k, bot)
        except Exception:
            self.error('space spawn', traceback.format_exc())
            return k

    def __spawnPick(self, area):
        """Near a landmark half the time (fountain, gazebo, shops...), else anywhere on the ground."""
        if area.kind == 'street' and area.players and random.random() < 0.75:
            # P7: on the street a real player walks, toons show up where he can see them
            for avId in area.players:
                v = self.views.get(self.players.get(avId))
                o = v.objects.get(avId) if v is not None else None
                if o is not None:
                    k = area.nodeNear(o.pos[0], o.pos[1], random.uniform(40.0, 110.0))
                    if k is not None:
                        return k
        if area.kind == 'playground' and random.random() < 0.5:
            places = [p for p in area.wm.places() if p['kind'] in PLACE_KINDS and area.placeNode(p) is not None]
            if places:
                p = random.choice(places)
                k = area.nodeNear(p['pos'][0], p['pos'][1], 22.0)
                if k is not None:
                    return k
        return area.randomNode()

    def pickDoor(self, area, near=None):
        """A building door with a live exterior door object: (place, doId)."""
        found = []
        for p in area.doors:
            ex = p['extra']
            doId = self.doorDOs.get((area.id, ex['block'], ex.get('door', 0)))
            if doId is not None:
                found.append((p, doId))
        if not found:
            return None
        if near is not None:
            found.sort(key=lambda f: (f[0]['pos'][0] - near[0]) ** 2 + (f[0]['pos'][1] - near[1]) ** 2)
            found = found[:3]
        return random.choice(found)

    def arrived(self, bot):
        bot.freeSince = globalClock.getRealTime()

    # ---- views: who is where ----------------------------------------------------------------
    def __viewEnter(self, view, obj):
        bot = self.bots.get(obj.doId)
        if bot is not None:
            if view.zoneId == bot.zoneId and bot.state == 'activating':
                bot.onSeen(view, obj)
            return
        cls = obj.className
        if cls == 'DistributedToon':
            self.__playerAt(obj.doId, view.zoneId)
        elif cls == 'DistributedDoor':
            a = self.world.zoneToArea.get(view.zoneId)
            zb = obj.get('setZoneIdAndBlock')
            if a is not None and zb and obj.get('setDoorType', (0,))[0] in EXTERIOR_DOOR_TYPES:
                self.doorDOs[(a.id, zb[1], obj.get('setDoorIndex', (0,))[0])] = obj.doId

    def __viewExit(self, view, obj, deleted=False):
        bot = self.bots.get(obj.doId)
        if bot is not None:
            if deleted and bot.state != 'offline':
                self.notify.warning('[TTBOTS] %s %s was deleted by the server' % (bot.avId, bot.name))
                bot.gone('deleted by the server')
            return
        if obj.className == 'DistributedToon' and self.players.get(obj.doId) == view.zoneId:
            self.__playerAt(obj.doId, None)

    def __playerAt(self, avId, zoneId):
        old = self.players.get(avId)
        oldArea = self.world.zoneToArea.get(old) if old is not None else None
        newArea = self.world.zoneToArea.get(zoneId) if zoneId is not None else None
        if zoneId is None:
            self.players.pop(avId, None)
        else:
            self.players[avId] = zoneId
        if oldArea is not None and oldArea is not newArea:
            oldArea.players.discard(avId)
        if newArea is not None:
            if avId not in newArea.players:
                newArea.players.add(avId)
                self.notify.info('[TTBOTS] real player %s is in %s (zone %s): %d bots there' % (
                    avId, newArea.name, zoneId, self.__countIn(newArea)))
                self.__warm(newArea)
        if zoneId == QUIET and old is None:
            # loading in (a login or a teleport): warm the hood he was last in, from the DB
            try:
                self.air.dbInterface.queryObject(self.air.dbId, avId, lambda dclass, fields:
                                                 self.__lastHood(avId, fields),
                                                 dclass=self.air.dclassesByName['DistributedToon'],
                                                 fieldNames=('setLastHood',))
            except Exception:
                self.error('lastHood query', traceback.format_exc())

    def __lastHood(self, avId, fields):
        hood = (fields or {}).get('setLastHood', (0,))[0]
        a = self.world.areas.get(hood)
        if a is not None:
            self.__warm(a)

    def __warm(self, area):
        until = globalClock.getRealTime() + 180.0
        self.warm[area.id] = until
        for nb in self.world.neighbours(area):
            self.warm.setdefault(nb.id, until - 60.0)

    def __countIn(self, area):
        return sum(1 for b in self.bots.values() if self.__countArea(b) is area)

    def viewZones(self, area):
        """POP: the zones the real players in a street have open on their clients (their visgroups' DNA vis
        lists); a playground: the playground zone. Empty when nobody real is there."""
        out = set()
        for avId in area.players:
            out |= BotVis.visibleFrom(self.players.get(avId))
        return out

    def __visibleCounts(self, counts):
        """POP (after the sweep fix's TTC-only version): a playground or street target is what a kid there
        SEES, so it counts only bots standing in the area's own zones (plus those on their way in); bots of
        that area riding a trolley game, in a shop / Toon HQ / street building, or in a door's quiet zone are
        not counted, so the director keeps the place full while they are away. A street a real player walks
        counts only the bots in the visgroups his client has open (his view), against WATCHED_VIEW.
        Also fills self.areaAll (every bot belonging to an area, seen or not) and self.hidden."""
        views = {}
        for a in list(counts):
            if a is not None and a.kind in VISIBLE_KINDS:
                counts[a] = 0
                if a.kind == 'street' and a.players:
                    views[a] = self.viewZones(a)
        allIn, hidden, hiddenBy = {}, 0, {}
        recalled, now, credit = getattr(self, 'recalled', {}), globalClock.getRealTime(), {}
        for b in self.bots.values():
            if b.state == 'offline':
                continue
            if b.travel is not None:
                a = b.travel.dest
                if a is not None:
                    allIn[a] = allIn.get(a, 0) + 1
                    if a.kind in VISIBLE_KINDS and (a not in views or credit.get(a, 0) < VIEW_CREDIT):
                        counts[a] = counts.get(a, 0) + 1       # on its way in: it will show up there
                        if a in views:                          # POP: his view credits only a few (a tunnel
                            credit[a] = credit.get(a, 0) + 1    # arrival can come out far from him)
                continue
            a = b.area
            if a is None:
                continue
            allIn[a] = allIn.get(a, 0) + 1
            if a.kind not in VISIBLE_KINDS:
                continue
            v = views.get(a)
            if v is not None and recalled.get(b.avId, 0) > now and b.zoneId in a.zones and b.zoneId not in v:
                if credit.get(a, 0) < VIEW_CREDIT:
                    counts[a] = counts.get(a, 0) + 1   # POP: on its way back into his view
                    credit[a] = credit.get(a, 0) + 1
            elif (b.zoneId in v) if v is not None else (b.zoneId in a.zones):
                counts[a] = counts.get(a, 0) + 1
            elif b.zoneId not in a.zones:
                hidden += 1
                k = b.activity.name if b.activity is not None else 'none'
                hiddenBy[k] = hiddenBy.get(k, 0) + 1
        self.areaAll = allIn
        self.hidden = hidden
        self.hiddenBy = hiddenBy
        self.viewed = views

    @staticmethod
    def __countArea(bot):
        if bot.state == 'offline':
            return None
        if bot.travel is not None:
            return bot.travel.dest
        return bot.area

    def __viewField(self, view, obj, fieldName, args, sender):
        # only the AI's and real players' broadcasts get here (bots' own echoes are dropped early)
        if fieldName == 'setSC' and obj.className == 'DistributedToon' and obj.doId not in self.bots:
            self.__heard(view.zoneId, obj, args[0])
            return
        for bot in self.bots.values():
            if bot.zoneId == view.zoneId and bot.activity is not None and bot.state == 'present':
                try:
                    bot.activity.onField(obj, fieldName, args)
                except Exception:
                    self.error('%s onField' % bot.activity.name, traceback.format_exc())

    def __heard(self, zoneId, speaker, msgId):
        near = [b for b in self.bots.values() if b.zoneId == zoneId and b.state == 'present' and b.travel is None]
        near.sort(key=lambda b: (b.pos - speaker.pos).length())
        for bot in near:
            if bot.heard(speaker, msgId):
                return
        for bot in near:
            if bot.activity is not None and not bot.activity.interruptible:
                continue
            for cls in activities.REGISTRY:
                act = cls.trigger(bot, speaker, msgId)
                if act is not None:
                    bot.startActivity(act)
                    return

    # ---- the ONE tick ---------------------------------------------------------------------
    def __tick(self, task):
        t0 = globalClock.getRealTime()
        now = t0
        for bot in list(self.bots.values()):
            if bot.state != 'offline' and bot.nextTick <= now:
                try:
                    # smoothness: each bot's position is computed for and stamped with ITS moment in the loop (a
                    # 420-bot pass takes 10-140 ms; one loop-start stamp made late bots' samples look that much
                    # older, and a client's clock-skew averaging then pulled the toon back ~1 ft at run speed)
                    bot.tick(globalClock.getRealTime())
                except Exception:
                    self.error('bot tick', traceback.format_exc())
        if now - self.lastDirector >= 1.0 and self.air.districtId and self.world is not None:
            self.lastDirector = now
            try:
                self.__direct(now)
            except Exception:
                self.error('director', traceback.format_exc())
        self.tickMs = (self.tickMs + [(globalClock.getRealTime() - t0) * 1000.0])[-600:]
        return Task.again

    def __readSwitch(self):
        path = os.path.join(self.runDir, 'bots.enabled')
        try:
            m = os.path.getmtime(path)
        except OSError:
            return
        if m == self.enabledMtime:
            return
        self.enabledMtime = m
        with open(path) as f:
            on = f.read().strip().lower() not in ('off', '0', 'false', 'no')
        if on != self.enabled:
            self.notify.info('[TTBOTS] director: Enabled switched %s' % ('ON' if on else 'OFF'))
        self.enabled = on

    def eligible(self, bot, area):
        """May this bot stand here? (Where the director PUTS it is placeable().)"""
        rule = ELIGIBLE.get(area.kind)          # P8b: Cog HQ places have their own rule
        if rule is not None:
            return not bot.pinned and rule(bot, area)
        if bot.pinned:
            return area.id == bot.home
        if bot.crew:
            # PROGRESSION: the crew lives in Cog HQ; its home playground only to heal / restock (the Cog HQ code
            # sends it there and fetches it back), never a street
            return area.kind == 'playground' and area.id == bot.home
        # PROGRESSION: a progressing toon goes where it has got to: its tier's hood, hoods it can teleport to, TTC
        return area.id in SHARED or area.hood in bot.reach()

    def placeable(self, bot, area):
        """PROGRESSION: where the director may send a bot (fill, spare, rotate): the crew only to Cog HQ places."""
        if bot.crew and area.kind != 'coghq':
            return False
        return self.eligible(bot, area)

    @staticmethod
    def anchored(bot):
        """PROGRESSION: an anchored activity (a toon waiting at a building door for help) is never pulled."""
        return bot.activity is not None and getattr(bot.activity, 'anchored', False)

    def __pace(self, now):
        """PROGRESSION (owner 1a): playing = a real player on the district: the district's avatar count minus our
        toons on the AI, or a real toon seen in a view. ON after 10 s of one, OFF after 120 s of none."""
        seen = len(self.players)
        count = None
        if self.statsView is not None:
            o = self.statsView.first('ToontownDistrictStats')
            if o is not None:
                count = (o.get('setAvatarCount') or (None,))[0]
        onAi = sum(1 for b in self.bots.values() if b.state in ('activating', 'present', 'leaving'))
        byCount = max(0, count - onAi) if count is not None else 0
        self.realOnline = max(seen, byCount)
        self.paceCounts = (count, onAi, seen)
        if self.realOnline > 0:
            self._paceOffSince = None
            if self._paceOnSince is None:
                self._paceOnSince = now
            if not self.playing and now - self._paceOnSince >= PLAYING_ON_AFTER:
                self.playing = True
                self.playingWhy = 'district count %s - bots on the AI %d = %d, real toons seen %d' % (
                    count, onAi, byCount, seen)
                self.notify.info('[TTBOTS] PLAYING ON: %s' % self.playingWhy)
        else:
            self._paceOnSince = None
            if self._paceOffSince is None:
                self._paceOffSince = now
            if self.playing and now - self._paceOffSince >= PLAYING_OFF_AFTER:
                self.playing = False
                self.playingWhy = 'nobody for %ds (district count %s, bots on the AI %d)' % (
                    PLAYING_OFF_AFTER, count, onAi)
                self.notify.info('[TTBOTS] PLAYING OFF: %s' % self.playingWhy)

    def __progressPass(self, now):
        """PROGRESSION: the one-time reset of bots that were online at district-up, the registry copy of each
        toon's tier / reach, and bots-progress.txt."""
        if now - self.lastReset >= 30.0:
            self.lastReset = now
            try:
                progress_pool.resetOffline(self)
            except Exception:
                self.error('progress reset', traceback.format_exc())
        if now - self.lastProgress >= 60.0:
            self.lastProgress = now
            try:
                progress_pool.remember(self)
            except Exception:
                self.error('progress remember', traceback.format_exc())
            progress_pool.writeProgress(self)

    def __rollTargets(self, repick=True):
        """Every 900 s: a fresh random pick per area, fitted to the pool. POP: repick=False only re-fits the
        same picks to a new out-of-sight share (no reshuffle: every moved toon is a DB write)."""
        areas = list(self.world.areas.values())
        for a in areas:
            key = 'ttc' if a.id == 2000 else a.kind
            a.min, a.max = AREA_TARGETS.get(a.id) or DEFAULT_TARGETS[key]
            if repick or getattr(a, 'pick', None) is None or not a.min <= a.pick <= a.max:
                a.pick = random.randint(a.min, a.max)
            a.target = a.pick
        # scale so the total fits the online pool (cap): all of it online, inside every range
        cap = min(self.cap, len(self.pool.specs))
        # POP: targets count bots in sight, so keep part of the pool for the ones out of it (in shops, trolley
        # games, doors' quiet zones): the share measured now, never under HIDDEN_SHARE
        self.rollHidden = getattr(self, 'hidden', 0)
        cap -= max(int(cap * HIDDEN_SHARE), self.rollHidden + 5)
        lo, hi = sum(a.min for a in areas), sum(a.max for a in areas)
        s = sum(a.target for a in areas)
        for a in areas:
            if s > cap and s > lo:
                a.target = int(round(a.min + (a.target - a.min) * (cap - lo) / float(s - lo)))
            elif s < cap and hi > s:
                a.target = int(round(a.target + (a.max - a.target) * (cap - s) / float(hi - s)))
        for a in areas:
            a.baseTarget = a.target
        self.lastFit = globalClock.getRealTime()
        if repick:
            self.lastRoll = self.lastFit
        self.notify.info('[TTBOTS] targets: total %d (cap %d): %s' % (
            sum(a.target for a in areas), cap, ' '.join('%s=%d' % (a.id, a.target) for a in areas)))

    def __direct(self, now):
        self.__readSwitch()
        self.__syncStep(now)
        self.__pace(now)                              # PROGRESSION
        self.__progressPass(now)
        self.halloween.tick(now)
        if now - getattr(self, 'lastAudit', 0.0) >= 60.0 and self.air.districtId and hasattr(self.air, 'auditViews'):     # P10 view invariants
            self.lastAudit = now
            self.air.auditViews(self)
        if now - self.lastRoll > 900.0:
            self.__rollTargets()
        for k in [k for k, u in self.warm.items() if u < now]:
            del self.warm[k]
        counts = {a: 0 for a in self.world.areas.values()}
        for b in self.bots.values():
            a = self.__countArea(b)
            if a is not None:
                counts[a] = counts.get(a, 0) + 1
        online = sum(counts.values())
        self.__visibleCounts(counts)                 # sweep fix: a playground counts only who stands in it
        if abs(getattr(self, 'hidden', 0) - getattr(self, 'rollHidden', 0)) > 12 and now - self.lastFit > 180.0:
            self.__rollTargets(repick=False)         # POP: the share out of sight moved: re-fit the targets to the pool
        self.load.update()                          # P10b: watched zones (full-rate broadcasts)
        for a in self.world.areas.values():          # P7: the street a real player is on is busy
            if a.kind == 'street':
                base = getattr(a, 'baseTarget', a.target)
                if a.players:          # POP: his view (the visgroups his client has open) gets 9-13 toons
                    a.target = getattr(a, 'viewTarget', None) or random.randint(*WATCHED_VIEW)
                    a.viewTarget = a.target
                    if self.areaAll.get(a, 0) >= STREET_HARD_MAX:
                        a.target = min(a.target, counts.get(a, 0))
                else:
                    a.viewTarget = None
                    a.target = base
        if not self.enabled:
            self.__drain(now)
        else:
            self.__recall(now, counts)
            self.__fill(now, counts, online)
            self.__rotate(now, counts)
            self.__freeBots(now)
        self.__streetSample(now)
        space.sample(self, now)                      # personal space proof sampler (<run>/bots-gaps.on)
        if now - self.lastStatus >= 5.0:
            self.lastStatus = now
            self.__writeStatus(counts)

    def __streetSample(self, now):
        """STREET LIFE proof sampler: while <run>/bots-street.on exists, once a director second, every bot on a
        street a real player is on (zone, x, y, activity, label, walking, battle) and the street's battles, plus
        the SpeedChat lines bots said there, go to <run>/bots-street.jsonl."""
        if not os.path.exists(os.path.join(self.runDir, 'bots-street.on')):
            self.streetLog = None
            return
        if self.streetLog is None:
            self.streetLog = []
            return
        from toontown.bots.activities.battle import streetBattles
        rows = []
        for a in self.world.areas.values():
            if a.kind != 'street' or not a.players:
                continue
            bts = []
            for o in streetBattles(self, a):
                m, p = o.get('setMembers'), o.get('setPosition')
                if m and p:
                    bts.append([o.doId, round(p[0], 1), round(p[1], 1), list(m[6]), [t for t in m[6] if t not in self.bots]])
            bs = []
            for b in self.bots.values():
                if b.area is a and b.state == 'present' and b.zoneId in a.zones:
                    act = b.activity
                    bs.append([b.avId, b.zoneId, round(b.pos[0], 1), round(b.pos[1], 1), act.name if act else None,
                               getattr(act, 'label', None), bool(b.path), getattr(act, 'phase', None)])
            says = [s for s in self.streetLog if s[2] in a.zones]
            rows.append({'t': round(now, 2), 'area': a.id, 'battles': bts, 'bots': bs, 'says': says})
        self.streetLog = []
        try:
            with open(os.path.join(self.runDir, 'bots-street.jsonl'), 'a') as f:
                f.write(''.join(json.dumps(r) + '\n' for r in rows))
        except Exception:
            self.error('street sample', traceback.format_exc())

    def __priority(self, a, counts):
        # real player there first, then warm (neighbours / his loading hood), then home-hood places
        # before the shared ones (they take the hoods' leftovers), then the emptiest
        return (0 if a.players else (1 if a.id in self.warm else 2), a.id in SHARED,
                -(a.target - counts.get(a, 0)) / float(max(a.target, 1)))

    def __fill(self, now, counts, online):
        logins, moves = self.loginsPerSec, self.movesPerSec
        # POP: an unwatched place is refilled only once it is FILL_SLACK short (a toon stepping into a shop and out
        # again moves nobody); where a real player is (or is about to be) it is kept exact
        need = [a for a in self.world.areas.values() if counts.get(a, 0) < a.target - (
            FILL_SLACK if a.kind in VISIBLE_KINDS and not a.players and a.id not in self.warm else 0)]
        need.sort(key=lambda a: self.__priority(a, counts))
        offline = [b for b in self.bots.values() if b.state == 'offline' and self.load.mayLogin(b, now)   # P10b
                   and not progress_pool.waitingReset(self, b)]            # PROGRESSION: fresh toons only
        random.shuffle(offline)
        offline.sort(key=lambda b: STYLE_RANK.get(b.style, 1))          # PROGRESSION: grinders log in first
        for a in need:
            while counts.get(a, 0) < a.target:
                bot = None
                if logins > 0 and online < self.cap:
                    bot = next((b for b in offline if self.placeable(b, a)), None)
                    if bot is not None:
                        offline.remove(bot)
                        logins -= 1
                        online += 1
                if bot is None and moves > 0:
                    bot = self.__spareFrom(a, counts)
                    if bot is not None:
                        counts[bot.area] -= 1
                        moves -= 1
                if bot is None:
                    break
                counts[a] = counts.get(a, 0) + 1
                self.__send(bot, a, 'fill')
        # areas over target with nobody else needing their bots: log the extra ones out
        budget = 2
        for a, n in counts.items():
            if a is None:
                continue
            if a.kind == 'street' and a.players:
                continue        # POP: his view is counted there, not the street; never thin what he watches
            while n > a.max and budget > 0:
                bot = self.__freeIn(a)
                if bot is None:
                    break
                n -= 1
                counts[a] = n
                budget -= 1
                self.__send(bot, None, 'over max')

    def __recall(self, now, counts):
        """POP: a watched street short in his view: free bots already on that street but out of his view take a
        fresh street walk that heads his way (spots near him), before more toons are pulled onto the street."""
        from toontown.bots.activities.street import StreetWalk
        rec = self.__dict__.setdefault('recalled', {})
        for k in [k for k, u in rec.items() if u < now]:
            del rec[k]
        for a, v in getattr(self, 'viewed', {}).items():
            short = a.target - counts.get(a, 0)
            if short <= 0:
                continue
            free = [b for b in self.bots.values() if b.area is a and b.state == 'present' and b.travel is None
                    and not b.pinned and b.zoneId in a.zones and b.zoneId not in v and b.avId not in rec
                    and not self.anchored(b)
                    and (b.activity is None or (b.activity.name == 'streetwalk' and getattr(b.activity, 'visit', None) is None))]
            random.shuffle(free)
            for b in free[:min(short, 3)]:
                act = StreetWalk(b)
                act.forceNear = True
                b.stopWalking()
                if b.startActivity(act):
                    rec[b.avId] = now + 40.0
                    counts[a] = counts.get(a, 0) + 1
                    b.nextTick = min(b.nextTick, now)
                    self.count('in', 'recall')

    def __spareFrom(self, dest, counts):
        """A free bot, eligible for dest, standing in an area that is over its target (or, for a
        home-hood place, in a shared place that is above its minimum)."""
        free = [b for b in self.bots.values() if b.state == 'present' and b.travel is None and not b.pinned
                and b.area is not None and b.area is not dest and self.placeable(b, dest) and not self.anchored(b)
                and (b.activity is None or b.activity.interruptible)]
        cands = [b for b in free if counts.get(b.area, 0) > b.area.target]
        if not cands and dest.id not in SHARED:
            cands = [b for b in free if b.area.id in SHARED and counts.get(b.area, 0) > b.area.min]
        if not cands and dest.players:
            # P7: a place a real player is in may borrow from unwatched places above their minimum
            cands = [b for b in free if not b.area.players and counts.get(b.area, 0) > b.area.min]
        if not cands and dest.players:
            # POP: the pool is spent: unwatched places that are not his neighbours run lean for him
            cands = [b for b in free if not b.area.players and b.area.id not in self.warm
                     and b.area.kind in VISIBLE_KINDS and counts.get(b.area, 0) > int(b.area.min * LEAN)]
        return random.choice(cands) if cands else None

    def __freeIn(self, area):
        cands = [b for b in self.bots.values() if b.area is area and b.state == 'present' and b.travel is None
                 and not b.pinned and not self.anchored(b) and (b.activity is None or b.activity.interruptible)]
        return random.choice(cands) if cands else None

    def __send(self, bot, dest, why):
        tr = BotTravel(self, bot, dest, logout=dest is None, why=why)
        bot.travel = tr
        try:
            tr.begin()
        except Exception:
            bot.travel = None
            self.error('travel begin', traceback.format_exc())
        bot.nextTick = min(bot.nextTick, globalClock.getRealTime())

    def travel(self, bot, dest, why='activity', via=None):
        """Public travel API for activities (P4/P7): send a present bot to area dest the way the
        director would (via='tunnel': it stands at the tunnel into dest). The activity ends."""
        if bot.state != 'present' or bot.travel is not None:
            return False
        tr = BotTravel(self, bot, dest, logout=dest is None, why=why, via=via)
        bot.travel = tr
        try:
            tr.begin()
        except Exception:
            bot.travel = None
            self.error('travel begin', traceback.format_exc())
            return False
        bot.nextTick = min(bot.nextTick, globalClock.getRealTime())
        return True

    def botsIn(self, area):
        """Bots counted in an area (present there, or on their way in)."""
        return self.__countIn(area)

    def __rotate(self, now, counts):
        """The regulars move around: every rotateEvery s one bot walks, tunnels or teleports to
        another place it belongs (or logs out for a while, and another logs in)."""
        if now - getattr(self, '_lastRotate', 0.0) < self.rotateEvery:
            return
        self._lastRotate = now
        movers = [b for b in self.bots.values() if b.state == 'present' and b.travel is None and not b.pinned
                  and now - b.arrivedAt > 90.0 and b.area is not None and not self.anchored(b)
                  and (b.activity is None or b.activity.interruptible)]
        if not movers:
            return
        bot = random.choice(movers)
        src = bot.area
        if counts.get(src, 0) - 1 < src.min:
            return
        offline = [b for b in self.bots.values() if b.state == 'offline' and self.placeable(b, src)
                   and not progress_pool.waitingReset(self, b)]
        if offline and random.random() < 0.15:
            counts[src] -= 1
            self.__send(bot, None, 'rotate: log out')        # an offline regular refills it next tick
            return
        # neighbours are likelier (walk through the tunnel), anywhere it belongs otherwise
        dests = [a for a in self.world.areas.values() if a is not src and self.placeable(bot, a)
                 and counts.get(a, 0) + 1 <= a.max]
        if not dests:
            return
        nbs = [a for a in dests if a.id in src.tunnels]
        dest = random.choice(nbs) if nbs and random.random() < 0.6 else random.choice(dests)
        counts[src] -= 1
        counts[dest] = counts.get(dest, 0) + 1
        self.__send(bot, dest, 'rotate')

    def __drain(self, now):
        budget = 10
        for bot in list(self.bots.values()):
            if budget <= 0:
                break
            if bot.state == 'present' and (bot.travel is None or not bot.travel.logout):
                if bot.travel is not None:
                    continue
                self.__send(bot, None, 'switched off')
                budget -= 1

    def __freeBots(self, now):
        for bot in self.bots.values():
            if bot.state != 'present' or bot.travel is not None or bot.activity is not None:
                continue
            if now - bot.freeSince < 0.5:
                continue
            # PROGRESSION (owner 1a): nobody real online -> no progress activity starts (running ones finish)
            choices = [c for c in activities.REGISTRY if c.weight > 0 and (self.playing or not getattr(c, 'progress', False))
                       and c.runsIn(bot)]
            if not choices:
                continue
            total = sum(c.weight for c in choices)
            r = random.uniform(0, total)
            for cls in choices:
                r -= cls.weight
                if r <= 0:
                    break
            bot.startActivity(cls(bot))
            bot.nextTick = min(bot.nextTick, now)

    # ---- status ------------------------------------------------------------------------------
    def __writeStatus(self, counts):
        areas = sorted(self.world.areas.values(), key=lambda a: (a.hood not in SHARED, a.hood, a.id))
        online = sum(1 for b in self.bots.values() if b.state != 'offline')
        ticks = sorted(self.tickMs) or [0.0]
        data = {
            'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'uptime': int(time.time() - self.started),
            'enabled': self.enabled, 'playing': self.playing, 'playingWhy': self.playingWhy,
            'realOnline': self.realOnline, 'paceCounts': getattr(self, 'paceCounts', None), 'online': online, 'hidden': getattr(self, 'hidden', 0), 'bots': len(self.bots),
            'pool': len(self.pool.specs), 'poolPending': len(self.pool.pending), 'cap': self.cap,
            'players': sorted(self.players.items()), 'errors': self.errors, 'errorLog': self.errorLog,
            'arrivals': self.stats['in'], 'departures': self.stats['out'],
            'views': getattr(self.air, 'viewAudit', None), 'clock': getattr(self, 'clockStats', None),
            'resyncAnswers': getattr(self, 'resyncAnswers', 0),
            'tickMs': {'avg': round(sum(ticks) / len(ticks), 2), 'p50': round(ticks[len(ticks) // 2], 2), 'p99': round(ticks[int(len(ticks) * 0.99) - 1], 2),
                       'max': round(ticks[-1], 2)},
            'areas': [{'id': a.id, 'name': a.name, 'n': counts.get(a, 0), 'target': a.target, 'min': a.min,
                       'max': a.max, 'players': len(a.players), 'all': getattr(self, 'areaAll', {}).get(a, 0),
                       'present': sum(1 for b in self.bots.values() if b.area is a and b.state == 'present'
                                      and b.travel is None)} for a in areas],
        }
        lines = ['TTBOTS  %s  up %ds  Enabled=%s  online %d / %d bots (pool %d, %d still to create in the DB)'
                 % (data['time'], data['uptime'], 'ON' if self.enabled else 'OFF', online, len(self.bots),
                    data['pool'], data['poolPending']),
                 'real players: %s' % (', '.join('%s@%s' % p for p in data['players']) or 'none'),
                 'PROGRESSION: playing=%s (%s); real online now %d (district count, bots on the AI, seen: %s)' % (
                     'ON' if self.playing else 'OFF', self.playingWhy, self.realOnline, getattr(self, 'paceCounts', None)),
                 'POP: bots = SEEN there (a watched street: in his view); all = every bot of the area; out of sight %d'
                 % getattr(self, 'hidden', 0) + ' %s' % sorted(getattr(self, 'hiddenBy', {}).items(), key=lambda kv: -kv[1]),
                 '%-6s %-26s %5s %7s %8s %8s %5s' % ('area', 'name', 'bots', 'target', 'range', 'players', 'all')]
        for r in data['areas']:
            flag = '' if r['min'] <= r['n'] <= r['max'] else '  <-- outside range'
            lines.append('%-6s %-26s %5d %7d %8s %8d %5d%s' % (r['id'], r['name'], r['n'], r['target'],
                                                               '%d-%d' % (r['min'], r['max']), r['players'], r['all'], flag))
        lines.append('total %d (target %d)' % (sum(r['n'] for r in data['areas']),
                                               sum(r['target'] for r in data['areas'])))
        lines.append('arrivals:   ' + (', '.join('%s %d' % kv for kv in sorted(self.stats['in'].items())) or '-'))
        lines.append('departures: ' + (', '.join('%s %d' % kv for kv in sorted(self.stats['out'].items())) or '-'))
        lines.append('tick ms: avg %.2f p99 %.2f max %.2f' % (data['tickMs']['avg'], data['tickMs']['p99'],
                                                                data['tickMs']['max']))
        lines.append('views: %s; battle delivery %s' % ((getattr(self.air, 'viewAudit', None) or {}).get('last') or '-',          # P10
                                                       getattr(getattr(self, 'delivery', None), 'totals', None)))
        lines.append('clock: %s, resync answers %d, resync suggestions to players %d' % (
            getattr(self, 'clockStats', None), getattr(self, 'resyncAnswers', 0), getattr(self, 'peerSuggests', 0)))
        # walking on the spot: a walk/run anim, but no move since the last status (5 s)
        odd = []
        for bot in self.bots.values():
            if bot.state == 'present' and bot.anim in ('walk', 'run') and bot.zoneId is not None:
                last = getattr(bot, '_statusPos', None)
                still = last is not None and (bot.pos - last).length() < 0.05
                bot._statusStill = getattr(bot, '_statusStill', 0) + 1 if still else 0
                if bot._statusStill >= 2:       # two status periods in a row (10 s), not a walk just begun
                    now = globalClock.getRealTime()
                    odd.append('%s %s z%s %s path%d travel=%s act=%s next%+.1f last%+.1f speed%.1f pos%s p0%s' % (
                        bot.avId, bot.name, bot.zoneId, bot.anim, len(bot.path),
                        bot.travel.phase if bot.travel else None, bot.activity.name if bot.activity else None,
                        bot.nextTick - now, bot.lastTick - now, bot.speed, tuple(round(c, 1) for c in bot.pos),
                        tuple(round(c, 1) for c in bot.path[0][:3]) if bot.path else None)
                        + ' diag(advance calls, dist, walkTo calls)=%s' % (getattr(bot, 'diag', None),))
            bot._statusPos = Point3(bot.pos)
        data['walkingOnTheSpot'] = odd
        from toontown.bots import BotToon as _bt
        data['posSamples'] = _bt.SAMPLES[0]
        lines.append('position samples: %d' % _bt.SAMPLES[0])
        lines.append('walking on the spot: %d%s' % (len(odd), ''.join('\n  ' + o for o in odd[:5])))
        self.load.checkAllOnline(sum(1 for b in self.bots.values() if b.state == 'present'), len(self.bots))
        data['load'] = dict(self.load.stats, watchedAreas=len(self.load.watchedAreas))        # P10b
        lines.append(self.load.line())
        lines.append('errors: %d%s' % (self.errors, ''.join('\n  ' + e for e in self.errorLog)))
        try:
            os.makedirs(self.runDir, exist_ok=True)
            for name, text in (('bots-status.txt', '\n'.join(lines) + '\n'),
                               ('bots-status.json', json.dumps(data))):
                tmp = os.path.join(self.runDir, name + '.new')
                with open(tmp, 'w') as f:
                    f.write(text)
                os.replace(tmp, os.path.join(self.runDir, name))
            # run-speed proof: every finished walk-map move since the last status, only while the switch exists
            from toontown.bots import BotToon as _bt
            if os.path.exists(os.path.join(self.runDir, 'bots-moves.on')):
                sent = getattr(self, 'movesSent', None)
                fresh = [m for m in _bt.MOVES if m[1] > sent] if sent is not None else []
                if _bt.MOVES:
                    self.movesSent = _bt.MOVES[-1][1]
                if fresh:
                    with open(os.path.join(self.runDir, 'bots-moves.jsonl'), 'a') as f:
                        f.write(''.join(json.dumps(m) + '\n' for m in fresh))
            else:
                self.movesSent = None
        except Exception:
            self.error('status file', traceback.format_exc())
