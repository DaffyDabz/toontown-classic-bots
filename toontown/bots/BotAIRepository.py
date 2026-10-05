"""The BOT AI process's repository (README.md, How it works).

Its own internal repo on its own channel range (402000000-402999999; the main
AI is 401000000+, the UberDOG 1000000+, client agent 1000000000+). It owns no
game objects and keeps no AI classes: it reads the .dc raw, so it stays small.

SENDER TRICK. A real client's messages reach the State Server with the sender
set to the client's channel, (accountId << 32) | avId (CLIENTAGENT_SET_CLIENT_ID
in AstronLoginManagerUD). The main AI reads that back with
OTPInternalRepository.getAvatarIdFromSender() = sender & 0xFFFFFFFF. The State
Server passes the sender through untouched, so sendAsBot() builds the field
update with dclass.aiFormatUpdate(..., fromChannel=botChannel) and every
existing getAvatarIdFromSender() check in the main AI sees the bot's avId.

REPLIES. The main AI answers a client with sendUpdateToAvatarId(avId), which
goes to the PUPPET channel avId + (1001 << 32). Owner-only fields (ownrecv,
e.g. whispers) and CLIENTAGENT_* messages (eject) go to the account connection
channel accountId + (1003 << 32), which is set as the toon's owner, exactly as
the login manager does for a player. The bot process subscribes to both for
each bot and hands what arrives to that bot (onDirect).

ZONE VIEW: see BotZoneView.py.
"""
from direct.directnotify import DirectNotifyGlobal
from direct.distributed.MsgTypes import *
from direct.distributed.PyDatagram import PyDatagram
from panda3d.direct import DCPacker

from otp.distributed.OtpDoGlobals import OTP_DO_ID_TOONTOWN, OTP_ZONE_ID_DISTRICTS, OTP_ZONE_ID_MANAGEMENT, \
    OTP_DO_ID_FRIEND_MANAGER, OTP_DO_ID_TT_FRIENDS_MANAGER
from toontown.bots.BotZoneView import BotZoneView, unpackEntry, unpackField
from toontown.distributed.ToontownInternalRepository import ToontownInternalRepository

PUPPET = 1001 << 32
ACCOUNT = 1003 << 32
GLOBALS = {OTP_DO_ID_FRIEND_MANAGER: 'FriendManager', OTP_DO_ID_TT_FRIENDS_MANAGER: 'TTFriendsManager'}
ENTRY_TYPES = {STATESERVER_OBJECT_ENTER_LOCATION_WITH_REQUIRED: False,
               STATESERVER_OBJECT_ENTER_LOCATION_WITH_REQUIRED_OTHER: True,
               STATESERVER_OBJECT_ENTER_OWNER_WITH_REQUIRED: False,
               STATESERVER_OBJECT_ENTER_OWNER_WITH_REQUIRED_OTHER: True}


class BotAIRepository(ToontownInternalRepository):
    notify = DirectNotifyGlobal.directNotify.newCategory('BotAIRepository')

    def __init__(self, baseChannel, serverId):
        ToontownInternalRepository.__init__(self, baseChannel, serverId, dcSuffix='')
        self.viewContexts = {}    # GET_ZONES_OBJECTS context -> view
        self.views = {}           # location channel -> view
        self.direct = {}          # puppet / account channel -> bot
        self.dclassOf = {}        # every doId ever seen -> dclass
        self.unknownTypes = set()
        self.districtView = None
        self.districtId = None
        self.botDirector = None
        self.botSenders = set()   # our bots' client channels: their broadcasts echo back, drop them early
        self.doView = {}          # doId -> the view holding it (a broadcast's first target is often not
                                  # the location channel, see __viewFor)
        self.viewAudit = {'overclose': 0, 'lost': 0, 'leak': 0, 'checks': 0, 'last': ''}   # P10

    # the bot process needs the .dc layout only, never the AI classes
    def readDCFile(self, dcFileNames=None):
        dcFile = self.getDcFile()
        dcFile.clear()
        self.dclassesByName = {}
        self.dclassesByNumber = {}
        self.hashVal = 0
        if dcFileNames:
            for name in dcFileNames:
                dcFile.read(name)
        else:
            dcFile.readAll()
        for i in range(dcFile.getNumClasses()):
            dclass = dcFile.getClass(i)
            self.dclassesByName[dclass.getName()] = dclass
            self.dclassesByNumber[dclass.getNumber()] = dclass
        self.hashVal = dcFile.getHash()

    def handleConnected(self):
        ToontownInternalRepository.handleConnected(self)
        # every SET_FIELD comes to Python: none of these objects live in doId2do
        self.setHandleCUpdates(False)
        # global objects never enter a view, but they write to a bot's puppet channel
        # (FriendManager.inviteeFriendQuery, ...): know their classes up front (P0 dropped them)
        for doId, className in GLOBALS.items():
            self.dclassOf[doId] = self.dclassesByName[className]
        self.notify.info('[TTBOTS] bot AI connected on channel %d, looking for the district' % self.ourChannel)
        import time
        self.districtAsked = time.time()
        self.accept('botview-enter', self.__checkDistrict)
        self.accept('botview-field', self.__checkDistrictField)
        self.accept('botview-exit', self.__districtGone)
        self.districtView = self.openView(OTP_DO_ID_TOONTOWN, OTP_ZONE_ID_DISTRICTS)
        # P10b: ask again until the district answers (a first answer lost or late left bots offline for minutes)
        from toontown.bots.BotLoad import DISTRICT_REQUERY
        taskMgr.doMethodLater(DISTRICT_REQUERY, self.__requeryDistrict, 'botai-district-requery')

    def __requeryDistrict(self, task):
        if self.districtId or self.districtView is None:
            return task.done
        self.notify.info('[TTBOTS] no district answer after %.0f s: asking again' % (
            __import__("time").time() - self.districtAsked))
        self.districtView.requery()
        return task.again

    def lostConnection(self):
        self.notify.warning('[TTBOTS] lost the message director, exiting')
        import sys
        sys.exit(1)

    # ---- district --------------------------------------------------------------
    def __checkDistrict(self, view, obj):
        if view is self.districtView:
            self.__maybeDistrict(obj)

    def __checkDistrictField(self, view, obj, fieldName, args, sender):
        if view is self.districtView and fieldName == 'setAvailable':
            self.__maybeDistrict(obj)

    def __maybeDistrict(self, obj):
        if self.districtId or obj.className != 'ToontownDistrict' or not obj.get('setAvailable', (0,))[0]:
            return
        self.districtId = obj.doId
        self.notify.info('[TTBOTS] district %s (%s) is up (%.1f s after connecting)' % (
            obj.doId, obj.get('setName', ('?',))[0], __import__("time").time() - getattr(self, "districtAsked", 0.0)))
        messenger.send('botai-district-up', [obj.doId])

    def __districtGone(self, view, obj, deleted=False):
        if view is self.districtView and obj.doId == self.districtId:
            self.notify.warning('[TTBOTS] district %s went away' % obj.doId)
            self.districtId = None
            messenger.send('botai-district-down')

    # ---- views -----------------------------------------------------------------
    def openView(self, parentId, zoneId):
        ch = (parentId << 32) | zoneId
        view = self.views.get(ch)
        if view is None:
            view = BotZoneView(self, parentId, zoneId)
            self.views[ch] = view
            view.open()
            self.viewQueries = getattr(self, 'viewQueries', 0) + 1     # P10b: each one is a district-wide relay
            self.viewQueryZones = getattr(self, 'viewQueryZones', {})
            self.viewQueryZones[zoneId] = self.viewQueryZones.get(zoneId, 0) + 1
        view.users += 1
        return view

    def openViews(self, parentId, zones, batch=100):
        """P10b: open many shared views with one GET_ZONES_OBJECTS per `batch` zones. Every query
        makes the parent relay it to ALL its children (Astron), so 680 single-zone queries at
        district-up cost the State Server 680 relays to thousands of objects; one query for many zones
        costs one relay. Answers carry their zone: the context maps to {zoneId: view}."""
        out, fresh = [], []
        for z in zones:
            ch = (parentId << 32) | z
            view = self.views.get(ch)
            if view is None:
                view = BotZoneView(self, parentId, z)
                self.views[ch] = view
                self.registerForChannel(ch)
                fresh.append(view)
            view.users += 1
            out.append(view)
        for i in range(0, len(fresh), batch):
            chunk = fresh[i:i + batch]
            ctx = self.getContext()
            self.viewContexts[ctx] = dict((v.zoneId, v) for v in chunk)
            dg = PyDatagram()
            dg.addServerHeader(parentId, self.ourChannel, STATESERVER_OBJECT_GET_ZONES_OBJECTS)
            dg.addUint32(ctx)
            dg.addUint32(parentId)
            dg.addUint16(len(chunk))
            for v in chunk:
                dg.addUint32(v.zoneId)
            self.send(dg)
            taskMgr.doMethodLater(120.0, lambda task, c=ctx: self.viewContexts.pop(c, None) and task.done,
                                  'botai-batch-ctx-%d' % ctx)
        return out

    def closeView(self, view):
        if self.views.get(view.channel) is not view:
            # P10: a second close of a view already closed (or replaced by a new one on the same zone)
            # must never touch the live view: its close would unsubscribe the shared location channel
            self.viewAudit['overclose'] += 1
            self.notify.warning('[TTBOTS] view-lost guard: close of an already closed view (zone %s)' % view.zoneId)
            return
        view.users -= 1
        if view.users <= 0:
            view.close()
            self.views.pop(view.channel, None)

    def auditViews(self, director):
        """P10 invariant check, run by the director every VIEW_AUDIT s. Logs one line; 'view-lost' (a present
        bot whose own zone has no open, subscribed view, or a director view that is not the live one) and
        'view-leak' (an open view nobody holds, or one whose channel is not subscribed) are warnings."""
        a = self.viewAudit
        reg = self._registeredChannels
        lost = []
        for bot in director.bots.values():
            if bot.state != 'present' or bot.zoneId is None or not self.districtId:
                continue
            v = self.views.get((self.districtId << 32) | bot.zoneId)
            if v is None or v.users <= 0 or v.channel not in reg:
                lost.append((bot.avId, bot.zoneId))
        stale = [z for z, v in director.views.items() if self.views.get(v.channel) is not v]
        leak = [v.zoneId for v in self.views.values() if v.users <= 0 or v.channel not in reg]
        botZones = set(b.zoneId for b in director.bots.values() if b.state != 'offline')
        dirZones = set(director.views)
        extra = [v.zoneId for v in self.views.values() if v.zoneId not in dirZones and v.zoneId not in botZones]
        miss = sum(n for v in self.views.values() for k, n in v.stats.items() if k.startswith('unknown:'))
        a['lost'] += len(lost) + len(stale)
        a['leak'] += len(leak)
        a['checks'] += 1
        line = 'views %d (director %d, held by activities only %d) lost %d stale %d leak %d overclose %d, ' \
               'updates for objects not in the view %d' % (len(self.views), len(director.views), len(extra),
                                                            len(lost), len(stale), len(leak), a['overclose'], miss)
        a['last'] = line
        if extra:
            line += '; activity-only zones %s' % sorted(extra)[:16]
        if lost or stale:
            self.notify.warning('[TTBOTS] view-lost: %s; bots %s stale zones %s' % (line, lost[:8], stale[:8]))
        elif leak:
            self.notify.warning('[TTBOTS] view-leak: %s; zones %s' % (line, leak[:8]))
        else:
            self.notify.info('[TTBOTS] view-audit: %s' % line)
        return line

    # ---- sending as a bot client -------------------------------------------------
    @staticmethod
    def botChannel(accountId, avId):
        return (accountId << 32) | avId

    def sendAsBot(self, botChannel, doId, className, fieldName, args):
        dclass = self.dclassesByName[className]
        dg = dclass.aiFormatUpdate(fieldName, doId, doId, botChannel, args)
        self.send(dg)

    def sendInternal(self, doId, className, fieldName, args):
        """A server-to-server update (sender = this process), e.g. online status to the UD."""
        self.send(self.dclassesByName[className].aiFormatUpdate(fieldName, doId, doId, self.ourChannel, args))

    def addPostRemoveUpdate(self, doId, className, fieldName, args):
        """Sent by the message director if this process dies (bots go offline)."""
        self.addPostRemove(self.dclassesByName[className].aiFormatUpdate(fieldName, doId, doId, self.ourChannel, args))

    def openBotChannels(self, bot, accountId, avId):
        for ch in (avId + PUPPET, accountId + ACCOUNT):
            self.direct[ch] = bot
            self.registerForChannel(ch)

    def closeBotChannels(self, accountId, avId):
        for ch in (avId + PUPPET, accountId + ACCOUNT):
            self.direct.pop(ch, None)
            self.unregisterForChannel(ch)

    def setOwner(self, doId, newOwner):
        dg = PyDatagram()
        dg.addServerHeader(doId, self.ourChannel, STATESERVER_OBJECT_SET_OWNER)
        dg.addChannel(newOwner)
        self.send(dg)

    def addPostRemoveDelete(self, doId, senderChannel):
        dg = PyDatagram()
        dg.addServerHeader(doId, senderChannel, STATESERVER_OBJECT_DELETE_RAM)
        dg.addUint32(doId)
        self.addPostRemove(dg)

    # ---- incoming ------------------------------------------------------------------
    def handleDatagram(self, di):
        msgType = self.getMsgType()
        channel = self.getMsgChannel()
        try:
            if msgType == STATESERVER_OBJECT_SET_FIELD:
                self.__handleSetField(di, channel)
            elif msgType == STATESERVER_OBJECT_SET_FIELDS:
                self.__handleSetFields(di, channel)
            elif msgType in ENTRY_TYPES:
                self.__handleEntry(di, channel, ENTRY_TYPES[msgType], owner=msgType >= 2062)
            elif msgType == STATESERVER_OBJECT_CHANGING_LOCATION:
                doId = di.getUint32()
                newParent, newZone = di.getUint32(), di.getUint32()
                view = self.__viewFor(channel, doId)
                if view is not None and (newParent, newZone) != (view.parentId, view.zoneId):
                    view.exit(doId)
            elif msgType == STATESERVER_OBJECT_DELETE_RAM:
                doId = di.getUint32()
                view = self.doView.get(doId)
                if view is not None:
                    view.exit(doId, deleted=True)
            elif msgType == STATESERVER_OBJECT_GET_ZONES_COUNT_RESP:
                ctx = di.getUint32()
                view = self.viewContexts.get(ctx)
                if view is not None and not isinstance(view, dict):
                    view.expected = di.getUint32()
            elif msgType in (STATESERVER_OBJECT_CHANGING_OWNER, STATESERVER_OBJECT_CHANGING_AI):
                pass
            elif 3000 <= msgType < 4000:                # DBSERVER_* answers (HALLOWEEN: queryObject): the base
                ToontownInternalRepository.handleDatagram(self, di)   # repository's dbInterface, untouched
            elif channel in self.direct and 1000 <= msgType < 2000:
                self.direct[channel].onClientAgentMessage(msgType, di)
            elif msgType < 20000 and self.__maybeInterestEntry(msgType, di, channel):
                pass
            else:
                ToontownInternalRepository.handleDatagram(self, di)
        except Exception:
            import traceback
            self.notify.warning('[TTBOTS] datagram %d on %d failed: %s' % (msgType, channel, traceback.format_exc()))

    def __maybeInterestEntry(self, msgType, di, channel):
        """The answer to GET_ZONES_OBJECTS: context + an ENTER_LOCATION body."""
        if di.getRemainingSize() < 18:
            return False
        ctx = di.getUint32()
        view = self.viewContexts.get(ctx)
        if view is None:
            return False
        if msgType not in self.unknownTypes:
            self.unknownTypes.add(msgType)
            self.notify.info('[TTBOTS] zone-objects answers arrive as msgType %d' % msgType)
        doId, parentId, zoneId, dclass, fields = unpackEntry(self, di, msgType % 2 == 1)
        if isinstance(view, dict):          # P10b: a batched query (openViews): the answer's zone picks the view
            view = view.get(zoneId)
            if view is None or self.views.get(view.channel) is not view:
                return True                 # that view was closed before the answer came
        if dclass is not None:
            self.dclassOf[doId] = dclass
            view.enter(doId, parentId, zoneId, dclass, fields)
        return True

    def __handleEntry(self, di, channel, other, owner):
        doId, parentId, zoneId, dclass, fields = unpackEntry(self, di, other, owner)
        if dclass is None:
            return
        self.dclassOf[doId] = dclass
        if owner:
            bot = self.direct.get(channel)
            if bot is not None:
                bot.onOwnerEntry(dclass, fields)
            return
        view = self.views.get(channel) or self.views.get((parentId << 32) | zoneId)
        if view is not None:
            view.enter(doId, parentId, zoneId, dclass, fields)

    def __handleSetField(self, di, channel):
        sender = self.getMsgSender()
        if sender in self.botSenders and channel not in self.direct:
            return                # one of our own bots' broadcasts coming back: we sent it
        doId = di.getUint32()
        dclass = self.dclassOf.get(doId)
        if dclass is None:
            return
        fieldName, value = unpackField(dclass, di)
        if fieldName is None:
            return
        bot = self.direct.get(channel)
        if bot is not None:
            bot.onDirect(doId, dclass, fieldName, value, sender)
            return
        view = self.__viewFor(channel, doId)
        if view is not None:
            view.field(doId, fieldName, value, sender)

    def __viewFor(self, channel, doId):
        # Astron sends one datagram to several channels at once (the SS re-sends a
        # client's broadcast update to [its AI channel, the location channel]) and the
        # MD delivers it once; getMsgChannel() is only the FIRST target, so the location
        # channel is often hidden. Fall back to the view that holds the object.
        view = self.views.get(channel)
        if view is not None:
            return view
        return self.doView.get(doId)

    def __handleSetFields(self, di, channel):
        sender = self.getMsgSender()
        if sender in self.botSenders:
            return
        doId = di.getUint32()
        dclass = self.dclassOf.get(doId)
        view = self.__viewFor(channel, doId)
        if dclass is None or view is None:
            return
        packer = DCPacker()
        packer.setUnpackData(di.getRemainingBytes())
        for _ in range(packer.rawUnpackUint16()):
            f = dclass.getFieldByIndex(packer.rawUnpackUint16())
            if f is None:
                return
            packer.beginUnpack(f)
            v = packer.unpackObject()
            if not packer.endUnpack():
                return
            view.field(doId, f.getName(), v, sender)
