"""Client half of TTFriendsManagerUD (global id 4666): the 2013 friends list, online
dots and avatar details, fed to the 2013 handlers in ToontownClientRepository
exactly as the OTP server's CLIENT_* messages were (2013 restore, CHANGES.md)."""
from direct.directnotify import DirectNotifyGlobal
from direct.distributed.DistributedObjectGlobal import DistributedObjectGlobal
from direct.distributed.PyDatagram import PyDatagram
from direct.distributed.PyDatagramIterator import PyDatagramIterator


class TTFriendsManager(DistributedObjectGlobal):
    notify = DirectNotifyGlobal.directNotify.newCategory('TTFriendsManager')

    def d_requestFriendsList(self):
        self.sendUpdate('requestFriendsList', [])

    def d_removeFriend(self, friendId):
        self.sendUpdate('removeFriend', [friendId])

    def d_requestAvatarDetails(self, avId):
        self.sendUpdate('requestAvatarDetails', [avId])

    @staticmethod
    def __iterator(dg):
        return PyDatagramIterator(dg)

    def friendsList(self, blob):
        dg = PyDatagram(blob)
        self.cr.handleGetFriendsList(self.__iterator(dg))

    def friendOnline(self, avId, commonChatFlags, whitelistChatFlags):
        dg = PyDatagram()
        dg.addUint32(avId)
        dg.addUint8(commonChatFlags)
        dg.addUint8(whitelistChatFlags)
        self.cr.handleFriendOnline(self.__iterator(dg))

    def friendOffline(self, avId):
        dg = PyDatagram()
        dg.addUint32(avId)
        self.cr.handleFriendOffline(self.__iterator(dg))

    def avatarDetails(self, avId, returnCode, blob):
        dg = PyDatagram()
        dg.addUint32(avId)
        dg.addUint8(returnCode)
        dg.appendData(blob)
        self.cr.handleGetAvatarDetailsResp(self.__iterator(dg))
