import json
import os
import time

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.DistributedObjectGlobalUD import DistributedObjectGlobalUD
from panda3d.core import ConfigVariableString
from toontown.toonbase import ToontownGlobals

# Ported from the Disney leak (toontownretro/toontown-org 7d3b71f, toontown/src/uberdog/
# DistributedMailManagerUD.py). Disney kept simple mail in MySQL (ttMaildb); this keeps it in a
# JSON file, the same way DistributedPartyManagerUD's PartyDb keeps parties.
# Simple mail was a Disney test feature: nothing in the 2013 game sent it and neither the leak's
# UberDOG nor the client generated this manager. It is ported so the shell is filled, and stays
# ungenerated (vanilla).


class MailDb:
    """ttMaildb's putMail/getMail on a JSON file."""

    def __init__(self):
        self.filePath = ConfigVariableString('maildb-local-file', 'astron/databases/mail.json').getValue()
        self.mail = {'nextId': 1, 'items': []}
        if os.path.exists(self.filePath):
            self.load()
        else:
            self.save()

    def load(self):
        with open(self.filePath, 'r') as file:
            self.mail = json.load(file)

    def save(self):
        with open(self.filePath, 'w') as file:
            json.dump(self.mail, file)

    def putMail(self, recipientId, senderId, message):
        self.load()
        self.mail['items'].append({'messageId': self.mail['nextId'],
                                   'recipientId': recipientId,
                                   'senderId': senderId,
                                   'message': message,
                                   'lastupdate': time.time(),
                                   'readFlag': 0})
        self.mail['nextId'] += 1
        self.save()

    def getMail(self, recipientId):
        self.load()
        return [item for item in self.mail['items'] if item['recipientId'] == recipientId]


class DistributedMailManagerUD(DistributedObjectGlobalUD):
    notify = DirectNotifyGlobal.directNotify.newCategory('DistributedMailManagerUD')

    def __init__(self, air):
        DistributedObjectGlobalUD.__init__(self, air)
        self.mailDB = MailDb()

    def announceGenerate(self):
        DistributedObjectGlobalUD.announceGenerate(self)
        self.accept('avatarOnlinePlusAccountInfo', self.avatarOnlinePlusAccountInfo, [])

    def sendSimpleMail(self, senderId, recipientId, simpleText):
        """Testing to send a simple text message to another."""
        self.notify.debug("sendSimpleMail( senderId=%d, recipientId=%d, simpleText='%s')" % (senderId, recipientId, simpleText))
        self.mailDB.putMail(recipientId, senderId, simpleText)

    def avatarLoggedIn(self, avatarId):
        """Handle an avatar just logging in: send it all its mail."""
        result = self.mailDB.getMail(avatarId)
        self.notify.debug('mailDB.getMail returned %d items for avatarID %d' % (len(result), avatarId))
        formattedMail = []
        numOld = 0
        numNew = 0
        for item in result:
            date = time.localtime(item['lastupdate'])
            formattedMail.append((item['messageId'], item['senderId'], date.tm_year, date.tm_mon, date.tm_mday, item['message']))
            if item['readFlag']:
                numOld += 1
            else:
                numNew += 1

        self.air.sendUpdateToDoId('DistributedToon', 'setMail', avatarId, [formattedMail])
        self.air.sendUpdateToDoId('DistributedToon', 'setNumMailItems', avatarId, [len(result)])

        simpleMailNotify = ToontownGlobals.NoItems
        if numNew:
            simpleMailNotify = ToontownGlobals.NewItems
        elif numOld:
            simpleMailNotify = ToontownGlobals.OldItems
        self.air.sendUpdateToDoId('DistributedToon', 'setSimpleMailNotify', avatarId, [simpleMailNotify])

    def avatarOnlinePlusAccountInfo(self, avatarId, accountId, playerName, playerNameApproved, openChatEnabled,
                                    createFriendsWithChat, chatCodeCreation):
        # The OTP server announced a login. Astron never sends this (DistributedPartyManagerUD waits on it too).
        self.avatarLoggedIn(avatarId)
