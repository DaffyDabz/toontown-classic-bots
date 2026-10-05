"""FriendManagerAI - the AI half of the 2013 avatar-friends handshake (uberdog id 4501).

Ported from the author's modded Toontown server (otp/friends/FriendManagerAI.py);
see CHANGES.md. The client's FriendInviter/FriendInvitee/FriendSecret already speak
this protocol unchanged:

    inviter client            AI (this object)             invitee client
    friendQuery(inviteeId) -> validate, open context
                              inviteeFriendQuery(inviterId, name, dna, ctx) ->
    <- friendConsidering(code, ctx)  <- inviteeFriendConsidering(code, ctx)
    <- friendResponse(code, ctx)     <- inviteeFriendResponse(code, ctx)
                              on yes: write both friends lists

Without this object the client's first friend request (or secret request) went to
doId 4501, which did not exist, and Astron booted the client (error 117).
"""

import json
import os
import random
import time

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.DistributedObjectGlobalAI import DistributedObjectGlobalAI

from otp.otpbase import OTPGlobals

# friendConsidering(code) -> inviter (FriendInviter.py)
CONSIDERING_NOT_AVAILABLE = 0
CONSIDERING_ALREADY_FRIENDS = 2
CONSIDERING_SELF = 3
CONSIDERING_OTHER_TOO_MANY = 13
# friendResponse(code) -> inviter
RESPONSE_NO = 0
RESPONSE_YES = 1
RESPONSE_OTHER_TOO_MANY = 3
# submitSecretResponse(result) -> submitter (FriendSecret.py)
SECRET_UNKNOWN = 0
SECRET_SUCCESS = 1
SECRET_FULL = 2

INVITE_TIMEOUT = 90.0
SECRET_LIFETIME = 60 * 60 * 48  # secrets expired after two days
SECRET_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
SECRET_LENGTH = 6


class FriendManagerAI(DistributedObjectGlobalAI):
    notify = DirectNotifyGlobal.directNotify.newCategory('FriendManagerAI')

    def __init__(self, air):
        DistributedObjectGlobalAI.__init__(self, air)
        self.nextContext = 1
        self.invites = {}
        self.pendingByAvId = {}
        self.secretsFile = config.GetString('friend-secrets-file', 'astron/databases/friend_secrets.json')
        self.secrets = {}
        self._loadSecrets()

    def _toon(self, avId):
        av = self.air.doId2do.get(avId)
        if av is None or not hasattr(av, 'friendsList'):
            return None
        return av

    def _sendToAv(self, avId, field, args):
        self.sendUpdateToAvatarId(avId, field, args)

    @staticmethod
    def _isFriends(avA, avB):
        for friendId, flags in avA.getFriendsList():
            if friendId == avB.doId:
                return True
        return False

    @staticmethod
    def _friendFlags(av, friendId):
        for fid, flags in av.getFriendsList():
            if fid == friendId:
                return flags
        return None

    def friendQuery(self, inviteeId):
        inviterId = self.air.getAvatarIdFromSender()
        inviter = self._toon(inviterId) if inviterId else None
        if inviter is None:
            return
        if inviteeId == inviterId:
            self._sendToAv(inviterId, 'friendConsidering', [CONSIDERING_SELF, 0])
            return
        invitee = self._toon(inviteeId)
        if invitee is None:
            self._sendToAv(inviterId, 'friendConsidering', [CONSIDERING_NOT_AVAILABLE, 0])
            return
        if self._isFriends(inviter, invitee):
            self._sendToAv(inviterId, 'friendConsidering', [CONSIDERING_ALREADY_FRIENDS, 0])
            return
        if len(invitee.getFriendsList()) >= OTPGlobals.MaxFriends or \
                len(inviter.getFriendsList()) >= OTPGlobals.MaxFriends:
            self._sendToAv(inviterId, 'friendConsidering', [CONSIDERING_OTHER_TOO_MANY, 0])
            return
        if inviterId in self.pendingByAvId or inviteeId in self.pendingByAvId:
            self._sendToAv(inviterId, 'friendConsidering', [CONSIDERING_NOT_AVAILABLE, 0])
            return
        context = self.nextContext
        self.nextContext += 1
        self.invites[context] = {'inviterId': inviterId, 'inviteeId': inviteeId, 'state': 'query'}
        self.pendingByAvId[inviterId] = context
        self.pendingByAvId[inviteeId] = context
        taskMgr.doMethodLater(INVITE_TIMEOUT, self._inviteTimeout, self._timeoutTaskName(context),
                              extraArgs=[context])
        self.notify.info('friendQuery: %s asks %s (context %s)' % (inviterId, inviteeId, context))
        self._sendToAv(inviteeId, 'inviteeFriendQuery',
                       [inviterId, inviter.getName(), inviter.getDNAString(), context])

    def inviteeFriendConsidering(self, yesNo, context):
        invite = self._validInvite(context, expectInvitee=True)
        if invite is None or invite['state'] == 'cancelled':
            return
        self._sendToAv(invite['inviterId'], 'friendConsidering', [yesNo, context])
        if yesNo == 1:
            invite['state'] = 'considering'
        else:
            self._cleanupInvite(context)

    def inviteeFriendResponse(self, response, context):
        invite = self._validInvite(context, expectInvitee=True)
        if invite is None:
            return
        if invite['state'] == 'cancelled':
            self._cleanupInvite(context)
            return
        inviterId = invite['inviterId']
        if response == 1:
            ok, code = self.makeFriends(inviterId, invite['inviteeId'])
            self._sendToAv(inviterId, 'friendResponse', [RESPONSE_YES if ok else code, context])
        elif response == 3:
            self._sendToAv(inviterId, 'friendResponse', [RESPONSE_OTHER_TOO_MANY, context])
        else:
            self._sendToAv(inviterId, 'friendResponse', [RESPONSE_NO, context])
        self._cleanupInvite(context)

    def cancelFriendQuery(self, context):
        invite = self.invites.get(context)
        if invite is None or self.air.getAvatarIdFromSender() != invite['inviterId']:
            return
        invite['state'] = 'cancelled'
        self._sendToAv(invite['inviteeId'], 'inviteeCancelFriendQuery', [context])

    def inviteeAcknowledgeCancel(self, context):
        if self._validInvite(context, expectInvitee=True) is not None:
            self._cleanupInvite(context)

    def _validInvite(self, context, expectInvitee=False):
        invite = self.invites.get(context)
        if invite is None:
            return None
        expected = invite['inviteeId'] if expectInvitee else invite['inviterId']
        if self.air.getAvatarIdFromSender() != expected:
            return None
        return invite

    def _inviteTimeout(self, context):
        invite = self.invites.get(context)
        if invite is None:
            return
        if invite['state'] != 'cancelled':
            self._sendToAv(invite['inviterId'], 'friendResponse', [RESPONSE_NO, context])
            self._sendToAv(invite['inviteeId'], 'inviteeCancelFriendQuery', [context])
        self._cleanupInvite(context)

    def _cleanupInvite(self, context):
        invite = self.invites.pop(context, None)
        if invite is None:
            return
        taskMgr.remove(self._timeoutTaskName(context))
        for avId in (invite['inviterId'], invite['inviteeId']):
            if self.pendingByAvId.get(avId) == context:
                del self.pendingByAvId[avId]

    def _timeoutTaskName(self, context):
        return self.uniqueName('friendInvite-%s' % context)

    def makeFriends(self, avIdA, avIdB, trueFriend=False):
        avA = self._toon(avIdA)
        avB = self._toon(avIdB)
        if avA is None or avB is None:
            return False, RESPONSE_NO
        alreadyFriends = self._isFriends(avA, avB)
        if alreadyFriends and not trueFriend:
            return True, RESPONSE_YES
        if not alreadyFriends and (len(avA.getFriendsList()) >= OTPGlobals.MaxFriends or
                                   len(avB.getFriendsList()) >= OTPGlobals.MaxFriends):
            return False, RESPONSE_OTHER_TOO_MANY
        newFlags = 0
        if trueFriend:
            newFlags = (self._friendFlags(avA, avIdB) or 0) | OTPGlobals.FriendChat
        avA.extendFriendsList(avIdB, newFlags)
        avB.extendFriendsList(avIdA, newFlags)
        avA.d_setFriendsList(avA.getFriendsList())
        avB.d_setFriendsList(avB.getFriendsList())
        # the AI never hears its own setFriendsList back (the State Server skips the sender), so the FriendQuest
        # hook in DistributedToonAI.setFriendsList never ran: tell the quest manager here
        qm = getattr(self.air, 'questManager', None)
        if qm is not None and not alreadyFriends:
            qm.toonMadeFriend(avA, avB)
            qm.toonMadeFriend(avB, avA)
        self.air.writeServerEvent('friending', avIdA, '%s|%s|flags=%s' % (avIdA, avIdB, newFlags))
        self._tellFriendsList(avIdA, avIdB)
        return True, RESPONSE_YES

    def _tellFriendsList(self, avIdA, avIdB):
        # The 2013 server refreshed both friends lists (names, online dots) itself;
        # here the friends UberDOG does it (TTFriendsManagerUD.friendsMade).
        from otp.distributed.OtpDoGlobals import OTP_DO_ID_TT_FRIENDS_MANAGER
        dclass = self.air.dclassesByName.get('TTFriendsManager')
        if dclass is None:
            return
        self.air.send(dclass.aiFormatUpdate('friendsMade', OTP_DO_ID_TT_FRIENDS_MANAGER,
                                            OTP_DO_ID_TT_FRIENDS_MANAGER, self.air.ourChannel, [avIdA, avIdB]))

    def requestSecret(self):
        avId = self.air.getAvatarIdFromSender()
        if not avId or self._toon(avId) is None:
            return
        self._purgeExpiredSecrets()
        for code in [c for c, s in self.secrets.items() if s['ownerId'] == avId]:
            del self.secrets[code]
        code = self._newSecretCode()
        self.secrets[code] = {'ownerId': avId, 'created': time.time()}
        self._saveSecrets()
        self._sendToAv(avId, 'requestSecretResponse', [1, code])

    def submitSecret(self, secret):
        avId = self.air.getAvatarIdFromSender()
        av = self._toon(avId) if avId else None
        if av is None:
            return
        self._purgeExpiredSecrets()
        entry = self.secrets.get(secret.strip().lower())
        if entry is None or entry['ownerId'] == avId:
            self._sendToAv(avId, 'submitSecretResponse', [SECRET_UNKNOWN, 0])
            return
        ownerId = entry['ownerId']
        owner = self._toon(ownerId)
        if owner is None:
            self._sendToAv(avId, 'submitSecretResponse', [SECRET_UNKNOWN, 0])
            return
        if not self._isFriends(av, owner) and (len(av.getFriendsList()) >= OTPGlobals.MaxFriends or
                                               len(owner.getFriendsList()) >= OTPGlobals.MaxFriends):
            self._sendToAv(avId, 'submitSecretResponse', [SECRET_FULL, ownerId])
            return
        ok, _ = self.makeFriends(avId, ownerId, trueFriend=True)
        if not ok:
            self._sendToAv(avId, 'submitSecretResponse', [SECRET_UNKNOWN, 0])
            return
        del self.secrets[secret.strip().lower()]
        self._saveSecrets()
        self._sendToAv(avId, 'submitSecretResponse', [SECRET_SUCCESS, ownerId])

    def _newSecretCode(self):
        while True:
            code = ''.join(random.choice(SECRET_ALPHABET) for _ in range(SECRET_LENGTH))
            if code not in self.secrets:
                return code

    def _purgeExpiredSecrets(self):
        now = time.time()
        expired = [c for c, s in self.secrets.items() if now - s['created'] > SECRET_LIFETIME]
        for code in expired:
            del self.secrets[code]
        if expired:
            self._saveSecrets()

    def _loadSecrets(self):
        try:
            with open(self.secretsFile) as f:
                self.secrets = json.load(f).get('secrets', {})
        except FileNotFoundError:
            self.secrets = {}
        except Exception:
            self.notify.warning('could not read %s; starting empty' % self.secretsFile)
            self.secrets = {}

    def _saveSecrets(self):
        try:
            tmp = self.secretsFile + '.tmp'
            with open(tmp, 'w') as f:
                json.dump({'secrets': self.secrets}, f, indent=2)
            os.replace(tmp, self.secretsFile)
        except Exception:
            self.notify.warning('could not write %s' % self.secretsFile)
