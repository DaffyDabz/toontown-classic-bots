import time

from direct.directnotify.DirectNotifyGlobal import directNotify
from direct.distributed.DistributedObjectGlobalUD import DistributedObjectGlobalUD
from direct.showbase.PythonUtil import SerialNumGen, ScratchPad
from toontown.catalog import CatalogItem
from toontown.catalog import CatalogItemTypes
from toontown.catalog import CatalogClothingItem
from toontown.catalog import CatalogFurnitureItem
from toontown.catalog import CatalogChatItem
from toontown.catalog import CatalogEmoteItem
from toontown.catalog import CatalogBeanItem
from toontown.catalog import CatalogWallpaperItem
from toontown.catalog import CatalogWindowItem
from toontown.catalog import CatalogFlooringItem
from toontown.catalog import CatalogWainscotingItem
from toontown.catalog import CatalogMouldingItem
from toontown.catalog import CatalogPetTrickItem
from toontown.catalog import CatalogRentalItem
from toontown.catalog import CatalogAnimatedFurnitureItem
from toontown.rpc import AwardManagerConsts
from toontown.toonbase import ToontownGlobals
from toontown.uberdog.DistributedDeliveryManagerUD import GiftToon, DeliveryStore, WrittenBlobLifetime

# Ported from the Disney leak (toontownretro/toontown-org 7d3b71f, toontown/src/rpc/AwardManagerUD.py).
# Only the dc path is ported: giveAwardToToon from the code redemption UberDOG, answered with
# giveAwardToToonResult. The HTML award pages (awardMgr/awardGive) are not. Disney loaded the toon
# with a GetToonsRequest (AsyncRequest into DistributedToonUD); on Astron the toon is read with
# dbInterface.queryObject into the delivery manager's GiftToon stand-in and the award schedule is
# written with a field update to the toon, one read-modify-write per toon at a time.

WrongGenderStr = "wrong gender"
JellybeanRewardValues = (1, 5, 10, 15, 20, 25, 50, 100, 150, 200, 250, 300, 500, 750, 1000)  # 300: added for this server's own codes

GiveAfterDelayTime = 1
GiveImmediately = 2
TryToRemove = 3
GiveAfterOneMinute = 4
NukeAllAwards = 5


class AwardManagerUD(DistributedObjectGlobalUD):
    """
    Uberdog object for making promo awards to Toons
    """
    notify = directNotify.newCategory('AwardManagerUD')

    def __init__(self, air):
        DistributedObjectGlobalUD.__init__(self, air)
        self.air = air
        self._dcRequestSerialGen = SerialNumGen(1)
        self._dcId2info = {}
        # avId -> jobs waiting to read-modify-write that toon's award schedule, one at a time.
        self.avJobs = {}
        # avId -> (blob, time) of the award schedule we last wrote.
        self.writtenBlobs = {}

    # One read-modify-write of a toon at a time (the delivery manager's pattern).

    def __queueJob(self, avId, job):
        jobs = self.avJobs.setdefault(avId, [])
        jobs.append(job)
        if len(jobs) == 1:
            self.__startJob(avId)

    def __startJob(self, avId):
        self.air.dbInterface.queryObject(self.air.dbId, avId,
                                         lambda dclass, fields: self.__gotToon(avId, dclass, fields))

    def __gotToon(self, avId, dclass, fields):
        job = self.avJobs[avId][0]
        if fields is None:
            toon = None
            error = AwardManagerConsts.GiveAwardErrors.UnknownToon
        elif dclass != self.air.dclassesByName['DistributedToonUD']:
            toon = None
            error = AwardManagerConsts.GiveAwardErrors.NonToon
        else:
            written = self.writtenBlobs.get(avId)
            if written and time.time() - written[1] < WrittenBlobLifetime:
                fields = dict(fields)
                fields['setAwardSchedule'] = (written[0],)
            toon = GiftToon(avId, fields)
            error = None
        try:
            job(toon, error)
        finally:
            jobs = self.avJobs[avId]
            jobs.pop(0)
            if jobs:
                self.__startJob(avId)
            else:
                del self.avJobs[avId]

    def _getCatalogItemObj(self, itemType, itemIndex):
        itemObj = None
        if itemType == CatalogItemTypes.CLOTHING_ITEM:
            clothingNumber = itemIndex
            # for now always the first color choice
            itemObj = CatalogClothingItem.CatalogClothingItem(clothingNumber, 0)
            itemObj.giftTag = 0
            itemObj.giftCode = 1
        elif itemType == CatalogItemTypes.FURNITURE_ITEM:
            furnitureNumber = itemIndex
            itemObj = CatalogFurnitureItem.CatalogFurnitureItem(furnitureNumber, colorOption=0)
        elif itemType == CatalogItemTypes.CHAT_ITEM:
            chatIndex = itemIndex
            itemObj = CatalogChatItem.CatalogChatItem(chatIndex)
        elif itemType == CatalogItemTypes.EMOTE_ITEM:
            emoteIndex = itemIndex
            itemObj = CatalogEmoteItem.CatalogEmoteItem(emoteIndex)
        elif itemType == CatalogItemTypes.BEAN_ITEM:
            numBeans = itemIndex
            if not numBeans in JellybeanRewardValues:
                # the leak asserted here, so the beans were never rewarded
                self.air.writeServerEvent('suspicious', 0, 'giving %s beans' % numBeans)
                return None
            itemObj = CatalogBeanItem.CatalogBeanItem(numBeans)
        elif itemType == CatalogItemTypes.WALLPAPER_ITEM:
            wallPaperNumber = itemIndex
            itemObj = CatalogWallpaperItem.CatalogWallpaperItem(wallPaperNumber, colorIndex=0)
        elif itemType == CatalogItemTypes.WINDOW_ITEM:
            windowNumber = itemIndex
            itemObj = CatalogWindowItem.CatalogWindowItem(windowNumber, placement=0)
        elif itemType == CatalogItemTypes.FLOORING_ITEM:
            flooringNumber = itemIndex
            itemObj = CatalogFlooringItem.CatalogFlooringItem(flooringNumber, colorIndex=0)
        elif itemType == CatalogItemTypes.MOULDING_ITEM:
            mouldingNumber = itemIndex
            itemObj = CatalogMouldingItem.CatalogMouldingItem(mouldingNumber, colorIndex=0)
        elif itemType == CatalogItemTypes.WAINSCOTING_ITEM:
            wainscotingNumber = itemIndex
            itemObj = CatalogWainscotingItem.CatalogWainscotingItem(wainscotingNumber, colorIndex=0)
        elif itemType == CatalogItemTypes.PET_TRICK_ITEM:
            trickId = itemIndex
            itemObj = CatalogPetTrickItem.CatalogPetTrickItem(trickId)
        elif itemType == CatalogItemTypes.RENTAL_ITEM:
            # TODO since all we offer so far is 48 hours of cannons, values pulled for CatalogGenerator
            # do something else if we have different durations
            rentalType = itemIndex
            itemObj = CatalogRentalItem.CatalogRentalItem(rentalType, 2880, 1000)
        elif itemType == CatalogItemTypes.ANIMATED_FURNITURE_ITEM:
            furnitureNumber = itemIndex
            itemObj = CatalogAnimatedFurnitureItem.CatalogAnimatedFurnitureItem(furnitureNumber, colorOption=0)
        return itemObj

    def checkGender(self, toon, catalogItem):
        """Return None if everything is ok and we don't have mismatched sex."""
        if ((catalogItem.forBoysOnly() and toon.dna.getGender() == 'f') or (catalogItem.forGirlsOnly() and toon.dna.getGender() == 'm')):
            return ToontownGlobals.P_WillNotFit
        return None

    def checkGiftable(self, toon, catalogItem):
        """Return None if everything is ok and the item is giftable."""
        if not catalogItem.isGift():
            return ToontownGlobals.P_NotAGift
        return None

    def checkFullMailbox(self, toon, catalogItem):
        """Return None if he has space in his mailbox."""
        rAv = toon
        result = None
        if len(rAv.awardMailboxContents) + len(rAv.onAwardOrder) >= ToontownGlobals.MaxMailboxContents:
            if len(rAv.awardMailboxContents) == 0:
                result = ToontownGlobals.P_OnAwardOrderListFull
            else:
                result = ToontownGlobals.P_AwardMailboxFull
        return result

    def checkDuplicate(self, toon, catalogItem):
        """Return None if he doesn't have this item yet. an error code from GiveAwardErrors otherwise"""
        result = None
        checkDup = toon.checkForDuplicateItem(catalogItem)
        if checkDup == ToontownGlobals.P_ItemInMailbox:
            result = AwardManagerConsts.GiveAwardErrors.AlreadyInMailbox
        elif checkDup == ToontownGlobals.P_ItemOnGiftOrder:
            result = AwardManagerConsts.GiveAwardErrors.AlreadyInGiftQueue
        elif checkDup == ToontownGlobals.P_ItemOnOrder:
            result = AwardManagerConsts.GiveAwardErrors.AlreadyInOrderedQueue
        elif checkDup == ToontownGlobals.P_ItemInCloset:
            result = AwardManagerConsts.GiveAwardErrors.AlreadyInCloset
        elif checkDup == ToontownGlobals.P_ItemAlreadyWorn:
            result = AwardManagerConsts.GiveAwardErrors.AlreadyBeingWorn
        elif checkDup == ToontownGlobals.P_ItemInAwardMailbox:
            result = AwardManagerConsts.GiveAwardErrors.AlreadyInAwardMailbox
        elif checkDup == ToontownGlobals.P_ItemOnAwardOrder:
            result = AwardManagerConsts.GiveAwardErrors.AlreadyInThirtyMinuteQueue
        elif checkDup == ToontownGlobals.P_ItemInMyPhrases:
            result = AwardManagerConsts.GiveAwardErrors.AlreadyInMyPhrases
        elif checkDup == ToontownGlobals.P_ItemInPetTricks:
            result = AwardManagerConsts.GiveAwardErrors.AlreadyKnowDoodleTraining
        elif checkDup:
            # HACK: store the catalog error on self
            self._catalogError = checkDup
            result = AwardManagerConsts.GiveAwardErrors.GenericAlreadyHaveError
        return result

    def validateItem(self, toon, catalogItem):
        """Returns (True, AwardManagerConsts.GiveAwardErrors.Success) if everything is ok, otherwise returns (False,<error reason>)"""
        retcode = self.checkGender(toon, catalogItem)
        if retcode:
            return (False, AwardManagerConsts.GiveAwardErrors.WrongGender)
        retcode = self.checkGiftable(toon, catalogItem)
        if retcode:
            return (False, AwardManagerConsts.GiveAwardErrors.NotGiftable)
        retcode = self.checkFullMailbox(toon, catalogItem)
        if retcode:
            return (False, AwardManagerConsts.GiveAwardErrors.FullAwardMailbox)
        result = self.checkDuplicate(toon, catalogItem)
        if result:
            return (False, result)
        return (True, "success")

    def giveItemToToon(self, toon, catalogItem, specialEventId, specialCommands):
        """All checks passed, give the toon the item. Returns True if all ok"""
        # The dc path always gives after one minute: into the award schedule, which the toon's
        # AI (setAwardSchedule) moves to the award mailbox when it is due.
        catalogItem.specialEventId = specialEventId
        now = int(time.time() / 60 + 0.5)
        delay = 1
        future = now + delay
        curOnAwardOrderList = toon.onAwardOrder
        catalogItem.deliveryDate = future
        curOnAwardOrderList.append(catalogItem)
        newBlob = curOnAwardOrderList.getBlob(store=DeliveryStore)
        self.writtenBlobs[toon.doId] = (newBlob, time.time())
        self.air.sendUpdateToDoId(
            "DistributedToon",
            "setAwardSchedule", toon.doId, [newBlob])
        return True

    def giveAwardToToon(self, context, replyToDoId, replyToClass, avId, awardType, awardItemId):
        self.air.writeServerEvent('giveAwardCodeRequest', avId, '%s|%s' % (str(awardType), str(awardItemId)))
        dcId = next(self._dcRequestSerialGen)
        self._dcId2info[dcId] = ScratchPad(replyToClass=replyToClass,
                                           replyToDoId=replyToDoId,
                                           context=context)
        catalogItem = self._getCatalogItemObj(awardType, awardItemId)
        if catalogItem is None:
            self.sendGiveAwardToToonReply(dcId, AwardManagerConsts.GiveAwardErrors.UnknownError)
            return
        specialEventId = 1
        specialCommands = GiveAfterOneMinute

        def job(toon, error):
            if toon is None:
                errorCode = error
            else:
                success, error = self.validateItem(toon, catalogItem)
                if success:
                    success = self.giveItemToToon(toon, catalogItem, specialEventId, specialCommands)
                    if success:
                        errorCode = AwardManagerConsts.GiveAwardErrors.Success
                    else:
                        errorCode = AwardManagerConsts.GiveAwardErrors.UnknownError
                else:
                    errorCode = error
            self.air.writeServerEvent('giveAwardResults', 0, "%s|%s|%s" % ('', str(catalogItem), str({avId: int(errorCode)})))
            self.sendGiveAwardToToonReply(dcId, errorCode)

        self.__queueJob(avId, job)

    def sendGiveAwardToToonReply(self, dcId, result):
        info = self._dcId2info.pop(dcId)
        replyToClass = info.replyToClass
        # the leak passed 'TTCodeRedemptionMgrUD'; the repository adds its own dc suffix
        for suffix in ('UD', 'AI'):
            if replyToClass.endswith(suffix):
                replyToClass = replyToClass[:-len(suffix)]
        self.air.sendUpdateToDoId(replyToClass, "giveAwardToToonResult",
                                  info.replyToDoId, [info.context, int(result)])
