import datetime
import json
import os
import random
import re
import time

from direct.directnotify.DirectNotifyGlobal import directNotify
from direct.distributed.DistributedObjectGlobalUD import DistributedObjectGlobalUD
from direct.showbase.PythonUtil import SerialNumGen, Functor
from panda3d.core import ConfigVariableBool, ConfigVariableString
from otp.distributed import OtpDoGlobals
from toontown.catalog import CatalogItemTypes
from toontown.coderedemption.TTCodeDict import TTCodeDict
from toontown.coderedemption import TTCodeRedemptionConsts
from toontown.coderedemption import TTCodeRedemptionSpamDetector

# Ported from the Disney leak (toontownretro/toontown-org 7d3b71f,
# toontown/src/coderedemption/TTCodeRedemptionMgrUD.py + TTCodeRedemptionDB.py).
# Disney kept code lots in MySQL (TTCodeRedemptionDB) and managed them from an HTML admin page
# with reCAPTCHA. Here the lots live in a JSON file, the same way DistributedPartyManagerUD's
# PartyDb keeps parties, and the admin page is not ported: CodeDb keeps the leak DB's lot methods
# (createManualLot, createLot, deleteLot, lookup...) as plain Python for a script to call.
# Random codes come from random.SystemRandom instead of the NonRepeatableRandomSource UberDOG.
#
# The redemption rules are the leak's:
#   - code must be utf-8 and only letters, digits, dashes and spaces, else CodeDoesntExist
#   - bad or unknown codes count toward the spam detector; over the limit -> TooManyAttempts
#   - auto (random) codes: expired lot -> CodeIsExpired (CodeIsInactive in this client's
#     Consts, same value), already redeemed -> CodeAlreadyRedeemed; one redemption each
#   - manual codes: any number of toons, no redemption limit (and, as in the leak's DB query,
#     no expiry check)
#   - award refused by the AwardManager -> AwardCouldntBeGiven + the award manager's error


class CodeDb:
    """TTCodeRedemptionDB's lot and code tables on a JSON file."""
    notify = directNotify.newCategory('TTCodeRedemptionDB')

    # from TTCodeRedemptionDB
    StartCodeLength = 4
    LotNameRe = re.compile('^[a-z0-9_]+$')

    def __init__(self, air):
        self.air = air
        self.filePath = ConfigVariableString('coderedemptiondb-local-file',
                                             'astron/databases/coderedemption.json').getValue()
        self.data = {'codeSpace': {'codeLength': self.StartCodeLength, 'nextCodeValue': 0},
                     'lots': {}}
        self._code2lotName = {}
        if os.path.exists(self.filePath):
            self.load()
        else:
            self.save()

    def load(self):
        with open(self.filePath, 'r') as file:
            self.data = json.load(file)
        self._refreshCode2lotName()

    def save(self):
        with open(self.filePath, 'w') as file:
            json.dump(self.data, file, indent=1)

    def _refreshCode2lotName(self):
        self._code2lotName = {}
        for lotName, lot in self.data['lots'].items():
            for code in lot['codes']:
                self._code2lotName[code] = lotName

    @staticmethod
    def _checkExpiration(expirationDate):
        if expirationDate is None:
            return None
        # YYYY-MM-DD, the lot is good through the end of that day
        return str(datetime.date.fromisoformat(str(expirationDate)))

    @classmethod
    def isLotNameValid(cls, lotName):
        return cls.LotNameRe.match(lotName) is not None

    @staticmethod
    def isValidManualCodeRewardType(rewardType):
        # from TTCodeRedemptionMgrUD._isValidManualCodeRewardType
        isPermanent = rewardType not in CatalogItemTypes.NonPermanentItemTypes
        multipleAllowed = CatalogItemTypes.CatalogItemType2multipleAllowed[rewardType]
        return isPermanent and (not multipleAllowed)

    def _newLot(self, name, manual, rewardType, rewardItemId, expirationDate):
        if not self.isLotNameValid(name):
            raise ValueError('Name can only contain lowercase ASCII letters, numbers, and underscores')
        if self.lotExists(name):
            raise ValueError('tried to create lot %s that already exists' % name)
        return {'name': name,
                'manual': manual,
                'rewardType': int(rewardType),
                'rewardItemId': int(rewardItemId),
                'expiration': self._checkExpiration(expirationDate),
                'creation': str(datetime.datetime.fromtimestamp(int(time.time()))),
                'codes': {}}

    def createManualLot(self, name, code, rewardType, rewardItemId, expirationDate=None):
        """One code that any number of toons can redeem (the leak's admin form checks included)."""
        self.load()
        self.notify.info('creating manual code lot \'%s\', code=%s' % (name, ascii(code)))
        code = TTCodeDict.getFromReadableCode(code)
        if len(code) == 0:
            raise ValueError('This field is required')
        if not TTCodeDict.isLegalCode(code):
            raise ValueError('Code can only contain alphanumeric characters and dashes')
        if len(code) > TTCodeRedemptionConsts.MaxCustomCodeLen:
            raise ValueError('Code must be %s characters or less' % TTCodeRedemptionConsts.MaxCustomCodeLen)
        if not [c for c in code if TTCodeDict.isManualOnlyChar(c)]:
            raise ValueError('Code must contain at least one of the following: %s' % TTCodeDict.ManualOnlyCharacters)
        if not self.isValidManualCodeRewardType(rewardType):
            raise ValueError('reward type %s cannot be given by a manual code' % rewardType)
        if self.codeExists(code):
            raise ValueError('tried to create code %s that already exists' % ascii(code))
        lot = self._newLot(name, True, rewardType, rewardItemId, expirationDate)
        lot['codes'][code] = {'redemptions': 0, 'avId': None}
        self.data['lots'][name] = lot
        self.save()
        self._refreshCode2lotName()
        self.notify.info('done')
        return code

    def createLot(self, name, numCodes, rewardType, rewardItemId, expirationDate=None):
        """numCodes random single-use codes. Returns the codes, readable (XXX-XXX)."""
        self.load()
        self.notify.info('creating code lot \'%s\', %s codes' % (name, numCodes))
        lot = self._newLot(name, False, rewardType, rewardItemId, expirationDate)
        rng = random.SystemRandom()
        codeLength = self.data['codeSpace']['codeLength']
        curSerialNum = self.data['codeSpace']['nextCodeValue']
        numCodeValues = TTCodeDict.getNumUsableValuesInCodeSpace(codeLength)
        codes = []
        codesLeft = numCodes
        while codesLeft:
            # each code is given a chunk of code space, of size N, and the actual value of the
            # code is chosen from that section of code space using a true random source
            randScatter = rng.randrange(TTCodeDict.BruteForceFactor)
            value = (curSerialNum * TTCodeDict.BruteForceFactor) + randScatter
            obfValue = TTCodeDict.getObfuscatedCodeValue(value, codeLength)
            code = TTCodeDict.getCodeFromValue(obfValue, codeLength)
            lot['codes'][code] = {'redemptions': 0, 'avId': None}
            codes.append(TTCodeDict.getReadableCode(code))
            codesLeft -= 1
            curSerialNum += 1
            if curSerialNum >= numCodeValues:
                curSerialNum = 0
                codeLength += 1
                numCodeValues = TTCodeDict.getNumUsableValuesInCodeSpace(codeLength)
        self.data['codeSpace'] = {'codeLength': codeLength, 'nextCodeValue': curSerialNum}
        self.data['lots'][name] = lot
        self.save()
        self._refreshCode2lotName()
        self.notify.info('done')
        return codes

    def deleteLot(self, lotName):
        self.load()
        self.notify.info('deleting code lot \'%s\'' % (lotName, ))
        self.data['lots'].pop(lotName, None)
        self.save()
        self._refreshCode2lotName()

    def getLotNames(self):
        return list(self.data['lots'].keys())

    def getAutoLotNames(self):
        return [name for name, lot in self.data['lots'].items() if not lot['manual']]

    def getManualLotNames(self):
        return [name for name, lot in self.data['lots'].items() if lot['manual']]

    def getExpirationLotNames(self):
        return [name for name, lot in self.data['lots'].items() if lot['expiration'] is not None]

    def getCodesInLot(self, lotName, justCode=True):
        codes = self.data['lots'][lotName]['codes']
        if justCode:
            return list(codes.keys())
        return dict(codes)

    def lotExists(self, lotName):
        return lotName in self.data['lots']

    def getLotNameFromCode(self, code):
        code = TTCodeDict.getFromReadableCode(code)
        return self._code2lotName.get(code, None)

    def codeExists(self, code):
        return self.getLotNameFromCode(code) is not None

    def getRewardFromCode(self, code):
        lot = self.data['lots'][self.getLotNameFromCode(code)]
        return lot['rewardType'], lot['rewardItemId']

    def getRedemptions(self, code):
        code = TTCodeDict.getFromReadableCode(code)
        return self.data['lots'][self.getLotNameFromCode(code)]['codes'][code]['redemptions']

    def getExpiration(self, lotName):
        return self.data['lots'][lotName]['expiration']

    def setExpiration(self, lotName, expiration):
        self.load()
        self.data['lots'][lotName]['expiration'] = self._checkExpiration(expiration)
        self.save()

    def lookupCodesRedeemedByAvId(self, avId):
        # manual lots don't record redeemer avIds since they are single-code-multi-toon
        codes = []
        for lotName in self.getAutoLotNames():
            for code, info in self.data['lots'][lotName]['codes'].items():
                if info['avId'] == avId:
                    codes.append(code)
        return codes

    def getCodeDetails(self, code):
        code = TTCodeDict.getFromReadableCode(code)
        lotName = self.getLotNameFromCode(code)
        if lotName is None:
            return None
        lot = self.data['lots'][lotName]
        details = dict((k, v) for k, v in lot.items() if k != 'codes')
        details.update(lot['codes'][code])
        details['code'] = code
        return details

    @staticmethod
    def _isExpired(lot):
        if lot['expiration'] is None:
            return False
        return datetime.date.today() > datetime.date.fromisoformat(lot['expiration'])

    def redeemCode(self, code, avId, rewarder, callback):
        # callback takes a RedeemError and the award manager result
        # 'code' can come from a client, treat with care
        origCode = code
        code = TTCodeDict.getFromReadableCode(code)

        lotName = self.getLotNameFromCode(code)
        if lotName is None:
            self.air.writeServerEvent('invalidCodeRedemption', avId, '%s' % (ascii(origCode), ))
            callback(TTCodeRedemptionConsts.RedeemErrors.CodeDoesntExist, 0)
            return

        lot = self.data['lots'][lotName]
        manualCode = lot['manual']

        if not manualCode:
            if self._isExpired(lot):
                callback(TTCodeRedemptionConsts.RedeemErrors.CodeIsInactive, 0)
                return

            if lot['codes'][code]['redemptions'] > 0:
                callback(TTCodeRedemptionConsts.RedeemErrors.CodeAlreadyRedeemed, 0)
                return

        rewardTypeId, rewardItemId = self.getRewardFromCode(code)

        rewarder._giveReward(avId, rewardTypeId, rewardItemId, Functor(
            self._handleRewardResult, code, manualCode, avId, lotName, rewardTypeId, rewardItemId,
            callback))

    def _handleRewardResult(self, code, manualCode, avId, lotName, rewardTypeId, rewardItemId,
                            callback, result):
        awardMgrResult = result
        if awardMgrResult:
            callback(TTCodeRedemptionConsts.RedeemErrors.AwardCouldntBeGiven, awardMgrResult)
            return

        self.load()
        lot = self.data['lots'].get(lotName)
        if lot is not None and code in lot['codes']:
            lot['codes'][code]['redemptions'] += 1
            if not manualCode:
                lot['codes'][code]['avId'] = avId
            self.save()

        self.air.writeServerEvent('codeRedeemed', avId, '%s|%s|%s|%s' % (
            ascii(code if manualCode else TTCodeDict.getReadableCode(code)),
            lotName, rewardTypeId, rewardItemId, ))

        callback(TTCodeRedemptionConsts.RedeemErrors.Success, awardMgrResult)


class TTCodeRedemptionMgrUD(DistributedObjectGlobalUD):
    notify = directNotify.newCategory('TTCodeRedemptionMgrUD')

    Disabled = ConfigVariableBool('disable-code-redemption', False).getValue()

    def __init__(self, air):
        DistributedObjectGlobalUD.__init__(self, air)

        self._rewardSerialNumGen = SerialNumGen()
        self._rewardContextTable = {}

        self._db = CodeDb(self.air)

        self._spamDetector = TTCodeRedemptionSpamDetector.TTCodeRedemptionSpamDetector()
        self._wantSpamDetect = ConfigVariableBool('want-code-redemption-spam-detect', True).getValue()

    def getDb(self):
        return self._db

    def _codeHasInvalidChars(self, code):
        return not TTCodeDict.isLegalCode(code)

    def redeemCode(self, context, code):
        # The client's own TTCodeRedemptionMgr global object (4695) sends here if the district's
        # AI manager has not reached it yet. Same checks, and the answer goes straight back.
        avId = self.air.getAvatarIdFromSender()

        def reply(serial, context, avId, result, awardMgrResult):
            self.sendUpdateToAvatarId(avId, 'redeemCodeResult', [context, result, awardMgrResult])

        self.redeemCodeAiToUd(0, 0, context, code, avId, reply)

    def redeemCodeAiToUd(self, serial, rmDoId, context, code, senderId, callback=None):
        avId = senderId

        # context is supplied by the client and there are no invalid values for it
        # code comes from the client and could be any string

        result = None

        if self.Disabled:
            result = TTCodeRedemptionConsts.RedeemErrors.SystemUnavailable
        else:
            while 1:
                if isinstance(code, bytes):
                    try:
                        code = code.decode('utf-8')
                    except UnicodeDecodeError:
                        # code is not utf-8-able
                        self.air.writeServerEvent('suspicious', avId, 'non-utf-8 code redemption: %s' % repr(code))
                        result = TTCodeRedemptionConsts.RedeemErrors.CodeDoesntExist
                        break

                if self._codeHasInvalidChars(code):
                    # code has non-letter/digit/dash characters
                    result = TTCodeRedemptionConsts.RedeemErrors.CodeDoesntExist
                    break

                break

            if (result or (not self._db.codeExists(code))):
                # check to make sure this avatar isn't submitting incorrect codes too often
                self._spamDetector.codeSubmitted(senderId)

            if self._wantSpamDetect and self._spamDetector.avIsBlocked(senderId):
                self.air.writeServerEvent('suspicious', avId,
                                          'too many invalid code redemption attempts, '
                                          'submission rejected: %s' % ascii(code))
                result = TTCodeRedemptionConsts.RedeemErrors.TooManyAttempts

        if result is not None:
            awardMgrResult = 0
            self._handleRedeemCodeAiToUdResult(callback, serial, rmDoId, context, avId, result, awardMgrResult)
        else:
            self._db.redeemCode(code, avId, self, Functor(
                self._handleRedeemCodeAiToUdResult, callback, serial, rmDoId, context, avId, ))

    def _handleRedeemCodeAiToUdResult(self, callback, serial, rmDoId, context, avId, result, awardMgrResult):
        if callback:
            callback(serial, context, avId, result, awardMgrResult)
        else:
            self.air.sendUpdateToDoId('TTCodeRedemptionMgr',
                                      'redeemCodeResultUdToAi',
                                      rmDoId,
                                      [serial, context, avId, result, awardMgrResult]
                                      )

    def _giveReward(self, avId, rewardType, rewardItemId, callback):
        # callback takes result
        context = next(self._rewardSerialNumGen)
        self._rewardContextTable[context] = callback
        self.air.sendUpdateToDoId(
            'AwardManager', 'giveAwardToToon',
            OtpDoGlobals.OTP_DO_ID_TOONTOWN_AWARD_MANAGER,
            [context, self.doId, 'TTCodeRedemptionMgr', avId, rewardType, rewardItemId, ])

    def giveAwardToToonResult(self, context, result):
        callback = self._rewardContextTable.pop(context, None)
        if callback is None:
            self.notify.warning('unexpected giveAwardToToonResult: %s, %s' % (context, result))
            return
        callback(result)
