import time

from direct.directnotify import DirectNotifyGlobal

from otp.distributed.DistributedDirectoryAI import DistributedDirectoryAI
from otp.distributed.OtpDoGlobals import *
from toontown.distributed.ToontownInternalRepository import ToontownInternalRepository
from toontown.parties.ToontownTimeManager import ToontownTimeManager


# TODO: Remove Astron dependence.

class ToontownUDRepository(ToontownInternalRepository):
    notify = DirectNotifyGlobal.directNotify.newCategory('ToontownUDRepository')

    def __init__(self, baseChannel, serverId):
        ToontownInternalRepository.__init__(self, baseChannel, serverId, dcSuffix='UD')
        self.toontownTimeManager = None
        self.astronLoginManager = None

    def handleConnected(self):
        ToontownInternalRepository.handleConnected(self)

        # Create our root object.
        self.notify.info('Creating root object (%d)...' % self.getGameDoId())
        rootObj = DistributedDirectoryAI(self)
        rootObj.generateWithRequiredAndId(self.getGameDoId(), 0, 0)

        # Create our local objects.
        self.notify.info('Creating local objects...')
        self.createLocals()

        # Create our global objects.
        self.notify.info('Creating global objects...')
        self.createGlobals()

        self.notify.info('UberDOG server is ready.')

    def createLocals(self):
        # Create our Toontown time manager...
        self.toontownTimeManager = ToontownTimeManager(serverTimeUponLogin=int(time.time()),
                                                       globalClockRealTimeUponLogin=globalClock.getRealTime())

    def createGlobals(self):
        if __astron__:
            # Create our Astron login manager...
            self.astronLoginManager = self.generateGlobalObject(OTP_DO_ID_ASTRON_LOGIN_MANAGER, 'AstronLoginManager')
            # 2013 restore: friends list, online status and avatar details.
            self.ttFriendsManager = self.generateGlobalObject(OTP_DO_ID_TT_FRIENDS_MANAGER, 'TTFriendsManager')
            # create our DistributedPartyManagerUD
            self.partyManager = self.generateGlobalObject(OTP_DO_ID_TOONTOWN_PARTY_MANAGER, 'DistributedPartyManager')
            # Catalog gifts (the toon's gift schedule).
            self.deliveryManager = self.generateGlobalObject(OTP_DO_ID_TOONTOWN_DELIVERY_MANAGER, 'DistributedDeliveryManager')
            # Code redemption awards (the toon's award schedule).
            self.awardManager = self.generateGlobalObject(OTP_DO_ID_TOONTOWN_AWARD_MANAGER, 'AwardManager')
            # 2013 restore: the data stores (Trick-or-Treat / scavenger hunt progress); the AI already talks to it.
            self.dataStoreManager = self.generateGlobalObject(OTP_DO_ID_TOONTOWN_TEMP_STORE_MANAGER, 'DistributedDataStoreManager')
            if config.GetBool('want-code-redemption', 1):
                self.codeRedemptionManager = self.generateGlobalObject(OTP_DO_ID_TOONTOWN_CODE_REDEMPTION_MANAGER, 'TTCodeRedemptionMgr')
