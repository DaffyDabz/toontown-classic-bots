from direct.directnotify.DirectNotifyGlobal import directNotify
from direct.distributed.DistributedObjectAI import DistributedObjectAI
from direct.showbase.DirectObject import DirectObject
from direct.showbase.PythonUtil import SerialNumGen, ScratchPad, Functor, uniqueName
from direct.task import Task
from otp.distributed import OtpDoGlobals

# Ported from the Disney leak (toontownretro/toontown-org 7d3b71f,
# toontown/src/coderedemption/TTCodeRedemptionMgrAI.py). The stress test is left out.


class TTCRMAIRetryMgr(DirectObject):
    notify = directNotify.newCategory('TTCodeRedemptionMgrAI')

    MinRetryPeriod = 5
    RetryGrowMult = 1.1

    def __init__(self, air, codeRedemptionMgr):
        self.air = air
        self._codeRedemptionMgr = codeRedemptionMgr
        self._serialGen = SerialNumGen()
        self._retryPeriod = self.MinRetryPeriod
        self._redemptions = {}

    def addRedemption(self, avId, context, code):
        serial = next(self._serialGen)
        self._redemptions[serial] = ScratchPad(avId=avId, context=context, code=code, attemptNum=0)
        self._doRedemption(serial, True)

    def resolveRedemption(self, serial, context, avId, result, awardMgrResult):
        if serial not in self._redemptions:
            self.notify.warning('unexpected redemption resolution: %s, %s, %s, %s, %s' % (
                serial, context, avId, result, awardMgrResult))
            return
        info = self._redemptions.pop(serial)
        info.doLater.remove()
        self._retryPeriod = self.MinRetryPeriod
        self._codeRedemptionMgr.sendUpdateToAvatarId(avId, 'redeemCodeResult', [context, result, awardMgrResult])

    def _doRedemption(self, serial, directCall, task=None):
        info = self._redemptions.get(serial)
        info.attemptNum += 1
        if info.attemptNum > 1:
            self._retryPeriod = max(self.MinRetryPeriod, self._retryPeriod * self.RetryGrowMult)
            self.notify.info('code redemption retry #%s for %s: %s' % ((info.attemptNum - 1), info.avId, info.code))
        self.air.sendUpdateToDoId('TTCodeRedemptionMgr',
                                  'redeemCodeAiToUd',
                                  OtpDoGlobals.OTP_DO_ID_TOONTOWN_CODE_REDEMPTION_MANAGER,
                                  [serial, self._codeRedemptionMgr.doId, info.context, info.code, info.avId]
                                  )
        info.doLater = self.doMethodLater(self._retryPeriod, Functor(self._doRedemption, serial, False),
                                          uniqueName('CodeRedemptionRetry'))
        return Task.done

    def destroy(self):
        for info in self._redemptions.values():
            if hasattr(info, 'doLater'):
                info.doLater.remove()
        self._redemptions = {}
        self.ignoreAll()


class TTCodeRedemptionMgrAI(DistributedObjectAI):
    notify = directNotify.newCategory('TTCodeRedemptionMgrAI')

    def __init__(self, air):
        DistributedObjectAI.__init__(self, air)
        self._retryMgr = TTCRMAIRetryMgr(self.air, self)
        self.air.codeRedemptionManager = self

    def delete(self):
        self._retryMgr.destroy()
        DistributedObjectAI.delete(self)

    def redeemCode(self, context, code):
        # pass it on to the UD w/out checking the content of the parameters; offload the
        # CPU work to the code redemption UD
        avId = self.air.getAvatarIdFromSender()
        self._retryMgr.addRedemption(avId, context, code)

    def redeemCodeResultUdToAi(self, serial, context, avId, result, awardMgrResult):
        # pass it back to the toon
        self._retryMgr.resolveRedemption(serial, context, avId, result, awardMgrResult)
