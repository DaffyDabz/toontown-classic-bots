from direct.directnotify import DirectNotifyGlobal
from direct.distributed import DistributedObjectAI
from otp.otpbase import OTPGlobals
import time

EFFECT = OTPGlobals.CEGreenToon
DURATION = 1440 # minutes; Disney's blog: the green "lasted only for a day"
HOOD = 0 # everywhere

class DistributedGreenToonEffectMgrAI(DistributedObjectAI.DistributedObjectAI):
    """Green toon effect ai implementation. This object sits in zone 5819 (Eugene's
    Green Bean Jeans interior) and turns green anyone who says 'It's easy to be green!'
    during the Ides of March holiday. They may come back and say it again."""

    notify = DirectNotifyGlobal.directNotify.newCategory('DistributedGreenToonEffectMgrAI')

    def __init__(self, air):
        DistributedObjectAI.DistributedObjectAI.__init__(self, air)

    # do the event
    def addGreenToonEffect(self):
        avId = self.air.getAvatarIdFromSender()
        av = self.air.doId2do.get(avId)
        if not av:
            DistributedGreenToonEffectMgrAI.notify.warning(
                'Tried to add green toon effect to av %s, but they left' % avId)
        else:
            DistributedGreenToonEffectMgrAI.notify.info(
                'Activating green toon effect for av %s' % avId)
            expireTime = (int)(time.time() / 60 + 0.5) + DURATION
            av.b_setCheesyEffect(EFFECT, HOOD, expireTime)
