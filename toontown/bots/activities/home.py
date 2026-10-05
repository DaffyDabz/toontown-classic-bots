"""PROGRESSION W2: a bot goes HOME to its own estate for a Phone / Mailbox ToonTask, the way a client does.

  GoHome(bot, want='phone'|'mailbox')     (the lead's toontask.serve hands the bot one; weight 0: never picked idly)
    1. TeleportOut where it stands (3.4 s hole, as Place.requestTeleport plays it), Quiet Zone.
    2. EstateManager.getEstateZone(avId, name)  -> setEstateZone(ownerId, zoneId) on its puppet channel
       (PlayGame.getEstateZoneAndGoHome). A first visit makes the estate + houses on the main AI (LoadEstateFSM:
       ESTATE_ID 0 -> a new DistributedEstate, a DistributedHouse for the toon, setHouseId), as for a new player.
    3. TeleportIn next to ITS house (Estate._teleportToHouse: house-relative (17, 3), H 125), setLocation(estate).
    4. phone: the house door (DistributedHouseDoor requestEnter -> setOtherZoneIdAndDoId, interior door requestExit),
       walk to the phone, DistributedPhone.avatarEnter (-> QuestManagerAI.toonUsedPhone), the catalog: setNewScale,
       browse, buy nothing, avatarExit (HANGUP); EMPTY = the "no catalog yet" dialog, read and closed. Out the door.
       mailbox: walk to its mailbox, DistributedMailbox.avatarEnter (-> toonOpenedMailbox), READY: read the mail and
       avatarExit; EMPTY/WAITING: the message only. After a phone call it opens the mailbox now and then.
    5. TeleportOut, EstateManager.exitEstate (EstateLoader.unload: the AI unloads the estate 5 s later), TeleportIn
       in its playground (the one of the hood it left from).
Points in the estate / house were measured from the shipped models (with a private test script).
Test trigger (W2 only): <run>/bots-w2test.txt, one command per line, run once:
    phone <key|avId|auto|auto175>   mailbox <key|avId|auto>   friend <keyA> <keyB> | friend auto | friend auto150
Log lines: '[TTBOTS-W2]' in botai.log.
"""
import math
import os
import random
import traceback

from direct.directnotify import DirectNotifyGlobal
from panda3d.core import Point3

from otp.distributed.OtpDoGlobals import OTP_ZONE_ID_MANAGEMENT
from otp.otpbase import OTPGlobals
from toontown.bots.BotToon import WALK_SPEED, RUN_SPEED
from toontown.bots.activities import Activity, register

notify = DirectNotifyGlobal.directNotify.newCategory('BotHome')
QUIET = OTPGlobals.QuietZone
TP_OUT = 3.5
TP_IN = 1.8
DOOR_SCALE_EXT = 0.6            # DistributedHouse.__setupDoor: door_origin scale (0.6, 0.6, 0.8)
DOOR_X = 1.5                    # DistributedDoor.doorX

# per house index (HouseGlobals.houseDrops order): house (x, y, z, h); door_origin (x, y, z, h) as the clients set it
# up; ground z in front of the door, at the teleport-in spot and at the mailbox stand (terrain ray, measured)
HOUSES = [
    ((-56.7788, -42.8756, 4.06471, -90.0), (-56.65, -49.93, 4.06, 0.0), 3.95, 3.95, 3.56),
    ((83.3909, -77.5085, 0.0708361, 116.565), (80.12, -71.26, 0.07, -153.4), 0.0, 0.0, 0.0),
    ((-69.077, -119.496, 0.025, 77.1957), (-67.64, -112.59, 0.03, 167.2), 0.0, 0.0, 0.21),
    ((63.4545, 11.0656, 8.05158, 356.6), (70.50, 10.78, 8.05, 86.6), 8.0, 8.0, 7.75),
    ((43.9315, 76.72, 0.0377455, 248.962), (41.52, 70.09, 0.04, -21.0), 0.0, 0.0, 0.03),
    ((-36.9122, 36.3429, 2.49382, 36.8699), (-31.35, 40.68, 2.49, 126.9), 2.32, 2.32, 2.33),
]
# the house interior (tt_m_ara_int_estateHouseA): door_origin (-12, -32.98) H 180 scale 0.8; the phone of every
# starting furniture set is at (-10.1, 2.0) H 0 and a client's pickup puts the toon 4.5 ft in front of it
INT_OUT = (-10.8, -30.58, 0.025)          # where the door's walk-out leaves a toon (door frame (-1.5, -3))
INT_FRONT = (-12.0, -29.78, 0.025)        # a step in front of the inside door (door frame (0, -4))
INT_IN = (-13.2, -34.58, 0.025)           # the door's walk-in end (door frame (1.5, 2))
INT_MID = (-10.5, -18.0, 0.025)
PHONE_POS = (-10.1, 2.0, 0.1)
PHONE_USE = (-10.1, -2.5, 0.025)          # DistributedPhone.takePhoneInterval: (0, -4.5) from the phone, H 0
PHONE_WALK = (-10.1, -6.5, 0.025)         # where the client's pickup walk starts
# SpeedChat
SC_HOME = (1108, 303, 309)                # (a line said on the way now and then)

PHONE_EMPTY, PHONE_PICKUP, PHONE_HANGUP, PHONE_CLEAR = 3, 4, 5, 2
MAIL_EMPTY, MAIL_WAITING, MAIL_READY, MAIL_NOT_OWNER, MAIL_CLEAR, MAIL_EXIT = 3, 4, 5, 6, 2, 7

STATS = {}


def count(key, n=1):
    STATS[key] = STATS.get(key, 0) + n


def note(text):
    notify.info('[TTBOTS-W2] ' + text)


def rot(h, lx, ly):
    r = math.radians(h)
    return lx * math.cos(r) - ly * math.sin(r), lx * math.sin(r) + ly * math.cos(r)


def houseLocal(idx, lx, ly):
    x, y, z, h = HOUSES[idx][0]
    dx, dy = rot(h, lx, ly)
    return x + dx, y + dy


def doorPoint(idx, lx, ly, z=None):
    """A point in the house door's frame (DistributedDoor's offsets, x along the door, -y out of the house)."""
    ox, oy, oz, oh = HOUSES[idx][1]
    dx, dy = rot(oh, lx * DOOR_SCALE_EXT, ly * DOOR_SCALE_EXT)
    return ox + dx, oy + dy, (oz + 0.025) if z is None else z


def questList(bot):
    flat = list((bot.ownFields.get('setQuests') or ([],))[0])
    return [tuple(flat[i:i + 5]) for i in range(0, len(flat) - 4, 5)]


# ---- the EstateManager (the client finds it in the district's management zone) ---------------------------------
class _Mgr:
    def __init__(self):
        self.doId = None
        self.view = None

    def find(self, air):
        if self.doId is not None:
            return self.doId
        if self.view is None and air.districtId:
            self.view = air.openView(air.districtId, OTP_ZONE_ID_MANAGEMENT)
        if self.view is not None:
            o = self.view.first('EstateManager')
            if o is not None:
                self.doId = o.doId
                air.dclassOf[o.doId] = air.dclassesByName['EstateManager']
                air.closeView(self.view)      # dclassOf keeps its class: setEstateZone replies still unpack
                self.view = None
                note('EstateManager is %s' % self.doId)
        return self.doId

    def reset(self):
        self.doId = None


MGR = _Mgr()


@register
class GoHome(Activity):
    name = 'home'
    weight = 0                  # only for a goal (the lead's toontask) or the W2 test trigger
    kinds = ('playground', 'street')
    progress = True
    interruptible = True
    label = 'going home'

    @classmethod
    def canRun(cls, bot):
        g = getattr(bot, 'goal', None)
        return isinstance(g, dict) and g.get('kind') in ('phone', 'mailbox')

    @classmethod
    def startFor(cls, bot, want='phone'):
        """Start a home trip now (a direct call: the test trigger, or the lead's own code). True = running."""
        if bot.state != 'present' or bot.travel is not None or bot.area is None:
            return False
        return bot.startActivity(cls(bot, want=want))

    def __init__(self, bot, want=None):
        Activity.__init__(self, bot)
        if want is None:
            g = getattr(bot, 'goal', None)
            want = g.get('kind') if isinstance(g, dict) else 'phone'
        self.want = want if want in ('phone', 'mailbox') else 'phone'
        self.phase = None
        self.until = 0.0
        self.due = 0.0
        self.estateZone = None
        self.houseIdx = None
        self.houseId = None
        self.extDoId = None
        self.intZone = None
        self.intDoId = None
        self.reply = None
        self.peek = None             # my view of the estate zone (opened before arriving: the house is found first)
        self.intView = None          # my view of the house interior (bot.view is the director's areas only)
        self.away = False            # left the playground (Quiet Zone / estate / house)
        self.inside = False
        self.asked = False           # getEstateZone sent: exitEstate owed
        self.phone = None
        self.phoneState = None
        self.mailbox = None
        self.mailState = None
        self.mailReady = False
        self.doMail = self.want == 'mailbox'
        self.questsBefore = None
        self.startedAt = 0.0
        self.home = None             # the playground to come back to

    # ---- life cycle -------------------------------------------------------------------------------------------
    def start(self):
        bot = self.bot
        if bot.state != 'present' or bot.area is None:
            return False
        d = self.director
        a = bot.area
        self.home = a if a.kind == 'playground' else (d.world.areas.get(a.hood) or a)
        self.startedAt = globalClock.getRealTime()
        self.questsBefore = questList(bot)
        bot.stopWalking()
        self.phase, self.until = 'mgr', self.startedAt + 15.0
        count('trips')
        note('%s goes home (%s) from %s zone %s; quests %s' % (bot.avId, self.want, a.name, bot.zoneId,
                                                               self.questsBefore))
        return True

    def onDirect(self, fieldName, args):
        if fieldName == 'setEstateZone':
            self.reply = ('estate', args[0], args[1])
        elif fieldName == 'setOtherZoneIdAndDoId':
            self.reply = ('door', args[0], args[1])
        elif fieldName == 'rejectEnter':
            self.reply = ('reject',)
        elif fieldName == 'freeAvatar':
            if self.phoneState == 'asked':
                self.phoneState = 'busy'
            if self.mailState == 'asked':
                self.mailState = 'busy'
        elif fieldName == 'sendAvToPlayground':
            # (my own exitEstate unloads the estate, which sends everyone in it, me too, to the playground)
            if self.phase not in ('leavetp', 'tpback', 'done') and self.away:
                note('%s: the estate sent me to the playground (%s)' % (self.bot.avId, args))
                self.__goBack(self.__now())

    def onField(self, obj, fieldName, args):
        if fieldName != 'setMovie':
            return
        me = self.bot.avId
        if self.phone is not None and obj.doId == self.phone and len(args) >= 2:
            mode, avId = args[0], args[1]
            if avId == me and mode == PHONE_PICKUP and self.phoneState in ('asked',):
                self.phoneState, self.due = 'pickup', self.__now() + 1.2
                count('phone_pickup')
                note('%s: phone PICKUP (catalog opens)' % me)
            elif avId == me and mode == PHONE_EMPTY and self.phoneState in ('asked',):
                self.phoneState, self.due = 'reading', self.__now() + random.uniform(2.0, 3.5)
                count('phone_empty')
                note('%s: phone EMPTY (no catalog yet dialog)' % me)
            elif mode == PHONE_HANGUP and avId == me:
                count('phone_hangup')
            elif mode == PHONE_CLEAR and self.phoneState in ('hangup', 'reading'):
                self.phoneState = 'done' if self.phoneState == 'hangup' else self.phoneState
        elif self.mailbox is not None and obj.doId == self.mailbox and len(args) >= 2:
            mode, avId = args[0], args[1]
            if avId == me and self.mailState == 'asked':
                count('mailbox_movie_%d' % mode)
                note('%s: mailbox movie %s' % (me, mode))
                if mode == MAIL_READY:
                    self.mailState, self.due = 'reading', self.__now() + random.uniform(4.0, 8.0)
                    self.mailReady = True
                else:
                    self.mailState, self.due = 'reading', self.__now() + random.uniform(2.0, 3.5)
                    self.mailReady = False

    @staticmethod
    def __now():
        return globalClock.getRealTime()

    # ---- the steps --------------------------------------------------------------------------------------------
    def step(self, now):
        try:
            return getattr(self, 'ph_' + self.phase)(now)
        except Exception:
            self.director.error('home %s' % self.phase, traceback.format_exc())
            self.__abortHome()
            return False

    def ph_mgr(self, now):
        if MGR.find(self.air) is not None:
            bot = self.bot
            self.interruptible = False
            self.away = True
            bot.setAnim('TeleportOut', force=True)
            self.phase, self.until = 'tpout', now + (TP_OUT if self.director.watched(bot.area) else 0.3)
            return True
        if now > self.until:
            count('fail_no_estate_manager')
            note('%s: no EstateManager in the management zone' % self.bot.avId)
            return False
        return True

    def ph_tpout(self, now):
        if now < self.until:
            return True
        bot = self.bot
        bot.relocate(QUIET)
        self.reply = None
        bot.send('getEstateZone', [bot.avId, bot.name], doId=MGR.doId, className='EstateManager')
        self.asked = True
        self.phase, self.until = 'ask', now + 45.0
        return True

    def ph_ask(self, now):
        r = self.reply
        if r is None or r[0] != 'estate':
            if now > self.until:
                count('fail_estate_timeout')
                note('%s: no setEstateZone in 45 s' % self.bot.avId)
                return self.__goBack(now)
            return True
        self.reply = None
        ownerId, zoneId = r[1], r[2]
        if not zoneId:
            count('fail_estate_refused')
            note('%s: setEstateZone(0, 0): no estate' % self.bot.avId)
            return self.__goBack(now)
        self.estateZone = zoneId
        count('estate_zone')
        note('%s: setEstateZone(%s, %s) after %.1f s' % (self.bot.avId, ownerId, zoneId, now - self.startedAt))
        self.peek = self.air.openView(self.air.districtId, zoneId)
        self.phase, self.until = 'peek', now + 15.0
        return True

    def ph_peek(self, now):
        """Find my house in the estate (the client knows it from houseId) before stepping in next to it."""
        v, bot = self.peek, self.bot
        house = None
        for o in v.ofClass('DistributedHouse'):
            if (o.get('setAvatarId') or (0,))[0] == bot.avId:
                house = o
        if house is None:
            if now > self.until:
                count('fail_no_house')
                note('%s: no house of mine in estate zone %s (%d objects)' % (bot.avId, self.estateZone,
                                                                               len(v.objects)))
                return self.__goBack(now)
            return True
        self.houseId = house.doId
        self.houseIdx = (house.get('setHousePos') or (0,))[0] % len(HOUSES)
        for o in v.ofClass('DistributedHouseDoor'):
            zb = o.get('setZoneIdAndBlock') or (0, 0)
            if zb[1] == self.houseId and (o.get('setDoorType') or (1,))[0] == 1:
                self.extDoId = o.doId
        for o in v.ofClass('DistributedMailbox'):
            if (o.get('setHouseId') or (0,))[0] == self.houseId:
                self.mailbox = o.doId
        if self.want == 'phone' and self.extDoId is None and now < self.until:
            return True                       # the door generates right after the house
        # TeleportIn beside my house (Estate._teleportToHouse)
        hx, hy, hz, hh = HOUSES[self.houseIdx][0]
        tx, ty = houseLocal(self.houseIdx, 17, 3)
        bot.path = []
        bot.pos = Point3(tx, ty, HOUSES[self.houseIdx][3])
        bot.h = hh + 125.0
        bot.broadcastNow()
        bot.setAnim('TeleportIn', force=True)
        bot.relocate(self.estateZone)          # (self.peek stays open: the mailbox / door live there)
        count('estate_arrived')
        note('%s: in its estate zone %s at house %s (index %d, door %s, mailbox %s)' % (
            bot.avId, self.estateZone, self.houseId, self.houseIdx, self.extDoId, self.mailbox))
        self.phase, self.until = 'tpin', now + TP_IN
        return True

    def ph_tpin(self, now):
        if now < self.until:
            return True
        bot = self.bot
        bot.setAnim('neutral', force=True)
        if self.want == 'mailbox':
            return self.__toMailbox(now)
        if self.extDoId is None:
            count('fail_no_door')
            return self.__toMailbox(now) if self.mailbox else self.__leave(now)
        i = self.houseIdx
        stand = doorPoint(i, 0, -12, HOUSES[i][2])
        front = doorPoint(i, 0, -5)
        self.__walk([stand, front], WALK_SPEED if random.random() < 0.5 else RUN_SPEED)
        self.phase, self.until = 'walkdoor', now + 40.0
        return True

    def ph_walkdoor(self, now):
        bot = self.bot
        if bot.path:
            if now > self.until:
                bot.stopWalking()
                return self.__leave(now)
            return True
        ox, oy = HOUSES[self.houseIdx][1][:2]
        bot.faceTo((ox, oy))
        bot.setAnim('neutral')
        if not bot.settled(now):
            return True
        self.reply = None
        bot.send('requestEnter', [], doId=self.extDoId, className='DistributedHouseDoor')
        self.phase, self.until = 'enter', now + 4.0
        return True

    def ph_enter(self, now):
        r = self.reply
        if r is not None and r[0] == 'door':
            self.intZone, self.intDoId = r[1], r[2]
            self.reply = None
            x, y, z = doorPoint(self.houseIdx, DOOR_X, 2)
            self.bot.pos = Point3(x, y, z)        # the clients' walk-in ends there; nothing broadcast
            count('door_enter')
            self.phase, self.until = 'doorin', now + 1.6
        elif r is not None or now > self.until:
            count('fail_door_rejected')
            note('%s: house door %s said %s' % (self.bot.avId, self.extDoId, r))
            return self.__toMailbox(now) if self.mailbox else self.__leave(now)
        return True

    def ph_doorin(self, now):
        if now < self.until:
            return True
        bot = self.bot
        bot.path = []
        self.intView = self.air.openView(self.air.districtId, self.intZone)
        bot.relocate(QUIET)
        bot.pos = Point3(*INT_OUT)
        bot.h = 0.0
        bot.broadcastNow()
        bot.setAnim('neutral', force=True)
        bot.relocate(self.intZone)
        bot.send('requestExit', [], doId=self.intDoId, className='DistributedHouseDoor')
        self.inside = True
        count('house_entered')
        note('%s: inside its house (zone %s, door %s)' % (bot.avId, self.intZone, self.intDoId))
        self.phase, self.until = 'arrive', now + 2.2
        return True

    def ph_arrive(self, now):
        if now < self.until:
            return True
        self.bot.setAnim('neutral', force=True)
        self.__walk([INT_MID, PHONE_WALK], WALK_SPEED)
        self.phase, self.until = 'walkphone', now + 30.0
        return True

    def ph_walkphone(self, now):
        bot = self.bot
        if bot.path:
            return True
        v = self.intView
        phone = v.first('DistributedPhone') if v is not None else None
        if phone is None:
            if now > self.until:
                count('fail_no_phone')
                note('%s: no phone in the house (zone %s)' % (bot.avId, self.intZone))
                return self.__leaveHouse(now)
            return True
        self.phone = phone.doId
        bot.faceTo(PHONE_POS)
        bot.setAnim('neutral')
        if not bot.settled(now):
            return True
        if self.director.realPlayersIn(self.intZone) or (phone.get('setMovie') or (PHONE_CLEAR,))[0] not in (
                PHONE_CLEAR, PHONE_HANGUP):
            if now > self.until:
                return self.__leaveHouse(now)
            return True                          # somebody is on it: wait
        bot.send('avatarEnter', [], doId=self.phone, className='DistributedPhone')
        count('phone_avatarEnter')
        note('%s: DistributedPhone %s avatarEnter' % (bot.avId, self.phone))
        self.phoneState, self.due = 'asked', now + 8.0
        self.phase = 'phone'
        return True

    def ph_phone(self, now):
        bot, st = self.bot, self.phoneState
        if st == 'asked' and now > self.due:
            count('fail_phone_no_answer')
            return self.__leaveHouse(now)
        if st == 'busy':
            return self.__leaveHouse(now)
        if st == 'pickup' and now >= self.due:
            # the clients walked me to the phone (takePhoneInterval): stand where they left me
            bot.pos = Point3(*PHONE_USE)
            bot.h = 0.0
            bot.broadcastNow()
            self.__sendScale()
            self.phoneState, self.due = 'browsing', now + random.uniform(5.0, 12.0)
        elif st == 'browsing' and now >= self.due:
            bot.send('avatarExit', [], doId=self.phone, className='DistributedPhone')     # closes the catalog
            count('phone_avatarExit')
            self.phoneState, self.due = 'hangup', now + 4.0
        elif st == 'hangup' and now >= self.due:
            self.phoneState = 'done'
        elif st == 'reading' and now >= self.due:
            self.phoneState = 'done'
        if self.phoneState == 'done':
            self.phoneState = None
            self.phone = None
            count('phone_done')
            note('%s: phone call done; quests now %s' % (bot.avId, questList(bot)))
            if random.random() < 0.3 and self.mailbox:
                self.doMail = True
            return self.__leaveHouse(now)
        return True

    def __sendScale(self):
        """DistributedPhone.__showPhoneGui: the phone scales to the caller (toonBodyScales of its animal)."""
        try:
            from toontown.toon import ToonDNA
            dna = ToonDNA.ToonDNA()
            dna.makeFromNetString((self.bot.ownFields.get('setDNAString') or (b'',))[0])
            s = OTPGlobals.toonBodyScales[dna.getAnimal()]
            self.bot.send('setNewScale', [s, s, s], doId=self.phone, className='DistributedPhone')
        except Exception:
            pass

    def __leaveHouse(self, now):
        self.__walk([INT_MID, INT_FRONT], WALK_SPEED)
        self.phase, self.until = 'leavewalk', now + 30.0
        return True

    def ph_leavewalk(self, now):
        bot = self.bot
        if bot.path and now < self.until:
            return True
        bot.path = []
        bot.faceTo((-12.0, -32.98))
        bot.setAnim('neutral')
        self.reply = None
        bot.send('requestEnter', [], doId=self.intDoId, className='DistributedHouseDoor')
        self.phase, self.until = 'leaveenter', now + 3.0
        return True

    def ph_leaveenter(self, now):
        if self.reply is None and now < self.until:
            return True
        bot = self.bot
        bot.path = [INT_IN + (None,)]
        bot.speed = WALK_SPEED
        bot.setAnim('walk')
        self.phase, self.until = 'doorout', now + 1.6
        return True

    def ph_doorout(self, now):
        if now < self.until:
            return True
        self.__comeOut()
        self.phase, self.until = 'out', now + 2.0
        return True

    def __comeOut(self):
        bot, i = self.bot, self.houseIdx
        bot.path = []
        bot.relocate(QUIET)
        x, y, z = doorPoint(i, -DOOR_X, -3)
        bot.pos = Point3(x, y, z)
        bot.h = HOUSES[i][1][3] + 179.0
        bot.broadcastNow()
        bot.setAnim('neutral', force=True)
        bot.relocate(self.estateZone)
        bot.send('requestExit', [], doId=self.extDoId, className='DistributedHouseDoor')
        self.inside = False
        self.__closeInterior()
        count('house_left')

    def __closeInterior(self):
        if self.intView is not None:
            self.air.closeView(self.intView)
            self.intView = None

    def ph_out(self, now):
        if now < self.until:
            return True
        bot = self.bot
        bot.setAnim('neutral', force=True)
        x, y, z = doorPoint(self.houseIdx, -DOOR_X, -6)
        bot.path = [(x, y, z, None)]          # the clients' walk-out ends ~6 ft out (as the local toon's does)
        bot.speed = WALK_SPEED
        bot.setAnim('walk')
        self.phase, self.until = 'outstep', now + 5.0
        return True

    def ph_outstep(self, now):
        if self.bot.path and now < self.until:
            return True
        self.bot.path = []
        if self.doMail and self.mailbox:
            return self.__toMailbox(now)
        return self.__leave(now)

    # ---- the mailbox ----------------------------------------------------------------------------------------------
    def __toMailbox(self, now):
        if not self.mailbox:
            count('fail_no_mailbox')
            return self.__leave(now)
        i = self.houseIdx
        mx, my = houseLocal(i, 22.5, -4)
        self.__walk([(mx, my, HOUSES[i][4])], WALK_SPEED)
        self.phase, self.until = 'walkmail', now + 30.0
        return True

    def ph_walkmail(self, now):
        bot = self.bot
        if bot.path and now < self.until:
            return True
        bot.path = []
        bot.faceTo(houseLocal(self.houseIdx, 19, -4))
        bot.setAnim('neutral')
        if not bot.settled(now):
            return True
        bot.send('avatarEnter', [], doId=self.mailbox, className='DistributedMailbox')
        count('mailbox_avatarEnter')
        note('%s: DistributedMailbox %s avatarEnter' % (bot.avId, self.mailbox))
        self.mailState, self.due = 'asked', now + 8.0
        self.phase = 'mail'
        return True

    def ph_mail(self, now):
        st = self.mailState
        if st in ('asked',) and now > self.due or st == 'busy':
            count('fail_mailbox_no_answer')
            return self.__leave(now)
        if st == 'reading' and now >= self.due:
            if getattr(self, 'mailReady', False):
                self.bot.send('avatarExit', [], doId=self.mailbox, className='DistributedMailbox')
            count('mailbox_done')
            note('%s: mailbox done; quests now %s' % (self.bot.avId, questList(self.bot)))
            self.mailState = None
            return self.__leave(now)
        return True

    # ---- back to the playground ---------------------------------------------------------------------------------
    def __leave(self, now):
        bot = self.bot
        bot.path = []
        bot.setAnim('TeleportOut', force=True)
        self.phase, self.until = 'leavetp', now + TP_OUT
        return True

    def ph_leavetp(self, now):
        if now < self.until:
            return True
        return self.__goBack(now)

    def __goBack(self, now):
        """Quiet Zone, exitEstate, TeleportIn in the playground (the way a client's teleport home looks)."""
        bot, d = self.bot, self.director
        bot.path = []
        if bot.zoneId != QUIET:
            bot.relocate(QUIET)
        self.__exitEstate()
        a = self.home or bot.area
        node = d.spawnNode(a, bot)
        bot.area = a
        bot.node = node
        bot.pos = Point3(*a.wm.pos(node))
        bot.h = random.uniform(-180, 180)
        bot.broadcastNow()
        watched = d.watched(a)
        bot.setAnim('TeleportIn' if watched else 'neutral', force=True)
        bot.relocate(a.zoneOfNode(node), area=a)
        self.away = False
        self.inside = False
        self.phase, self.until = 'tpback', now + (TP_IN if watched else 0.0)
        return True

    def ph_tpback(self, now):
        if now < self.until:
            return True
        bot = self.bot
        bot.setAnim('neutral', force=True)
        self.interruptible = True
        count('back_home')
        note('%s: back in %s zone %s after %.1f s (%s); quests %s -> %s' % (
            bot.avId, bot.area.name, bot.zoneId, now - self.startedAt, self.want, self.questsBefore, questList(bot)))
        self.phase = 'done'
        return False

    def ph_done(self, now):
        return False

    def __exitEstate(self):
        if self.asked and MGR.doId is not None:
            self.bot.send('exitEstate', [], doId=MGR.doId, className='EstateManager')
            self.asked = False
            count('exitEstate')
        if self.peek is not None:
            self.air.closeView(self.peek)
            self.peek = None
        self.__closeInterior()

    def __walk(self, points, speed):
        bot = self.bot
        bot.path = [(p[0], p[1], p[2], None) for p in points]
        bot.speed = speed
        bot.setAnim('run' if speed > WALK_SPEED + 2 else 'walk')
        bot.nextTick = min(bot.nextTick, self.__now())

    def __abortHome(self):
        """Something broke mid-trip: back to the playground at once (no animation wait)."""
        if self.away and self.bot.state == 'present':
            try:
                self.__goBack(self.__now())
            except Exception:
                self.director.error('home abort', traceback.format_exc())
        self.__exitEstate()

    def stop(self, why):
        bot = self.bot
        if self.phone is not None and self.phoneState in ('pickup', 'browsing'):
            bot.send('avatarExit', [], doId=self.phone, className='DistributedPhone')
        if self.mailbox is not None and self.mailState == 'reading' and getattr(self, 'mailReady', False):
            bot.send('avatarExit', [], doId=self.mailbox, className='DistributedMailbox')
        gone = why in ('logout', 'district down', 'deleted by the server', 'activation timeout')
        if self.away and not gone and bot.state == 'present' and self.phase not in ('tpback', 'done'):
            note('%s: home trip stopped (%s) in phase %s: back to the playground' % (bot.avId, why, self.phase))
            self.__abortHome()
        else:
            self.__exitEstate()
        if self.phase not in ('done',):
            count('stopped_' + str(why).split(' ')[0])


# ---- W2 test trigger (<run>/bots-w2test.txt) -------------------------------------------------------------------
def _find(d, word, pred=None):
    bots = [b for b in d.bots.values() if b.state == 'present' and b.travel is None and b.area is not None]
    if word.startswith('auto'):
        cands = [b for b in bots if b.area.kind == 'playground' and (pred is None or pred(b))]
        return random.choice(cands) if cands else None
    for b in d.bots.values():
        if str(b.key) == word or str(b.avId) == word:
            return b
    return None


def _hasQuest(qid):
    return lambda b: any(q[0] == qid for q in questList(b))


def _command(d, words):
    from toontown.bots import social
    kind = words[0].lower()
    if kind in ('phone', 'mailbox'):
        who = words[1] if len(words) > 1 else 'auto'
        b = _find(d, who, _hasQuest(175) if who == 'auto175' else None)
        if b is None:
            note('test %s %s: no such present bot' % (kind, who))
            return
        if b.area.kind not in GoHome.kinds:
            note('test %s: %s is in %s' % (kind, b.avId, b.area.kind))
            return
        b.endActivity('W2 test')
        ok = GoHome.startFor(b, kind)
        note('test %s: %s (%s) started=%s' % (kind, b.avId, b.name, ok))
    elif kind == 'friend':
        if len(words) >= 3:
            a, o = _find(d, words[1]), _find(d, words[2])
        else:
            mode = words[1] if len(words) > 1 else 'auto'
            a, o = None, None
            pool = [b for b in d.bots.values() if b.state == 'present' and b.travel is None and b.area is not None
                    and b.area.kind == 'playground']
            if mode == 'auto150':
                pool.sort(key=lambda b: not _hasQuest(150)(b))
            else:
                random.shuffle(pool)
            for b in pool:
                others = [x for x in d.bots.values() if x is not b and x.state == 'present' and x.zoneId == b.zoneId
                          and x.avId not in b.friendIds() and x.travel is None]
                if others and len(b.friendIds()) < OTPGlobals.MaxFriends - 1:
                    a, o = b, random.choice(others)
                    break
        if a is None or o is None:
            note('test friend %s: no pair' % words[1:])
            return
        note('test friend: %s (friends %d, quests %s) asks %s' % (a.avId, len(a.friendIds()), questList(a), o.avId))
        ok = social.makeFriend(a, o)
        note('test friend: makeFriend -> %s' % ok)
        taskMgr.doMethodLater(12.0, lambda task, a=a, o=o: note(
            'test friend after 12 s: %s friends %d has %s: %s; quests %s' % (
                a.avId, len(a.friendIds()), o.avId, o.avId in a.friendIds(), questList(a))) or task.done,
                              'w2-friend-check-%d' % a.avId)
    elif kind == 'stats':
        note('stats %s' % sorted(STATS.items()))


def _poll(task):
    try:
        air = simbase.air
        d = getattr(air, 'botDirector', None)
        if d is None or not getattr(d, 'runDir', None):
            return task.again
        path = os.path.join(d.runDir, 'bots-w2test.txt')
        if os.path.exists(path):
            done = path + '.done'
            os.replace(path, done)
            with open(done) as f:
                lines = [l.split('#')[0].strip() for l in f]
            for line in lines:
                if line:
                    try:
                        _command(d, line.split())
                    except Exception:
                        notify.warning('[TTBOTS-W2] test command %r failed: %s' % (line, traceback.format_exc()))
    except Exception:
        notify.warning('[TTBOTS-W2] test poll failed: %s' % traceback.format_exc())
    return task.again


try:
    taskMgr.remove('bots-w2test')
    taskMgr.doMethodLater(2.0, _poll, 'bots-w2test')
    note('home module loaded (test trigger <run>/bots-w2test.txt)')
except NameError:
    pass                         # imported outside the bot AI (tools, the simulator)
