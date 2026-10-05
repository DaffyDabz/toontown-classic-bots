"""TTFriendsManagerUD - the 2013 friends list, online status and avatar details (2013 restore).

The 2013 OTP server answered three client messages that Astron does not have:
CLIENT_GET_FRIEND_LIST (names + DNA of every friend), CLIENT_FRIEND_ONLINE /
CLIENT_FRIEND_OFFLINE (the online dots), and CLIENT_GET_AVATAR_DETAILS (the
Details panel: laff, gags, tracks straight from the database). This UberDOG
(global id 4666, the TTR/Stride pattern) gives the same answers as field updates;
the client's TTFriendsManager feeds them to the unchanged 2013 handlers in
ToontownClientRepository (handleGetFriendsList, handleFriendOnline/Offline,
handleGetAvatarDetailsResp).

The 2013 server also let a toon whisper or teleport to a friend anywhere. Astron's
client agent only lets a client send to objects it can see, so every friend is
DECLARED to the client (CLIENTAGENT_DECLARE_OBJECT): the client's unchanged
sendUpdate(..., sendToId=friendId) then reaches the friend's owner as it did.

Who is online: players come from the login manager ('avatarOnlinePlusAccountInfo'
and 'avatarOffline'), bot toons from the bot AI process (avatarOnline/avatarOffline).
"""
from direct.directnotify import DirectNotifyGlobal
from direct.distributed.DistributedObjectGlobalUD import DistributedObjectGlobalUD
from direct.distributed.MsgTypes import CLIENTAGENT_DECLARE_OBJECT
from direct.distributed.PyDatagram import PyDatagram
from panda3d.direct import DCPacker

FRIEND_FIELDS = ('setName', 'setDNAString', 'setPetId')


class TTFriendsManagerUD(DistributedObjectGlobalUD):
    notify = DirectNotifyGlobal.directNotify.newCategory('TTFriendsManagerUD')

    def __init__(self, air):
        DistributedObjectGlobalUD.__init__(self, air)
        self.online = {}      # avId -> accountId (None for a bot toon)

    def announceGenerate(self):
        DistributedObjectGlobalUD.announceGenerate(self)
        self.accept('avatarOnlinePlusAccountInfo', self.__playerOnline)
        self.accept('avatarOffline', self.__goOffline)

    # ---- helpers ----------------------------------------------------------------
    def __internal(self):
        return (self.air.getMsgSender() >> 32) == 0

    def __toonClass(self):
        return self.air.dclassesByName['DistributedToonUD']

    def __friendsOf(self, avId, callback):
        def got(dclass, fields):
            if dclass != self.__toonClass() or not fields:
                callback(None)
                return
            callback([f[0] for f in fields.get('setFriendsList', ([],))[0]])
        # a field query needs the dclass to turn names into field ids
        self.air.dbInterface.queryObject(self.air.dbId, avId, got, dclass=self.__toonClass(),
                                         fieldNames=('setFriendsList',))

    def __declare(self, accountId, doId):
        if not accountId:
            return
        dg = PyDatagram()
        dg.addServerHeader(self.GetAccountConnectionChannel(accountId), self.air.ourChannel,
                           CLIENTAGENT_DECLARE_OBJECT)
        dg.addUint32(doId)
        dg.addUint16(self.__toonClass().getNumber())
        self.air.send(dg)

    def __tellOnline(self, toId, avId):
        self.sendUpdateToAvatarId(toId, 'friendOnline', [avId, 1, 1])

    # ---- online / offline ---------------------------------------------------------
    def __playerOnline(self, avId, accountId, *args):
        self.__goOnline(avId, accountId)

    def avatarOnline(self, avId, accountId):
        if self.__internal():
            self.__goOnline(avId, None)

    def avatarOffline(self, avId):
        if self.__internal():
            self.__goOffline(avId)

    def __goOnline(self, avId, accountId):
        self.online[avId] = accountId
        self.notify.info('%s online (%s)' % (avId, 'bot' if accountId is None else 'account %s' % accountId))

        def got(friends):
            for fid in friends or []:
                if fid in self.online:
                    self.__declare(self.online[fid], avId)
                    self.__tellOnline(fid, avId)
        self.__friendsOf(avId, got)

    def __goOffline(self, avId):
        if self.online.pop(avId, 0) == 0:
            return
        self.notify.info('%s offline' % avId)

        def got(friends):
            for fid in friends or []:
                if fid in self.online:
                    self.sendUpdateToAvatarId(fid, 'friendOffline', [avId])
        self.__friendsOf(avId, got)

    # ---- the friends list (2013 CLIENT_GET_FRIEND_LIST_RESP) -------------------------
    def requestFriendsList(self):
        sender = self.air.getMsgSender()
        avId, accountId = sender & 0xFFFFFFFF, sender >> 32
        if avId and accountId:
            if avId not in self.online:
                self.__goOnline(avId, accountId)
            self.sendList(avId, accountId)

    def sendList(self, avId, accountId):
        def gotFriends(friends):
            # no stored list (a toon that never made a friend) is an empty list, not an error
            friends = friends or []
            entries = {}

            def finish():
                dg = PyDatagram()
                dg.addUint8(0)
                known = [f for f in friends if entries.get(f)]
                dg.addUint16(len(known))
                for fid in known:
                    name, dna, petId = entries[fid]
                    dg.addUint32(fid)
                    dg.addString(name)
                    dg.addBlob(dna)
                    dg.addUint32(petId)
                self.sendUpdateToAvatarId(avId, 'friendsList', [dg.getMessage()])
                for fid in known:
                    self.__declare(accountId, fid)
                    if fid in self.online:
                        self.__tellOnline(avId, fid)

            if not friends:
                finish()
                return
            for fid in friends:
                def got(dclass, fields, fid=fid):
                    if dclass == self.__toonClass() and fields:
                        entries[fid] = (fields.get('setName', ('',))[0], fields.get('setDNAString', (b'',))[0],
                                        fields.get('setPetId', (0,))[0])
                    else:
                        entries[fid] = None
                    if len(entries) == len(friends):
                        finish()
                self.air.dbInterface.queryObject(self.air.dbId, fid, got, dclass=self.__toonClass(),
                                                 fieldNames=FRIEND_FIELDS)
        self.__friendsOf(avId, gotFriends)

    def friendsMade(self, avIdA, avIdB):
        """The AI's FriendManager just wrote both lists: refresh both online sides."""
        if not self.__internal():
            return
        for me in (avIdA, avIdB):
            accountId = self.online.get(me)
            if accountId:
                self.sendList(me, accountId)

    def removeFriend(self, friendId):
        avId = self.air.getAvatarIdFromSender()
        if not avId or not friendId:
            return
        for me, other in ((avId, friendId), (friendId, avId)):
            def got(dclass, fields, me=me, other=other):
                if dclass != self.__toonClass() or not fields:
                    return
                newList = [e for e in fields.get('setFriendsList', ([],))[0] if e[0] != other]
                # through the State Server: the DB, the AI's copy and the owner all follow
                dg = self.__toonClass().aiFormatUpdate('setFriendsList', me, me, self.air.ourChannel, [newList])
                self.air.send(dg)
            self.air.dbInterface.queryObject(self.air.dbId, me, got, dclass=self.__toonClass(),
                                             fieldNames=('setFriendsList',))
        # no friendOffline here: the client would print "<name> has logged out.",
        # which the 2013 server never said for a removed friend

    # ---- Details (2013 CLIENT_GET_AVATAR_DETAILS_RESP) --------------------------------
    def requestAvatarDetails(self, avId):
        requester = self.air.getAvatarIdFromSender()
        if not requester:
            return

        def got(dclass, fields):
            if dclass != self.__toonClass() or not fields:
                self.sendUpdateToAvatarId(requester, 'avatarDetails', [avId, 1, b''])
                return
            self.sendUpdateToAvatarId(requester, 'avatarDetails', [avId, 0, self.packRequired(dclass, fields)])
        self.air.dbInterface.queryObject(self.air.dbId, avId, got)

    @staticmethod
    def packRequired(dclass, fields):
        """Every required field in .dc order, as the 2013 server sent them: the stored
        value where the database has one, else the field's default."""
        out = b''
        for i in range(dclass.getNumInheritedFields()):
            f = dclass.getInheritedField(i)
            if f.asMolecularField() is not None or not f.isRequired():
                continue
            value = fields.get(f.getName())
            if value is None:
                out += f.getDefaultValue()
                continue
            packer = DCPacker()
            packer.beginPack(f)
            f.packArgs(packer, value)
            if not packer.endPack():
                out += f.getDefaultValue()
                continue
            out += packer.getBytes()
        return out
