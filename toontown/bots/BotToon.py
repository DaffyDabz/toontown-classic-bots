"""One bot toon (TTBOTS P2): a real toon in the DB, driven from the BOT AI process exactly like
a client drives its toon (every update goes out with the SENDER = the bot's client channel, see
BotAIRepository.py). It holds no task of its own: the director's ONE central tick calls tick()
when the bot is due (every BCAST s while walking, every IDLE s while standing), so 300 bots
cost one task, and position broadcasts are staggered because every bot has its own due time.

What lives here, shared by every activity:
  - walking on the baked walk map (P3), with the street visgroup kept in step as a client does,
  - position broadcast (setSmPosHpr while moving, one setSmStop when it stops, nothing idle),
  - anim / SpeedChat / emote helpers,
  - the P1 social answers: friend requests accepted, whispers answered, teleport queries,
  - activation / relocation / logout primitives the director and BotTravel use.
"""
import math
import random
import traceback

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.ClockDelta import globalClockDelta
from direct.distributed.MsgTypes import STATESERVER_OBJECT_DELETE_RAM, STATESERVER_OBJECT_SET_LOCATION
from direct.distributed.PyDatagram import PyDatagram
from panda3d.core import Point3

from otp.distributed.OtpDoGlobals import OTP_DO_ID_FRIEND_MANAGER, OTP_DO_ID_TT_FRIENDS_MANAGER
from otp.otpbase import OTPGlobals
from toontown.bots import space
from toontown.toonbase import ToontownGlobals

WALK_SPEED = 8.0                           # ft/s strolling
RUN_SPEED = OTPGlobals.ToonForwardSpeed    # 20 ft/s, a player running (the run cycle's own speed)
# owner 09-25: real players ran almost everywhere; a stroll was the odd one out. Ambient trips (playground,
# street, shop, tunnel, trolley) run this share of the time, the rest are a slow walk.
STROLL_SHARE = 0.15
# every finished walk-map move (t0, t1, area, zone, activity, anim, speed, dist, moveTime); dumped to
# <run>/bots-moves.jsonl by the director while <run>/bots-moves.on exists (the run-speed proof sampler)
MOVES = []
MOVES_MAX = 20000
SAMPLES = [0]                              # position samples sent (status line)
ARRIVE_LAG = 0.3                           # s a walk/run anim outlasts arrival (a client's smoother lag)


def tripSpeed(stroll=STROLL_SHARE):
    """(speed, anim) for an ambient trip: the run cycle at the player run speed, or now and then a stroll."""
    return (WALK_SPEED, 'walk') if random.random() < stroll else (RUN_SPEED, 'run')


def gaitFor(slack, anim):
    """A follower's gait for `slack` ft beyond its follow gap: run (the player run speed and the run cycle,
    never a run cycle at a slower speed: feet would slide), a walk for the last few feet, or stand."""
    if slack > 2.5 or (anim == 'run' and slack > 0.75):
        return RUN_SPEED, 'run'
    if slack > 0.75:
        return WALK_SPEED, 'walk'
    return 0.0, 'neutral'


BCAST = 0.3                                # s between position broadcasts while moving
# owner 09-25 'rubber banding': a client draws other toons smooth-lag (0.2 s) in the past and, with prediction off,
# HOLDS a toon at its newest sample when that runs out. A sample every 0.35 s (0.3 + the director's 0.1 s tick)
# left 58% of moving frames frozen, then a jump forward (1456 snaps > 1 ft in 60 s). A real client sends every
# 0.2 s; a watched moving bot now sends on EVERY director pass (~0.1 s; due 'now', since a due time of now + 0.1
# taken late in a 20 ms pass missed the next pass and made 0.23 s gaps), so the newest sample stays < 0.2 s old.
# The director passes every 0.05 s; a moving bot sends at most every BCAST_WATCHED s (a real client: 0.2 s).
# (The client's clock-skew averaging then shifts its draw time back when a late sample follows a hold: that
# was the ~1 ft step BACK; no hold, no step back.)
BCAST_WATCHED = 0.09
IDLE = 1.0                                 # s between ticks while standing (no broadcast)
# sweep fix: the off-walk-map guard (a standing toon further than this from its nearest node is snapped back)
GROUND_EVERY = 5.0
GROUND_XY = 8.0                            # (a door stoop is ~4 ft off the map and 3.3 ft up: left alone)
GROUND_Z = 4.0
GROUNDED = ('hangout', 'wander', 'stroll', 'streetwalk', 'command', 'follow', 'tunnel', 'shop', 'treasure')
SNAPS = {}

# owner/puppet-channel fields a bot answers (on_<field>) or just takes in (P1)
SOCIAL = ('setFriendsList', 'inviteeFriendQuery', 'inviteeCancelFriendQuery', 'setWhisperSCFrom',
          'setWhisperSCCustomFrom', 'setWhisperSCEmoteFrom', 'setWhisperSCToontaskFrom', 'setWhisperFrom',
          'setTalkWhisper', 'teleportQuery')
OWN_TRACKED = ('setInventory', 'setMoney', 'setExperience', 'setMaxCarry', 'setTrackAccess', 'setEmoteAccess',
               'setAccess', 'setQuests', 'setHp', 'setMaxHp',
               # PROGRESSION: the owner view follows a toon that tiers up mid-session (home hood, eligibility, goals)
               'setRewardHistory', 'setQuestCarryLimit', 'setTeleportAccess', 'setHoodsVisited', 'setQuestHistory',
               'setCogParts', 'setCogMerits', 'setCogTypes', 'setCogLevels', 'setFishingRod', 'setMaxMoney',
               'setFriendsList',
               'setCustomMessages', 'setCheesyEffect')     # HALLOWEEN: its phrases, the pumpkin head
_PROGRESS = [None, -1e9]    # PROGRESSION: toontown.bots.progress (the lead's brain), imported lazily
COMMAND_HOOKS = []      # P9 (activities/commands.py): fn(bot, fieldName, args) -> True = the whisper was a command
QUIET = ('friendOnline', 'friendOffline', 'friendsList', 'teleportGiveup', 'friendsNotify', 'setSystemMessage',
         'setDefaultShard', 'setLastHood', 'setDefaultZone', 'setHoodsVisited', 'setTeleportAccess')

# SpeedChat answers (OTPLocalizer SpeedChatStaticText ids)
GREETINGS = range(100, 110)
REPLIES = {108: (309, 301), 107: (303, 309), 200: (200, 202, 204), 201: (202, 204), 202: (200, 204),
           203: (500, 204), 204: (500, 204), 205: (500,), 206: (3,), 207: (200, 202), 208: (202,),
           500: (502, 501, 503), 505: (509, 506), 506: (301, 309), 507: (301, 3), 508: (3, 301),
           513: (500, 3), 514: (3, 508), 510: (2, 1), 515: (1,), 511: (1, 2), 409: (3,)}


def replyTo(msgId):
    if msgId in GREETINGS:
        return random.choice((100, 101, 102, 103, 104))
    if msgId in REPLIES:
        return random.choice(REPLIES[msgId])
    if msgId is not None and 300 <= msgId < 400:
        return random.choice((306, 301, 303))
    if msgId is not None and 400 <= msgId < 500:
        return random.choice((401, 402))
    return random.choice((101, 3, 303))


def replyToText(text):
    words = set(''.join(c if c.isalnum() else ' ' for c in str(text).lower()).split())
    if words & {'hi', 'hello', 'hey', 'howdy', 'yo', 'sup'}:
        return random.choice((100, 101, 102))
    if words & {'bye', 'cya', 'later'}:
        return random.choice((200, 202))
    if words & {'thanks', 'thank', 'ty'}:
        return 502
    return random.choice((3, 303, 409))


class BotToon:
    notify = DirectNotifyGlobal.directNotify.newCategory('BotToon')

    def __init__(self, director, spec, entry):
        self.director = director
        self.air = director.air
        self.key = spec['key']
        self.name = spec['name']
        self.baseHome = spec['home']      # PROGRESSION: the crew's / a pinned bot's home; see the home property
        self.tier = spec.get('tier', 0)
        self.pinned = spec.get('pinned', False)
        self.crew = bool(spec.get('crew', False))     # PROGRESSION: the Cog HQ crew (301-360), never reset
        self.style = spec.get('style', 'regular')      # PROGRESSION: grinder / regular / casual
        self.resetHold = 0.0
        self._reach = (None, None, None)
        self.accountId = entry['accountId']
        self.avId = entry['avId']
        self.channel = self.air.botChannel(self.accountId, self.avId)
        self.state = 'offline'      # offline / activating / present / leaving
        self.area = None
        self.zoneId = None
        self.node = None
        self.pos = Point3(0, 0, 0)
        self.h = 0.0
        self.anim = None
        self.path = []              # [(x, y, z, node)] still to walk
        self.speed = WALK_SPEED
        self.dirty = False          # moved since the last broadcast
        self.moving = False         # last broadcast was a move (a setSmStop is owed)
        self.nextTick = 0.0
        self.lastTick = 0.0
        self.activity = None
        self.travel = None          # BotTravel while changing area / logging in or out
        self.ownFields = {}
        self.pendingInvites = set()
        self.channelsOpen = False
        self.postRemoved = False
        self.seenCallback = None
        self.freeSince = 0.0
        self.arrivedAt = 0.0
        self.stoodAt = 0.0          # personal space (space.py): when it last stopped walking
        self.stall = 0

    def __repr__(self):
        return '<Bot %s %s %s>' % (self.avId, self.name, self.zoneId)

    # ---- PROGRESSION: home and reach follow the toon's own progress --------------------------------------
    @property
    def home(self):
        """The crew and a pinned bot keep their home; a progressing bot's home is the hood of its reward tier."""
        if self.crew or self.pinned:
            return self.baseHome
        from toontown.bots import progress_pool
        return progress_pool.homeForTier(progress_pool.rewardTier(self))

    def reach(self):
        """Hoods a progressing bot may stand in: teleport access + its home hood + TTC (cached on the fields)."""
        rh, ta = self.ownFields.get('setRewardHistory'), self.ownFields.get('setTeleportAccess')
        if self._reach[0] is rh and self._reach[1] is ta and self._reach[2] is not None:
            return self._reach[2]
        from toontown.bots import progress_pool
        hoods = set(progress_pool.teleportAccess(self))
        hoods.add(progress_pool.homeForTier(progress_pool.rewardTier(self)))
        hoods.add(2000)
        self._reach = (rh, ta, frozenset(hoods))
        return self._reach[2]

    # ---- P10: like a client, a bot always has interest in its own location zone ----------
    # (a refcounted shared view: an activity closing ITS reference early can no longer leave
    # the bots still standing in that zone deaf to its broadcasts)
    @property
    def zoneId(self):
        return self.__dict__.get('_zoneId')

    @zoneId.setter
    def zoneId(self, z):
        old = self.__dict__.get('_zoneId')
        self.__dict__['_zoneId'] = z
        if z == old:
            return
        was = self.__dict__.get('_interest')
        self.__dict__['_interest'] = self.air.openView(self.air.districtId, z) \
            if z is not None and self.air.districtId else None
        if was is not None:
            self.air.closeView(was)

    # ---- sending as the bot's client ---------------------------------------------
    def send(self, fieldName, args, doId=None, className='DistributedToon'):
        self.air.sendAsBot(self.channel, doId or self.avId, className, fieldName, args)

    def say(self, msgId):
        load = getattr(self.director, 'load', None)      # P10b: nobody hears SpeedChat in an unwatched zone
        if load is not None and not load.mayChat(self):
            return
        self.send('setSC', [msgId])
        log = getattr(self.director, 'streetLog', None)      # STREET LIFE sampler (off unless bots-street.on)
        if log is not None:
            log.append((round(globalClock.getRealTime(), 2), self.avId, self.zoneId, msgId))

    def emote(self, emoteIndex):
        load = getattr(self.director, 'load', None)
        if load is not None and not load.mayChat(self):
            return
        self.send('setEmoteState', [emoteIndex, 1.0, globalClockDelta.getFrameNetworkTime()])

    def setAnim(self, anim, force=False):
        """self.anim changes at once (every brain reads it); what CLIENTS see may wait: a stand (or any non-moving
        anim) asked for within ARRIVE_LAG s of the last step goes out when the client's smoothed toon has arrived,
        so its feet never stop while it still glides (owner 09-25: the ~3 ft slide). force=True sends at once."""
        if anim == self.anim and not force:
            return
        self.anim = anim
        now = globalClock.getRealTime()
        moved = getattr(self, '_movedAt', -1.0)
        if not force and anim not in ('walk', 'run') and getattr(self, '_animSent', None) in ('walk', 'run') \
                and now - moved < ARRIVE_LAG:
            self._pendingAnim = (anim, moved + ARRIVE_LAG)
            self.nextTick = min(self.nextTick, moved + ARRIVE_LAG)
            return
        self._pendingAnim = None
        if anim == getattr(self, '_animSent', None) and not force:
            return                                  # a stand cancelled before it went out: still in that cycle
        self.__sendAnim(anim)

    def settled(self, now):
        """True once every client has drawn me arriving (my last step is ARRIVE_LAG s + a tick old): only then may
        a client-side door track take me over (its stopSmooth pops the toon to my newest sample)."""
        return now - getattr(self, '_movedAt', -1.0) >= ARRIVE_LAG + 0.1

    def __sendAnim(self, anim):
        self._animSent = anim
        self.send('setAnimState', [anim, 1.0, globalClockDelta.getFrameNetworkTime()])

    def __flushAnim(self, now):
        p = getattr(self, '_pendingAnim', None)
        if p is not None and now >= p[1]:
            self._pendingAnim = None
            if p[0] == self.anim and p[0] != getattr(self, '_animSent', None):
                self.__sendAnim(p[0])

    def broadcastNow(self, t=None):
        # P10: stamped with the time the position is FOR (the tick's time when ticking), not the send time
        if getattr(self.activity, 'holdsPose', False):
            # sweep fix (the toons 6 ft under TTC): a client sitting in a fishing spot sends no position; every
            # other client has the toon parented to the spot, where a setSmPosHpr / setSmStop (a P10b flush when
            # a player arrives) is applied RELATIVE to the spot: the toon drops ~6 ft under the ground and far away
            self.dirty = False
            self.moving = False
            return
        p = self.pos
        ts = globalClockDelta.getRealNetworkTime(bits=16) if t is None else globalClockDelta.localToNetworkTime(t, bits=16)
        self.send('setSmPosHpr', [p[0], p[1], p[2], self.h, 0, 0, ts])
        SAMPLES[0] += 1
        self.dirty = False
        self.lastBcast = globalClock.getRealTime() if t is None else t     # P10b

    # ---- life: activation, relocation, logout (director / BotTravel) -------------
    @property
    def view(self):
        return self.director.viewOf(self.zoneId)

    def activate(self, zoneId, seenCallback):
        """Log in: the DBSS loads the toon from the DB into zoneId, the main AI generates it."""
        if not self.channelsOpen:
            self.air.openBotChannels(self, self.accountId, self.avId)
            self.channelsOpen = True
        self.state = 'activating'
        self.zoneId = zoneId
        self.seenCallback = seenCallback
        self.air.sendActivate(self.avId, self.air.districtId, zoneId)
        if not self.postRemoved:
            # if this process dies, the message director deletes the toon and tells the friends UD
            self.air.addPostRemoveDelete(self.avId, self.channel)
            self.air.addPostRemoveUpdate(OTP_DO_ID_TT_FRIENDS_MANAGER, 'TTFriendsManager', 'avatarOffline',
                                         [self.avId])
            self.postRemoved = True

    def onSeen(self, view, obj):
        """My toon object showed up in a zone view (after activate or a relocation)."""
        if self.state == 'activating':
            self.state = 'present'
            self.air.setOwner(self.avId, self.accountId + (1003 << 32))
            self.send('setParent', [ToontownGlobals.SPRender])
            self.air.sendInternal(OTP_DO_ID_TT_FRIENDS_MANAGER, 'TTFriendsManager', 'avatarOnline',
                                  [self.avId, self.accountId])
            self.director.syncClock(self)
            if getattr(self.director, 'load', None) is not None:     # P10b
                self.director.load.loginOk(self)
        cb, self.seenCallback = self.seenCallback, None
        if cb:
            cb()

    def relocate(self, zoneId, pos=None, h=None, anim=None, area=None):
        """Move the toon to another location zone, as a client's setLocation does. Position and anim
        are sent FIRST (while still in the old zone), so a client in the new zone generates the toon
        already standing there, in that anim (a TeleportIn with a fresh timestamp plays the hole)."""
        if pos is not None:
            self.pos = Point3(*pos)
            if h is not None:
                self.h = h
            self.broadcastNow()
            self.moving = False
            self.stoodAt = globalClock.getRealTime()
        if anim is not None:
            self.setAnim(anim, force=True)
        if area is not None:
            self.director.setArea(self, area)
        old, self.zoneId = self.zoneId, zoneId
        dg = PyDatagram()
        dg.addServerHeader(self.avId, self.air.ourChannel, STATESERVER_OBJECT_SET_LOCATION)
        dg.addUint32(self.air.districtId)
        dg.addUint32(zoneId)
        self.air.send(dg)
        if old == OTPGlobals.QuietZone and zoneId != OTPGlobals.QuietZone and self.state == 'present':
            # sweep fix: a client arriving in a place (Place.enterTeleportIn, a door, a tunnel) sends
            # b_setParent(SPRender); without it a client that last saw this toon on the trolley car / at a
            # fishing spot can keep it parented there (seen standing 6 ft under TTC)
            self.send('setParent', [ToontownGlobals.SPRender])

    def logout(self, why):
        self.endActivity('logout')
        self.path = []
        if self.state in ('present', 'leaving', 'activating'):
            self.air.sendInternal(OTP_DO_ID_TT_FRIENDS_MANAGER, 'TTFriendsManager', 'avatarOffline', [self.avId])
            dg = PyDatagram()
            dg.addServerHeader(self.avId, self.channel, STATESERVER_OBJECT_DELETE_RAM)
            dg.addUint32(self.avId)
            self.air.send(dg)
        if self.channelsOpen:
            self.air.closeBotChannels(self.accountId, self.avId)
            self.channelsOpen = False
        self.state = 'offline'
        self.travel = None
        self.director.setArea(self, None)
        self.zoneId = None
        self.anim = None
        self.notify.debug('[TTBOTS] %s logged out: %s' % (self.avId, why))

    def gone(self, why):
        """The toon was deleted under us (district down, a kick): forget it, no messages."""
        self.endActivity(why)
        if self.channelsOpen:
            self.air.closeBotChannels(self.accountId, self.avId)
            self.channelsOpen = False
        self.state = 'offline'
        self.travel = None
        self.director.setArea(self, None)
        self.zoneId = None
        self.anim = None
        self.path = []

    # ---- activities --------------------------------------------------------------
    def startActivity(self, act):
        self.endActivity('replaced')
        self.activity = act
        try:
            ok = act.start()
        except Exception:
            self.director.error('activity %s start' % act.name, traceback.format_exc())
            ok = False
        if ok is False:
            act.releaseAll()
            self.activity = None
            return False
        return True

    def endActivity(self, why):
        act, self.activity = self.activity, None
        if act is not None and not act.stopped:
            act.stopped = True
            try:
                act.stop(why)
            except Exception:
                self.director.error('activity %s stop' % act.name, traceback.format_exc())
            act.releaseAll()
            self.freeSince = globalClock.getRealTime()

    @property
    def busy(self):
        return self.travel is not None or self.state != 'present'

    # ---- the central tick ------------------------------------------------------
    def tick(self, now):
        if self.state == 'present' and not self.crew:
            self.__progressTick(now)
        dt = min(now - self.lastTick, 1.0) if self.lastTick else 0.0
        prev, self.lastTick = self.lastTick, now
        if self.path and not self.moving and self.state == 'present' and self.director.clockSynced and prev \
                and not getattr(self.activity, 'holdsPose', False):
            # smoothness: a client's smoother, told a stopped toon moved, re-marks the old spot AT the new sample's
            # time and so jumps the whole first step (2 ft at run speed). The spot I start from goes out first,
            # stamped at my last tick, so the first step is drawn as a glide from there, like every other.
            self.broadcastNow(max(prev, now - 0.2))
            self.moving = True
        if self.path:
            before = Point3(self.pos)
            self.advance(dt)
            # never walk on the spot: a path that does not move the toon for 5 ticks is dropped
            self.stall = self.stall + 1 if (self.pos - before).length() < 0.001 else 0
            if self.stall >= 5:
                self.notify.warning('[TTBOTS] %s stalled on its path (dt %.3f speed %.1f pos %s next %s, %d left, '
                                    'diag %s, activity %s, travel %s): dropped' % (
                                        self.avId, dt, self.speed, tuple(self.pos), self.path[0][:3] if self.path else None,
                                        len(self.path), getattr(self, 'diag', None),
                                        self.activity.name if self.activity else None,
                                        self.travel.phase if self.travel else None))
                self.stall = 0
                self.path = []
                if self.area is not None:
                    self.node = self.area.wm.nearestNode(self.pos[0], self.pos[1], self.pos[2])
        driver = self.travel or self.activity
        if driver is not None:
            try:
                if not driver.step(now):
                    if driver is self.travel:
                        self.travel = None
                    else:
                        self.endActivity('done')
            except Exception:
                self.director.error('%s step' % driver.name, traceback.format_exc())
                if driver is self.travel:
                    self.travel = None
                else:
                    self.endActivity('error')
        if not self.path and self.travel is None and self.anim in ('walk', 'run') and self.state == 'present' \
                and not (self.activity is not None and getattr(self.activity, 'fast', False)):
            self.setAnim('neutral')           # never walk on the spot
        if not self.path and self.travel is None and self.state == 'present':
            space.settle(self, now)           # personal space: a toon standing inside mine for a while -> a step aside
        if now >= getattr(self, 'groundAt', 0.0):
            self.groundAt = now + GROUND_EVERY
            self.__groundGuard()
        if self.state == 'present' and self.director.clockSynced:
            load = getattr(self.director, 'load', None)     # P10b: slow broadcasts where nobody watches
            if self.dirty and (not self.path or now - getattr(self, 'lastBcast', 0.0) >= BCAST_WATCHED) \
                    and (load is None or load.mayBroadcast(self, now)):
                self.broadcastNow(now)
                self.moving = True
            elif self.dirty:
                pass                                         # still moving, unwatched: no setSmStop either
            elif self.moving:
                if not getattr(self.activity, 'holdsPose', False):     # sweep fix: none from a fishing spot
                    self.send('setSmStop', [globalClockDelta.localToNetworkTime(now, bits=16)])
                self.moving = False
        fast = self.path or self.dirty or self.travel is not None \
            or (self.activity is not None and getattr(self.activity, 'fast', False))
        self.nextTick = now + (BCAST if fast else IDLE)
        if self.path and self.state == 'present':
            load = getattr(self.director, 'load', None)
            if load is None or load.watched(self):
                self.nextTick = now                       # smoothness: every director pass (see the note at BCAST)
        self.__flushAnim(now)
        p = getattr(self, '_pendingAnim', None)
        if p is not None:
            self.nextTick = min(self.nextTick, p[1])

    def __groundGuard(self):
        """Sweep fix: a toon standing on a playground / street off its walk map (a walk-in from a tunnel
        mouth cut short, a dropped path, a spot left without its step-off) is snapped back onto the
        nearest walk-map node, so no kid ever sees one standing under the floor or in the air."""
        a = self.area
        if self.state == 'present' and self.travel is None and \
                (self.activity is None or not self.activity.name.startswith('fishing')):
            # the AI still has this toon in a fishing spot it has walked away from (a seat granted after the bot
            # gave up on it, an exit that never went through): every client keeps the toon parented to that
            # spot and draws it walking 6 ft under the ground (the sweep's "under the floor"). Get up, as a
            # client's Exit button does, and re-assert the parent.
            v = self.view
            for o in (list(v.objects.values()) if v is not None else ()):
                if o.className == 'DistributedFishingSpot' and (o.get('setOccupied') or (0,))[0] == self.avId:
                    self.notify.warning('[TTBOTS] %s still seated in fishing spot %s while %s: requestExit' % (
                        self.avId, o.doId, self.activity.name if self.activity else 'idle'))
                    SNAPS['spot'] = SNAPS.get('spot', 0) + 1
                    self.send('requestExit', [], doId=o.doId, className='DistributedFishingSpot')
                    self.send('setParent', [ToontownGlobals.SPRender])
        if self.state != 'present' or self.path or self.travel is not None or a is None \
                or a.kind not in ('playground', 'street') or self.zoneId not in a.zones:
            return
        if self.activity is not None and self.activity.name not in GROUNDED:
            return
        wm = a.wm
        k = wm.nearestNode(self.pos[0], self.pos[1], self.pos[2])
        if k is None:
            return
        # stacked nodes (a stair, a door step over the lawn) share a cell: take the one at the toon's height
        k = min(wm._cell.get((wm._i[k], wm._j[k])) or [k], key=lambda m: abs(wm._z[m] - self.pos[2]))
        x, y, z = wm.pos(k)[:3]
        if math.hypot(x - self.pos[0], y - self.pos[1]) <= GROUND_XY and abs(z - self.pos[2]) <= GROUND_Z:
            return
        self.notify.warning('[TTBOTS] %s off the walk map in %s at %s (activity %s): snapped to node %s at %s' % (
            self.avId, a.name, tuple(round(v, 1) for v in self.pos), self.activity.name if self.activity else None,
            k, (round(x, 1), round(y, 1), round(z, 1))))
        SNAPS[a.kind] = SNAPS.get(a.kind, 0) + 1
        self.placeAt(k)

    # ---- walking ---------------------------------------------------------------------
    def placeAt(self, node, h=None):
        self.node = node
        self.pos = Point3(*self.area.wm.pos(node))
        if h is not None:
            self.h = h
        self.dirty = True
        self.stoodAt = globalClock.getRealTime()

    def faceTo(self, target):
        dx, dy = target[0] - self.pos[0], target[1] - self.pos[1]
        if dx * dx + dy * dy > 0.0001:
            self.h = math.degrees(math.atan2(-dx, dy))
            self.dirty = True

    def walkTo(self, node, speed=WALK_SPEED, anim=None, exact=False):
        """Walk the proven walk-map edges from where I stand to node. False when unreachable.
        Personal space (owner 10-04): I stop on node itself only when no toon stands (or is headed) within GAP ft
        of it, else on the nearest free node a step or two round it (space.spotFor); exact=True walks to node."""
        wm = self.area.wm
        start = self.node if self.node is not None else wm.nearestNode(self.pos[0], self.pos[1], self.pos[2])
        if start is None or node is None:
            return False
        if not exact:
            node = space.spotFor(self, node)
        nodes = wm.pathNodes(start, node)
        if not nodes:
            return False
        self.diag = getattr(self, 'diag', [0, 0.0, 0])
        self.diag[2] += 1
        self.path = [wm.pos(k) + (k,) for k in nodes[1:]]
        self.speed = speed
        self.setAnim(anim or ('run' if speed > WALK_SPEED + 2 else 'walk'))
        self.nextTick = min(self.nextTick, globalClock.getRealTime())
        self.__moveEnd(False)
        a = self.area
        self._move = [globalClock.getRealTime(), a.name if a is not None else None, self.zoneId,
                      self.activity.name if self.activity is not None else None, self.anim, speed, 0.0, 0.0,
                      self.path]              # [8]: only this path counts (an activity may set bot.path itself)
        return True

    def __moveEnd(self, done):
        m = getattr(self, '_move', None)
        if m is None:
            return
        self._move = None
        if m[6] > 0.5:
            MOVES.append((round(m[0], 2), round(globalClock.getRealTime(), 2), m[1], m[2], m[3], m[4], m[5],
                          round(m[6], 2), round(m[7], 3), done))
            if len(MOVES) > MOVES_MAX:
                del MOVES[:len(MOVES) - MOVES_MAX]

    def walkToPos(self, x, y, z, speed=WALK_SPEED, anim=None):
        """Walk to the walk-map node nearest (x, y, z), then straight on to (x, y, z) itself
        (a tunnel mouth, a door step: the last few feet the baker left off the map)."""
        k = self.area.wm.nearestNode(x, y, z)
        if not self.walkTo(k, speed, anim, exact=True) and k != self.node:
            return False
        self.path.append((x, y, z, None))
        self.setAnim(anim or ('run' if speed > WALK_SPEED + 2 else 'walk'))
        return True

    def stopWalking(self, anim='neutral'):
        self.path = []
        self.__moveEnd(False)
        self.setAnim(anim)

    def advance(self, dt):
        dist = self.speed * dt
        self.diag = getattr(self, 'diag', [0, 0.0, 0])
        self.diag[0] += 1
        self.diag[1] += dist
        want = dist
        m = getattr(self, '_move', None) if self.path else None
        if m is not None and m[8] is not self.path:
            self.__moveEnd(False)             # someone replaced the walk-map path: that move was cut
            m = None
        if m is not None and self.anim in ('walk', 'run'):
            m[4] = self.anim                          # the cycle actually on show
        while self.path and dist > 0:
            x, y, z, k = self.path[0]
            dx, dy, dz = x - self.pos[0], y - self.pos[1], z - self.pos[2]
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length > 0.01:
                self.h = math.degrees(math.atan2(-dx, dy)) if dx * dx + dy * dy > 0.0001 else self.h
            if length <= dist:
                self.pos = Point3(x, y, z)
                dist -= length
                self.path.pop(0)
                if k is not None:
                    self.node = k
                    self.__checkVisgroup(k)
            else:
                f = dist / length
                self.pos = Point3(self.pos[0] + dx * f, self.pos[1] + dy * f, self.pos[2] + dz * f)
                dist = 0
            self.dirty = True
            self._movedAt = self.lastTick             # the moment this position is stamped for (setAnim)
        if m is not None and self.speed > 0:
            m[6] += want - dist                       # feet actually covered this tick
            m[7] += (want - dist) / self.speed        # and the move time it took
            if not self.path:
                self.__moveEnd(True)
        if not self.path:
            self.stoodAt = self.lastTick              # personal space: of two toons in one spot, the last one in steps
        if not self.path and self.anim in ('walk', 'run') and self.travel is None:
            self.setAnim('neutral')                   # clients see it once the glide has played (setAnim)

    def __checkVisgroup(self, k):
        """On a street the toon's location zone is the visgroup under its feet (the client's
        street loader does the same), so walking on means changing zone now and then."""
        if self.area.kind != 'street' or self.state != 'present':
            return
        z = self.area.wm.zoneOf(k)
        if z != self.zoneId and z in self.area.zones:
            self.relocate(z)

    def __progressTick(self, now):
        """PROGRESSION: the lead's brain (toontown/bots/progress.py) keeps bot.goal / bot.goalText; it throttles
        itself (every ~5 s). Lazy: a missing module is retried once a minute."""
        mod = _PROGRESS[0]
        if mod is None:
            if now - _PROGRESS[1] < 60.0:
                return
            _PROGRESS[1] = now
            try:
                from toontown.bots import progress as mod
            except ImportError:
                return
            _PROGRESS[0] = mod
        if now < getattr(self, '_progErrUntil', 0.0):
            return
        try:
            mod.tick(self, now)
        except Exception:
            self._progErrUntil = now + 60.0        # one traceback a minute per bot at most
            self.director.error('progress tick', traceback.format_exc())

    # ---- things addressed to me (puppet / owner channel) -------------------------
    def onDirect(self, doId, dclass, fieldName, args, sender):
        if doId == self.avId and fieldName in OWN_TRACKED:
            self.ownFields[fieldName] = args      # keep the owner view current (P4 gag shop: pouch, beans)
        if fieldName == 'serverTime':
            self.director.gotServerTime(self, args[0], args[1])
        elif fieldName == 'suggestResync':           # P10: a client asks, answer like a client
            if hasattr(self.director, 'answerResync'):
                self.director.answerResync(self, args)
        elif fieldName in SOCIAL:
            if fieldName in ('setWhisperSCFrom', 'setWhisperSCToontaskFrom') and \
                    any(hook(self, fieldName, args) for hook in COMMAND_HOOKS):
                return
            getattr(self, 'on_' + fieldName)(*args)
        elif fieldName in QUIET:
            pass
        else:
            for driver in (self.travel, self.activity):
                if driver is not None:
                    try:
                        driver.onDirect(fieldName, args)
                    except Exception:
                        self.director.error('%s onDirect' % driver.name, traceback.format_exc())

    def onOwnerEntry(self, dclass, fields):
        self.ownFields = dict(fields)

    def onClientAgentMessage(self, msgType, di):
        self.notify.warning('[TTBOTS] %s got client-agent message %d' % (self.avId, msgType))

    def heard(self, speaker, msgId):
        if self.activity is not None and self.activity.onHeard(speaker, msgId):
            return True
        return False

    # ---- the toon panel, answered as a player at the keyboard would (P1) ------------
    def friendIds(self):
        return [f[0] for f in (self.ownFields.get('setFriendsList') or ([],))[0]]

    def later(self, lo, hi, name, func, *args):
        taskMgr.doMethodLater(random.uniform(lo, hi), lambda task: func(*args),
                              'bot-%s-%d-%d' % (name, self.avId, random.randint(0, 1 << 30)))

    def friendMgr(self, fieldName, args):
        self.send(fieldName, args, doId=OTP_DO_ID_FRIEND_MANAGER, className='FriendManager')

    def on_setFriendsList(self, friends):
        self.ownFields['setFriendsList'] = (friends,)
        self.notify.info('[TTBOTS] %s friends now %s' % (self.avId, [f[0] for f in friends]))

    def on_inviteeFriendQuery(self, inviterId, name, dna, context):
        # the invitee dialog pops up (considering), then a human reads it and clicks Yes
        self.notify.info('[TTBOTS] %s: friend request from %s %s (context %s)' % (self.avId, inviterId, name, context))
        self.pendingInvites.add(context)
        self.later(0.3, 0.8, 'fconsider', self.friendMgr, 'inviteeFriendConsidering', [1, context])
        self.later(2.0, 5.0, 'fanswer', self.answerInvite, context)

    def answerInvite(self, context):
        if context in self.pendingInvites and self.state == 'present':
            self.pendingInvites.discard(context)
            self.friendMgr('inviteeFriendResponse', [1, context])
            self.notify.info('[TTBOTS] %s: accepted friend request (context %s)' % (self.avId, context))

    def on_inviteeCancelFriendQuery(self, context):
        self.pendingInvites.discard(context)
        self.friendMgr('inviteeAcknowledgeCancel', [context])

    def whisperBack(self, toId, msgId):
        if self.state == 'present':
            self.send('setWhisperSCFrom', [self.avId, msgId], doId=toId)
            self.notify.info('[TTBOTS] %s whispers SpeedChat %s to %s' % (self.avId, msgId, toId))

    def answerWhisper(self, fromId, msgId):
        self.later(1.5, 3.5, 'whisper', self.whisperBack, fromId, replyTo(msgId))

    def on_setWhisperSCFrom(self, fromId, msgId):
        self.notify.info('[TTBOTS] %s heard whisper SC %s from %s' % (self.avId, msgId, fromId))
        self.answerWhisper(fromId, msgId)

    def on_setWhisperSCCustomFrom(self, fromId, msgId):
        self.answerWhisper(fromId, None)

    def on_setWhisperSCEmoteFrom(self, fromId, emoteId):
        self.answerWhisper(fromId, None)

    def on_setWhisperSCToontaskFrom(self, fromId, *args):
        self.answerWhisper(fromId, None)

    def on_setWhisperFrom(self, fromId, text, *args):
        self.answerWhisper(fromId, replyToText(text))

    def on_setTalkWhisper(self, fromAV, fromAC, avatarName, chat, mods, flags):
        self.notify.info('[TTBOTS] %s heard typed whisper %r from %s' % (self.avId, chat, fromAV))
        self.answerWhisper(fromAV, replyToText(chat))

    def on_teleportQuery(self, requesterId):
        # DistributedPlayer.teleportQuery + Place.handleTeleportQuery: friends only, and only
        # while standing somewhere (a toon mid-teleport answers "busy", like a client does)
        if requesterId in self.friendIds() and self.state == 'present' and self.travel is None \
                and self.area is not None and self.air.districtId and self.zoneId:
            args = [self.avId, 1, self.air.districtId, self.area.hood, self.zoneId]
        else:
            args = [self.avId, 0, 0, 0, 0]
        self.send('teleportResponse', args, doId=requesterId)
        self.notify.info('[TTBOTS] %s teleportQuery from %s -> %s' % (self.avId, requesterId, args))
