import time

from direct.directnotify import DirectNotifyGlobal
from direct.distributed.DistributedObjectGlobalUD import DistributedObjectGlobalUD
from panda3d.core import ConfigVariableDouble
from toontown.toonbase import ToontownGlobals
from toontown.catalog import CatalogItemList
from toontown.catalog import CatalogItem
from toontown.catalog import CatalogBeanItem
from toontown.catalog import CatalogItemTypes
from toontown.catalog import CatalogClothingItem
from toontown.toon import ToonDNA

# Ported from the Disney leak (toontownretro/toontown-org 7d3b71f, toontown/src/uberdog/
# DistributedDeliveryManagerUD.py). Disney loaded toons through OTP AsyncRequests into
# DistributedToonUD objects and kept gift blobs in an LRUlist. On Astron the toon's own
# giftSchedule db field is the store: it is read with dbInterface.queryObject and written with a
# field update to the toon (the DBSS writes db fields for an offline toon too).

DeliveryStore = CatalogItem.Customization | CatalogItem.DeliveryDate

# A field update reaches the database through the DBSS, a query goes to the database directly, so
# a query right after a write can read the old blob. Keep what we wrote this long and use it instead.
WrittenBlobLifetime = 10.0


class GiftToon:
    """The fields of a toon that the gift checks read (Disney's DistributedToonUD), from a query."""

    def __init__(self, doId, fields):
        self.doId = doId

        def args(name, default):
            return fields.get(name, default)

        def one(name, default):
            return args(name, (default,))[0]

        self.mailboxContents = CatalogItemList.CatalogItemList(one('setMailboxContents', b''), store=CatalogItem.Customization)
        self.awardMailboxContents = CatalogItemList.CatalogItemList(one('setAwardMailboxContents', b''), store=CatalogItem.Customization)
        self.onOrder = CatalogItemList.CatalogItemList(one('setDeliverySchedule', b''), store=DeliveryStore)
        self.onGiftOrder = CatalogItemList.CatalogItemList(one('setGiftSchedule', b''), store=DeliveryStore)
        self.onAwardOrder = CatalogItemList.CatalogItemList(one('setAwardSchedule', b''), store=DeliveryStore)
        monthly, weekly, back = args('setCatalog', (b'', b'', b''))
        self.monthlyCatalog = CatalogItemList.CatalogItemList(monthly)
        self.weeklyCatalog = CatalogItemList.CatalogItemList(weekly)
        self.backCatalog = CatalogItemList.CatalogItemList(back)
        self.clothesTopsList = list(one('setClothesTopsList', []))
        self.clothesBottomsList = list(one('setClothesBottomsList', []))
        self.hatList = list(one('setHatList', []))
        self.glassesList = list(one('setGlassesList', []))
        self.backpackList = list(one('setBackpackList', []))
        self.shoesList = list(one('setShoesList', []))
        self.hat = tuple(args('setHat', (0, 0, 0)))
        self.glasses = tuple(args('setGlasses', (0, 0, 0)))
        self.backpack = tuple(args('setBackpack', (0, 0, 0)))
        self.shoes = tuple(args('setShoes', (0, 0, 0)))
        self.emoteAccess = list(one('setEmoteAccess', []))
        self.customMessages = list(one('setCustomMessages', []))
        self.petTrickPhrases = list(one('setPetTrickPhrases', []))
        self.nametagStyle = one('setNametagStyle', 0)
        self.fishingRod = one('setFishingRod', 0)
        self.gardenSpecials = list(one('setGardenSpecials', []))
        self.gardenStarted = one('setGardenStarted', 0)
        self.maxAccessories = one('setMaxAccessories', 0)
        self.maxBankMoney = one('setMaxBankMoney', 0)
        self.maxClothes = one('setMaxClothes', 0)
        self.style = ToonDNA.ToonDNA()
        self.style.makeFromNetString(one('setDNAString', b''))
        self.dna = self.style
        self.name = one('setName', '')

    def getHat(self):
        return self.hat

    def getGlasses(self):
        return self.glasses

    def getBackpack(self):
        return self.backpack

    def getShoes(self):
        return self.shoes

    def getFishingRod(self):
        return self.fishingRod

    def getGardenSpecials(self):
        return self.gardenSpecials

    def getGardenStarted(self):
        return self.gardenStarted

    def getMaxAccessories(self):
        return self.maxAccessories

    def getMaxBankMoney(self):
        return self.maxBankMoney

    def getMaxClothes(self):
        return self.maxClothes

    def getStyle(self):
        return self.style

    def getName(self):
        return self.name

    # The award manager's duplicate check: Disney's DistributedToonUD.checkForDuplicateItem
    # (leak 7d3b71f, toontown/src/toon/DistributedToonUD.py) with its two clothing helpers.

    def checkForItemInCloset(self, clothingItem):
        """Returns None if the clothing item is not in the closet."""
        result = None
        clothingTypeInfo = CatalogClothingItem.ClothingTypes[clothingItem.clothingType]
        styleStr = clothingTypeInfo[1]
        if clothingItem.isShirt():
            shirtStyleInfo = ToonDNA.ShirtStyles[styleStr]
            topTex = shirtStyleInfo[0]
            sleeveTex = shirtStyleInfo[1]
            topTexColor = shirtStyleInfo[2][clothingItem.colorIndex][0]
            sleeveTexColor = shirtStyleInfo[2][clothingItem.colorIndex][1]
            for i in range(0, len(self.clothesTopsList), 4):
                if (self.clothesTopsList[i] == topTex and
                    self.clothesTopsList[i+1] == topTexColor and
                    self.clothesTopsList[i+2] == sleeveTex and
                    self.clothesTopsList[i+3] == sleeveTexColor):
                    result = ToontownGlobals.P_ItemInCloset
                    break
        else:
            bottomStyleInfo = ToonDNA.BottomStyles[styleStr]
            botTex = bottomStyleInfo[0]
            botTexColor = bottomStyleInfo[1][clothingItem.colorIndex]
            for i in range(0, len(self.clothesBottomsList), 2):
                if (self.clothesBottomsList[i] == botTex and
                    self.clothesBottomsList[i+1] == botTexColor):
                    result = ToontownGlobals.P_ItemInCloset
                    break
        return result

    def checkForItemAlreadyWorn(self, clothingItem):
        """Returns None if the toon is not wearing the clothing item."""
        result = None
        clothingTypeInfo = CatalogClothingItem.ClothingTypes[clothingItem.clothingType]
        styleStr = clothingTypeInfo[1]
        if clothingItem.isShirt():
            shirtStyleInfo = ToonDNA.ShirtStyles[styleStr]
            topTex = shirtStyleInfo[0]
            sleeveTex = shirtStyleInfo[1]
            topTexColor = shirtStyleInfo[2][clothingItem.colorIndex][0]
            sleeveTexColor = shirtStyleInfo[2][clothingItem.colorIndex][1]
            if self.dna.topTex == topTex and                self.dna.sleeveTex == sleeveTex and                self.dna.topTexColor == topTexColor and                self.dna.sleeveTexColor == sleeveTexColor:
                result = ToontownGlobals.P_ItemAlreadyWorn
        else:
            bottomStyleInfo = ToonDNA.BottomStyles[styleStr]
            bottomTex = bottomStyleInfo[0]
            bottomTexColor = bottomStyleInfo[1][clothingItem.colorIndex]
            if self.dna.botTex == bottomTex and                self.dna.botTexColor == bottomTexColor:
                result = ToontownGlobals.P_ItemAlreadyWorn
        return result

    def checkForDuplicateItem(self, catalogItem):
        """Return None if the catalog item is not in his mailbox, or on him somehow"""
        result = None
        if catalogItem in self.mailboxContents:
            result = ToontownGlobals.P_ItemInMailbox
        elif catalogItem in self.onOrder:
            result = ToontownGlobals.P_ItemOnOrder
        elif catalogItem in self.onGiftOrder:
            result = ToontownGlobals.P_ItemOnGiftOrder
        elif catalogItem in self.awardMailboxContents:
            result = ToontownGlobals.P_ItemInAwardMailbox
        elif catalogItem in self.onAwardOrder:
            result = ToontownGlobals.P_ItemOnAwardOrder
        if not result:
            if catalogItem.getTypeCode() == CatalogItemTypes.CLOTHING_ITEM:
                result = self.checkForItemInCloset(catalogItem)
                if not result:
                    result = self.checkForItemAlreadyWorn(catalogItem)
            elif catalogItem.getTypeCode() == CatalogItemTypes.CHAT_ITEM:
                speedChatIndex = catalogItem.customIndex
                if speedChatIndex in self.customMessages:
                    result = ToontownGlobals.P_ItemInMyPhrases
            elif catalogItem.getTypeCode() == CatalogItemTypes.PET_TRICK_ITEM:
                trickId = catalogItem.trickId
                if trickId in self.petTrickPhrases:
                    result = ToontownGlobals.P_ItemInPetTricks
        return result


class PurchaseGiftRequest:
    """The leak's PurchaseGiftRequest: the same checks, in the same order, on GiftToon stand-ins."""
    notify = DirectNotifyGlobal.directNotify.newCategory('PurchaseGiftRequest')

    def __init__(self, distObj, sAv, rAv, itemBlob):
        self.distObj = distObj
        self.air = distObj.air
        self.sAv = sAv
        self.rAv = rAv
        self.item = CatalogItem.getItem(itemBlob, store=CatalogItem.Customization)
        self.catalogType = None
        self.cost = 0

    def checkCatalog(self, retcode):
        sAv = self.sAv
        if self.item in sAv.monthlyCatalog:
            self.catalogType = CatalogItem.CatalogTypeMonthly
        elif self.item in sAv.weeklyCatalog:
            self.catalogType = CatalogItem.CatalogTypeWeekly
        elif self.item in sAv.backCatalog:
            self.catalogType = CatalogItem.CatalogTypeBackorder
        else:
            self.air.writeServerEvent('suspicious', sAv.doId, 'purchaseItem %s not in catalog' % self.item)
            self.notify.warning('Avatar %s attempted to purchase %s, not on catalog.' % (sAv.doId, self.item))
            return ToontownGlobals.P_NotInCatalog
        # The AI already took the price (payForGiftItem); the leak never set cost, so a
        # rejected gift refunded 0. Report the price so the AI's refund gives it back.
        self.cost = self.item.getPrice(self.catalogType)
        return retcode

    def checkGift(self, retcode):
        if self.item.isGift() <= 0:
            return ToontownGlobals.P_NotAGift
        return retcode

    def checkGender(self, retcode):
        rAv = self.rAv
        if (self.item.forBoysOnly() and rAv.dna.getGender() == 'f') or (self.item.forGirlsOnly() and rAv.dna.getGender() == 'm'):
            return ToontownGlobals.P_WillNotFit
        return retcode

    def checkPurchaseLimit(self, retcode):
        if self.item.reachedPurchaseLimit(self.rAv):
            return ToontownGlobals.P_ReachedPurchaseLimit
        return retcode

    def checkMailbox(self, retcode):
        rAv = self.rAv
        if len(rAv.mailboxContents) + len(rAv.onGiftOrder) >= ToontownGlobals.MaxMailboxContents:
            if len(rAv.mailboxContents) == 0:
                retcode = ToontownGlobals.P_OnOrderListFull
            else:
                retcode = ToontownGlobals.P_MailboxFull
        return retcode

    def run(self):
        """Returns (retcode, cost, itemBlob). retcode None means the gift may be sent."""
        retcode = None
        retcode = self.checkGift(retcode)
        retcode = self.checkCatalog(retcode)
        retcode = self.checkGender(retcode)
        retcode = self.checkPurchaseLimit(retcode)
        retcode = self.checkMailbox(retcode)
        if retcode is not None:
            return retcode, self.cost, None
        now = int(time.time() / 60 + 0.5)
        deliveryTime = self.item.getDeliveryTime() / self.distObj.timeScale
        if deliveryTime < 2:
            deliveryTime = 2
        self.item.deliveryDate = int(now + deliveryTime)
        itemBlob = CatalogItemList.CatalogItemList([self.item]).getBlob(store=DeliveryStore)
        return None, self.cost, itemBlob


def AccumRATBeans(newGift, giftListBlob):
    giftList = CatalogItemList.CatalogItemList(giftListBlob, store=DeliveryStore)
    found = 0
    if newGift.giftCode == ToontownGlobals.GIFT_RAT:
        numBeans = newGift.beanAmount
        for index in range(len(giftList)):
            if giftList[index].giftCode == ToontownGlobals.GIFT_RAT and found == 0:
                found = 1
                giftList[index].beanAmount = numBeans + giftList[index].beanAmount
    if found:
        giftList.markDirty()
    else:
        giftList.append(newGift)
    return giftList.getBlob(DeliveryStore)


class DistributedDeliveryManagerUD(DistributedObjectGlobalUD):
    notify = DirectNotifyGlobal.directNotify.newCategory('DistributedDeliveryManagerUD')

    def __init__(self, air):
        DistributedObjectGlobalUD.__init__(self, air)
        self.timeScale = ConfigVariableDouble('catalog-time-scale', 1.0).getValue()
        # avId -> jobs waiting to read-modify-write that toon's gift schedule, one at a time.
        self.avJobs = {}
        # avId -> (blob, time) of the gift schedule we last wrote.
        self.writtenBlobs = {}

    # One read-modify-write of a toon at a time, so two gifts in a row cannot overwrite each other.

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
        if dclass != self.air.dclassesByName['DistributedToonUD'] or fields is None:
            fields = None
        else:
            written = self.writtenBlobs.get(avId)
            if written and time.time() - written[1] < WrittenBlobLifetime:
                fields = dict(fields)
                fields['setGiftSchedule'] = (written[0],)
        try:
            job(fields)
        finally:
            jobs = self.avJobs[avId]
            jobs.pop(0)
            if jobs:
                self.__startJob(avId)
            else:
                del self.avJobs[avId]

    def __writeGiftSchedule(self, avId, giftBlob):
        self.writtenBlobs[avId] = (giftBlob, time.time())
        self.air.sendUpdateToDoId('DistributedToon', 'setGiftSchedule', avId, [giftBlob])

    # AI requests.

    def receiveRequestPurchaseGift(self, giftBlob, receiverId, senderId, context):
        replyToChannelAI = self.air.getMsgSender()

        def reject(retcode, cost):
            self.sendUpdateToChannel(replyToChannelAI, 'receiveRejectPurchaseGift', [senderId, context, retcode, cost])

        def gotSender(sFields):
            if sFields is None:
                self.notify.warning('gift from %s: sender not found' % senderId)
                reject(ToontownGlobals.P_NotShopping, 0)
                return
            sAv = GiftToon(senderId, sFields)
            self.__queueJob(receiverId, lambda rFields: gotReceiver(sAv, rFields))

        def gotReceiver(sAv, rFields):
            if rFields is None:
                self.air.writeServerEvent('suspicious', senderId, 'Attempted to buy a gift for %s which is not a toon' % receiverId)
                reject(ToontownGlobals.P_NotShopping, 0)
                return
            rAv = GiftToon(receiverId, rFields)
            retcode, cost, itemBlob = PurchaseGiftRequest(self, sAv, rAv, giftBlob).run()
            if retcode is not None:
                reject(retcode, cost)
                return
            giftList = CatalogItemList.CatalogItemList(rFields['setGiftSchedule'][0], store=DeliveryStore)
            giftItem = CatalogItemList.CatalogItemList(itemBlob, store=DeliveryStore)
            self.air.writeServerEvent('Adding Gift', receiverId, 'sender %s receiver %s gift %s' % (senderId, receiverId, giftItem[0].getName()))
            giftList.append(giftItem[0])
            self.__writeGiftSchedule(receiverId, giftList.getBlob(DeliveryStore))
            self.sendUpdateToChannel(replyToChannelAI, 'receiveAcceptPurchaseGift', [senderId, context, ToontownGlobals.P_ItemOnOrder])

        # The sender is read outside the receiver's queue; a toon can gift itself.
        self.air.dbInterface.queryObject(self.air.dbId, senderId,
                                         lambda dclass, fields: gotSender(fields if dclass == self.air.dclassesByName['DistributedToonUD'] else None))

    def deliverGifts(self, avId, time):
        """The AI delivered this toon's due gifts; drop them from the stored schedule."""
        replyToChannelId = self.air.getMsgSender()

        def job(fields):
            if fields is None:
                self.sendUpdateToChannel(replyToChannelId, 'receiveRejectDeliverGifts', [avId, 'rRDG'])
                return
            giftList = CatalogItemList.CatalogItemList(fields['setGiftSchedule'][0], store=DeliveryStore)
            delivered, remaining = giftList.extractDeliveryItems(time)
            self.__writeGiftSchedule(avId, remaining.getBlob(DeliveryStore))
            self.sendUpdateToChannel(replyToChannelId, 'receiveAcceptDeliverGifts', [avId, 'rADG'])

        self.__queueJob(avId, job)

    def giveBeanBonus(self, receiverId, amount):
        item = CatalogBeanItem.CatalogBeanItem(amount)
        item.giftTag = 0
        item.giftCode = 1
        self.__giveItem(receiverId, item)

    def __giveItem(self, receiverId, item):
        """The leak's GiveItem + addGift: an unchecked server gift, delivered in two minutes."""
        now = int(time.time() / 60 + 0.5)
        item.deliveryDate = int(now + 2)
        itemBlob = CatalogItemList.CatalogItemList([item]).getBlob(store=DeliveryStore)

        def job(fields):
            if fields is None:
                self.notify.warning('server gift for %s: not a toon' % receiverId)
                return
            giftBlob = fields['setGiftSchedule'][0]
            giftItem = CatalogItemList.CatalogItemList(itemBlob, store=DeliveryStore)
            self.air.writeServerEvent('Adding Server Gift', receiverId, 'receiver %s gift %s' % (receiverId, giftItem[0].getName()))
            if giftItem[0].giftCode != ToontownGlobals.GIFT_RAT:
                giftList = CatalogItemList.CatalogItemList(giftBlob, store=DeliveryStore)
                giftList.append(giftItem[0])
                giftBlob = giftList.getBlob(DeliveryStore)
            else:
                giftBlob = AccumRATBeans(giftItem[0], giftBlob)
            self.__writeGiftSchedule(receiverId, giftBlob)

        self.__queueJob(receiverId, job)

    # Client and test messages.

    def hello(self, message):
        replyToChannel = self.air.getMsgSender()
        self.sendUpdateToChannel(replyToChannel, 'helloResponse', [message + 'response'])

    def requestAck(self):
        replyToChannel = self.air.getMsgSender()
        self.sendUpdateToChannel(replyToChannel, 'returnAck', [])
