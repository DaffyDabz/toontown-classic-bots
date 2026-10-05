"""TTBOTS P10b: what the load proof found in the BOT AI process.

1. LOGIN ROBUSTNESS. A bot's toon can still be live on the State Server when a new bot AI
   process starts (left by an earlier process): the DBSS then refuses the new activation
   ("already-active") and the bot never appears. Fix, the Astron way:
     - at district-up, before the first login, STATESERVER_OBJECT_DELETE_RAM every pool toon
       (a toon that is not live ignores it: nobody holds its channel), then wait SWEEP_WAIT s;
     - a login that times out deletes the toon again and backs off (BACKOFF, doubling, jittered),
       logging the first failure of a streak only (no retry floods);
     - post-removes (BotToon.activate) still take every toon offline if this process dies.
   The district is asked for again every DISTRICT_REQUERY s until it answers.

2. MESSAGE VOLUME. A bot moving where no real player can see it does not need a 3 Hz
   position broadcast: every broadcast costs the State Server a fan-out to the main AI and the
   location channel. A zone is WATCHED when a real player is in it, in its area, in a neighbouring
   area (he can walk in through a tunnel) or the area is warm (he is loading into it). Outside
   watched zones a moving bot broadcasts once per SLOW_BCAST s and always when it stops (the RAM
   position field stays fresh), and SpeedChat / emotes are not sent at all (not RAM: nobody sees
   them). The moment a zone becomes watched every bot in it broadcasts at once, then at full rate.
"""
import os
import random

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.MsgTypes import STATESERVER_OBJECT_DELETE_RAM
from direct.distributed.PyDatagram import PyDatagram

SWEEP_WAIT = 3.0          # s after the stale-toon sweep before the first login
BACKOFF = 10.0            # s before the first retry of a failed login (doubles, max BACKOFF_MAX)
BACKOFF_MAX = 300.0
DISTRICT_REQUERY = 5.0    # s between district queries until it is found
SLOW_BCAST = 3.0          # s between position broadcasts of a moving bot nobody watches

notify = DirectNotifyGlobal.directNotify.newCategory('BotLoad')


def deleteToon(air, avId):
    """Delete a (possibly stale) live toon: the State Server / DBSS drops it, the main AI gets its delete."""
    dg = PyDatagram()
    dg.addServerHeader(avId, air.ourChannel, STATESERVER_OBJECT_DELETE_RAM)
    dg.addUint32(avId)
    air.send(dg)


class LoadGuard:
    """Owned by the director: stale sweep, login backoff, the watched-zone set and traffic counters."""

    def __init__(self, director):
        self.director = director
        self.loginHold = 0.0
        self.watchedAreas = set()     # area ids
        self.playerZones = set()      # zones a real player is in (interiors, minigames, battles...)
        self.stats = {'sweep': 0, 'loginFail': 0, 'loginRetry': 0, 'bcast': 0, 'bcastSlowSkip': 0,
                      'chatSkip': 0, 'flush': 0}
        self.firstLogin = None
        self.allOnline = None
        self.startT = globalClock.getRealTime()
        self.fullRate = False

    # ---- 1. logins ----------------------------------------------------------------------------
    def sweep(self, bots):
        """District up: delete every pool toon that may still be live from an earlier process."""
        air = self.director.air
        for bot in bots:
            if bot.state == 'offline':
                deleteToon(air, bot.avId)
                self.stats['sweep'] += 1
        self.loginHold = globalClock.getRealTime() + SWEEP_WAIT
        notify.info('[TTBOTS] stale-toon sweep: %d pool toons deleted if live, logins start in %.0f s'
                    % (self.stats['sweep'], SWEEP_WAIT))

    def mayLogin(self, bot, now):
        return now >= self.loginHold and now >= getattr(bot, 'retryAt', 0.0)

    def loginFailed(self, bot, now):
        """The toon never showed up: delete whatever is live under its id and back off."""
        fails = getattr(bot, 'loginFails', 0) + 1
        bot.loginFails = fails
        delay = min(BACKOFF_MAX, BACKOFF * (2 ** (fails - 1))) * random.uniform(0.8, 1.2)
        bot.retryAt = now + delay
        deleteToon(self.director.air, bot.avId)
        self.stats['loginFail'] += 1
        if fails == 1 or (fails & (fails - 1)) == 0:
            notify.warning('[TTBOTS] login of %s timed out in zone %s (failure %d): stale toon deleted, retry in %.0f s'
                           % (bot.avId, bot.zoneId, fails, delay))

    def loginOk(self, bot):
        if getattr(bot, 'loginFails', 0):
            self.stats['loginRetry'] += 1
            notify.info('[TTBOTS] %s logged in after %d failed tries' % (bot.avId, bot.loginFails))
        bot.loginFails = 0
        bot.retryAt = 0.0
        now = globalClock.getRealTime()
        if self.firstLogin is None:
            self.firstLogin = now

    def checkAllOnline(self, online, total):
        """Status tick: log when 95% and then all of the pool are first present (rotation may log one out
        at any moment, so the 100% mark can come late or never while a place rotates)."""
        if not total or self.firstLogin is None:
            return
        now = globalClock.getRealTime()
        up = now - self.startT
        if getattr(self, 't95', None) is None and online >= 0.95 * total:
            self.t95 = up
            notify.info('[TTBOTS] %d of %d bots present, %.0f s after the process started' % (online, total, up))
        if self.allOnline is None and online >= total:
            self.allOnline = now
            notify.info('[TTBOTS] all %d bots present, %.0f s after the process started' % (total, up))

    # ---- 2. traffic -----------------------------------------------------------------------------
    def update(self):
        """Once a director second: recompute the watched areas; flush the newly watched ones."""
        d = self.director
        try:            # A/B switch for measurements: <run>/bots.fullrate present = every zone counts as watched
            self.fullRate = os.path.exists(os.path.join(d.runDir, 'bots.fullrate'))
        except Exception:
            self.fullRate = False
        areas = set()
        for a in d.world.areas.values():
            if a.players:
                areas.add(a.id)
                for nb in d.world.neighbours(a):
                    areas.add(nb.id)
        areas.update(d.warm.keys())
        zones = set(z for z in d.players.values() if z is not None)
        from toontown.bots.BotTravel import QUIET
        if QUIET in zones:
            # a player is loading (a teleport: we cannot know where to): every area is watched until he lands,
            # so wherever he arrives the bots' RAM positions are fresh and moving at full rate
            areas.update(d.world.areas.keys())
        newAreas = areas - self.watchedAreas
        newZones = zones - self.playerZones
        self.watchedAreas = areas
        self.playerZones = zones
        if newAreas or newZones:
            for bot in d.bots.values():
                if bot.state == 'present' and (bot.zoneId in newZones or self.__areaId(bot) in newAreas):
                    bot.lastBcast = 0.0         # broadcast on its next tick, full rate from then on
                    if not bot.path:
                        bot.dirty = True
                    self.stats['flush'] += 1

    def __areaId(self, bot):
        a = self.director.world.zoneToArea.get(bot.zoneId)
        return a.id if a is not None else None

    def watched(self, bot):
        if self.fullRate:
            return True
        z = bot.zoneId
        if z in self.playerZones:
            return True
        a = self.director.world.zoneToArea.get(z)
        return a is not None and a.id in self.watchedAreas

    def mayBroadcast(self, bot, now):
        """A moving bot's periodic broadcast: always where watched and when it stops, else every SLOW_BCAST s."""
        if not bot.path or now - getattr(bot, 'lastBcast', 0.0) >= SLOW_BCAST or self.watched(bot):
            self.stats['bcast'] += 1
            return True
        self.stats['bcastSlowSkip'] += 1
        return False

    def mayChat(self, bot):
        if self.watched(bot):
            return True
        self.stats['chatSkip'] += 1
        return False

    def line(self):
        s = self.stats
        return ('load: watched areas %d player zones %d; broadcasts sent %d, skipped (unwatched) %d, chat/emote skipped %d, '
                'flushes %d, single zone queries %d; stale sweep %d, login timeouts %d, recovered %d%s' % (
                    len(self.watchedAreas), len(self.playerZones), s['bcast'], s['bcastSlowSkip'], s['chatSkip'],
                    s['flush'], getattr(self.director.air, 'viewQueries', 0),
                    s['sweep'], s['loginFail'], s['loginRetry'],
                    ', 95%% present at %.0f s' % self.t95 if getattr(self, 't95', None) is not None else '')
                + (', all present at %.0f s' % (self.allOnline - self.startT) if self.allOnline is not None else '')
                + (' [FULL RATE switch on]' if self.fullRate else '')
                + '; top single-query zones %s' % sorted(getattr(self.director.air, 'viewQueryZones', {}).items(),
                                                         key=lambda kv: -kv[1])[:8])
