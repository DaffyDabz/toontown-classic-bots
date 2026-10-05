"""The fixed pool of bot toons (TTBOTS P2): DB records, created once, loaded every start.

Registry: astron/databases/bots.json (config bot-registry-file), key 'bot-NNNN' ->
  {accountId, avId, name, home, seed, gender, tier}. Lucky Doodlesnout stays bot-0001.
Creation is idempotent and resumable: the account id is written the moment the account
exists, the toon id the moment the toon exists, so a stop half-way resumes where it left off.
DB creates are throttled (bot-create-per-sec, default 4) so the DB and the State Server
never see a burst.

Every bot is the same regular each time it logs in: its name comes from the game's own
Pick-A-Name generator (NameGenerator.randomName) and its look from ToonDNA.newToonRandom,
both seeded by the bot's number. Its stats fit its HOME HOOD, built the way the game builds
a real toon's (QuestRewardCounter for the reward tier -> max laff, carry, jellybean jar,
ToonTask slots, teleport access; Experience + InventoryBase for gags), so the toon panel,
Details, the gag shop (validatePurchase) and battles (requestAttack numItem) all accept them.
"""
import json
import os
import random

from direct.directnotify import DirectNotifyGlobal
from toontown.bots import BotWorld
from toontown.bots import progress_pool
from toontown.toon import ToonDNA
from toontown.toonbase import ToontownBattleGlobals as TBG, ToontownGlobals

POOL_SIZE = 480     # POP: counted-as-seen targets need ~15% more toons (the ones in shops, trolley games)
                    # PROGRESSION: 421-480 are fresh toons, so 420 progress besides the 60 crew (301-360)
# home-hood split of the pool (TTC is the busiest place in the game)
HOME_SPLIT = ((BotWorld.TTC, 80), (BotWorld.DD, 45), (BotWorld.DG, 45), (BotWorld.MML, 45),
              (BotWorld.BR, 45), (BotWorld.DDL, 40))
# P8c: bots 301-360 are the Cog HQ regulars (the owner's boss-lobby rule: 8 waiting at each of the four boss
# elevators plus a refill needs ~64 regulars with finished suits): Dreamland toons, a few from the Brrrgh
EXTRA_SPLIT = ((BotWorld.DDL, 48), (BotWorld.BR, 12))
# POP: bots 361-420 cover the ones out of sight (shops, Toon HQ, trolley games) and a real player's hood
EXTRA2_SPLIT = ((BotWorld.TTC, 12), (BotWorld.DD, 10), (BotWorld.DG, 10), (BotWorld.MML, 10), (BotWorld.BR, 10),
                (BotWorld.DDL, 8))
# per home hood: reward tiers, extra laff (fish/karting/golf/suits), how many gag tracks, track exp
PROFILE = {
    BotWorld.TTC: {'tiers': (0, 1, 2, 3), 'extraHp': (0, 0), 'tracks': (2, 2, 2, 3), 'exp': (0, 140)},
    BotWorld.DD:  {'tiers': (4, 5, 6), 'extraHp': (0, 3), 'tracks': (3, 3, 4), 'exp': (120, 900)},
    BotWorld.DG:  {'tiers': (7,), 'extraHp': (0, 6), 'tracks': (4,), 'exp': (400, 1600)},
    BotWorld.MML: {'tiers': (8, 9, 10), 'extraHp': (0, 8), 'tracks': (4, 5, 5), 'exp': (800, 2600)},
    BotWorld.BR:  {'tiers': (11, 12, 13), 'extraHp': (0, 12), 'tracks': (5, 6, 6), 'exp': (2000, 6200)},
    BotWorld.DDL: {'tiers': (18, 22, 26, 32, 40, 49), 'extraHp': (0, 30), 'tracks': (6,), 'exp': (6000, 9999)},
}
CREATE_HOOKS = []                  # fn(spec, fields) -> registry extras: a new toon's DB fields, filled in by later phases
CHOOSABLE = (0, 1, 2, 3, 6)       # toon-up, trap, lure, sound, drop (throw 4 + squirt 5 every toon has)
LUCKY = {'key': 'bot-0001', 'name': 'Lucky Doodlesnout', 'seed': 1001, 'gender': 'm', 'home': BotWorld.TTC,
         'tier': 0, 'pinned': True}


class _StatToon:
    """Just enough of a toon for Experience/InventoryBase to size a gag pouch."""
    def __init__(self, experience, maxCarry, trackArray):
        self.experience = experience
        self.maxCarry = maxCarry
        self.trackArray = trackArray
        self.doId = 0

    def getGameAccess(self):
        return ToontownGlobals.AccessFull

    def hasTrackAccess(self, track):
        return bool(self.trackArray[track])

    def getMaxCarry(self):
        return self.maxCarry


def makeStats(home, tier, rng):
    """DB fields for a toon whose progress fits its home hood."""
    from toontown.quest.QuestRewardCounter import QuestRewardCounter
    from toontown.toon.Experience import Experience
    from toontown.toon.InventoryBase import InventoryBase
    prof = PROFILE[home]
    qrc = QuestRewardCounter()
    qrc.setRewardIndex(tier, [], [])
    maxHp = min(ToontownGlobals.MaxHpLimit, qrc.maxHp + rng.randint(*prof['extraHp']))
    hp = maxHp if rng.random() < 0.8 else rng.randint(max(1, maxHp // 2), maxHp)
    tracks = [0, 0, 0, 0, 1, 1, 0]
    extra = rng.choice(prof['tracks']) - 2
    for t in rng.sample(CHOOSABLE, extra):
        tracks[t] = 1
    exp = Experience()
    lo, hi = prof['exp']
    for t in range(len(tracks)):
        if tracks[t]:
            exp.experience[t] = rng.randint(lo, hi)
    inv = InventoryBase(_StatToon(exp, qrc.maxCarry, tracks))
    inv.toon.inventory = inv
    inv.maxOutInv(filterUberGags=1)
    if home == BotWorld.TTC:        # a fresh toon's pouch is half used up
        for t in range(len(tracks)):
            for lvl in range(len(TBG.Levels[t])):
                inv.inventory[t][lvl] = int(inv.inventory[t][lvl] * rng.uniform(0.3, 0.8))
        inv.calcTotalProps()
    teleport = list(qrc.teleportAccess)
    hoods = sorted(set(teleport + [home, BotWorld.TTC]))
    return {
        'setMaxHp': (maxHp,), 'setHp': (hp,),
        'setMaxCarry': (qrc.maxCarry,), 'setMaxMoney': (qrc.maxMoney,),
        'setMoney': (rng.randint(qrc.maxMoney // 4, qrc.maxMoney),),
        'setBankMoney': (rng.randint(0, 200 + tier * 250),),
        'setQuestCarryLimit': (qrc.questCarryLimit,),
        'setRewardHistory': (tier, []),
        'setTrackAccess': (tracks,),
        'setExperience': (exp.makeNetString(),),
        'setInventory': (inv.makeNetString(),),
        'setTeleportAccess': (teleport,),
        'setHoodsVisited': (hoods,), 'setZonesVisited': (hoods,),
        'setLastHood': (home,), 'setDefaultZone': (home,),
        'setTutorialAck': (1,),
    }


class BotPool:
    notify = DirectNotifyGlobal.directNotify.newCategory('BotPool')

    def __init__(self, air):
        self.air = air
        self.path = config.GetString('bot-registry-file', 'astron/databases/bots.json')
        self.size = config.GetInt('bot-pool-size', POOL_SIZE)
        self.perSec = config.GetFloat('bot-create-per-sec', 4.0)
        self.registry = {}
        self.specs = []                 # every bot, by key order
        self.pending = []               # keys still to create in the DB
        self.inFlight = set()
        self.created = 0
        self.errors = 0
        self.onReady = None             # callback(spec) when a bot has an account + toon

    # ---- the pool, same every start ---------------------------------------------
    def load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.registry = json.load(f)
        self.specs = self.__makeSpecs()
        self.pending = [s['key'] for s in self.specs if not self.registry.get(s['key'], {}).get('avId')]
        self.notify.info('[TTBOTS] pool: %d bots, %d in the DB, %d to create' % (
            len(self.specs), len(self.specs) - len(self.pending), len(self.pending)))

    def __makeSpecs(self):
        # NameGenerator's class body measures names with the interface font; the AI has no fonts
        from panda3d.core import TextNode
        from otp.otpbase import OTPGlobals
        if OTPGlobals.InterfaceFont is None:
            OTPGlobals.InterfaceFont = TextNode.getDefaultFont()
        from toontown.makeatoon.NameGenerator import NameGenerator
        ng = NameGenerator()
        homes = []
        for hood, n in HOME_SPLIT:
            homes += [hood] * n
        extra = []
        for hood, n in EXTRA_SPLIT:
            extra += [hood] * n
        extra2 = []
        for hood, n in EXTRA2_SPLIT:
            extra2 += [hood] * n
        specs = [dict(LUCKY)]
        used = {LUCKY['name']}
        for e in self.registry.values():
            if e.get('name'):
                used.add(e['name'])
        for i in range(2, self.size + 1):
            key = 'bot-%04d' % i
            seed = 1000 + i
            rng = random.Random(seed)
            gender = rng.choice('mf')
            if i - 1 < len(homes):
                home = homes[(i - 1) % len(homes)]
            elif i - 1 - len(homes) < len(extra):
                home = extra[(i - 1 - len(homes)) * 7 % len(extra)]      # interleaved, same every start
            elif i - 1 - len(homes) - len(extra) < len(extra2):
                home = extra2[(i - 1 - len(homes) - len(extra)) * 7 % len(extra2)]
            else:
                home = rng.choice(BotWorld.HOODS)
            tier = rng.choice(PROFILE[home]['tiers'])
            entry = self.registry.get(key, {})
            name = entry.get('name')
            if not name:
                state = random.getstate()
                k = 0
                while True:
                    random.seed(seed * 7919 + k)
                    name = ng.randomName(boy=gender == 'm', girl=gender == 'f')
                    k += 1
                    if name not in used and len(name) <= 32:
                        break
                random.setstate(state)
                used.add(name)
            crew = progress_pool.isCrew(key)
            if not crew and not entry.get('avId'):
                home, tier = BotWorld.TTC, 0     # PROGRESSION: a toon made from now on is a fresh level-1 toon
            specs.append({'key': key, 'name': name, 'seed': seed, 'gender': gender,
                          'home': entry.get('home', home), 'tier': entry.get('tier', tier),
                          'crew': crew, 'style': entry.get('style') or progress_pool.styleFor(seed)})
        specs[0]['crew'] = False
        specs[0]['style'] = self.registry.get(LUCKY['key'], {}).get('style') or progress_pool.styleFor(LUCKY['seed'])
        # PROGRESSION: the personality is kept in the registry (the same toon every start)
        for sp in specs:
            e = self.registry.get(sp['key'])
            if e is not None and not e.get('style'):
                e['style'] = sp['style']
        # interleave homes so a partial pool is still spread over every hood
        return specs

    def entry(self, key):
        return self.registry.get(key)

    def isReady(self, spec):
        e = self.registry.get(spec['key'])
        return bool(e and e.get('avId'))

    def save(self):
        tmp = self.path + '.new'
        with open(tmp, 'w') as f:
            json.dump(self.registry, f, indent=1)
        os.replace(tmp, self.path)

    # ---- creation, throttled -----------------------------------------------------
    def start(self):
        if self.pending:
            taskMgr.doMethodLater(1.0 / self.perSec, self.__createNext, 'bot-pool-create')

    def stop(self):
        taskMgr.remove('bot-pool-create')

    def __createNext(self, task):
        if not self.pending:
            self.notify.info('[TTBOTS] pool: every bot is in the DB (%d created this start, %d errors)'
                             % (self.created, self.errors))
            return task.done
        if len(self.inFlight) >= 4:
            return task.again
        key = self.pending.pop(0)
        spec = next(s for s in self.specs if s['key'] == key)
        self.inFlight.add(key)
        entry = self.registry.get(key, {})
        if entry.get('accountId'):
            self.__createToon(spec, entry['accountId'])
        else:
            self.__createAccount(spec)
        return task.again

    def __fail(self, spec, what):
        self.errors += 1
        self.inFlight.discard(spec['key'])
        self.pending.append(spec['key'])      # retried later
        self.notify.warning('[TTBOTS] pool: %s failed for %s' % (what, spec['key']))

    def __createAccount(self, spec):
        fields = {'ACCOUNT_AV_SET': [0] * 6, 'ESTATE_ID': 0, 'ACCOUNT_AV_SET_DEL': [],
                  'CREATED': 'bot', 'LAST_LOGIN': 'bot', 'ACCOUNT_ID': spec['key'], 'ACCESS_LEVEL': 'USER'}
        self.air.dbInterface.createObject(self.air.dbId, self.air.dclassesByName['AstronAccount'], fields,
                                          lambda accId: self.__accountCreated(spec, accId))

    def __accountCreated(self, spec, accId):
        if not accId:
            return self.__fail(spec, 'account create')
        self.registry[spec['key']] = {'accountId': accId, 'name': spec['name'], 'home': spec['home'],
                                      'seed': spec['seed'], 'gender': spec['gender'], 'tier': spec['tier'],
                                      'style': spec['style']}
        self.save()
        self.__createToon(spec, accId)

    def __createToon(self, spec, accId):
        dna = ToonDNA.ToonDNA()
        dna.newToonRandom(seed=spec['seed'], gender=spec['gender'])
        fields = {'setName': (spec['name'],), 'WishNameState': ('CLOSED',), 'WishName': ('',),
                  'setDNAString': (dna.makeNetString(),), 'setDISLid': (accId,)}
        reg = {}
        if spec.get('crew'):
            fields.update(makeStats(spec['home'], spec['tier'], random.Random(spec['seed'] * 31)))
            hooks = CREATE_HOOKS
        else:
            # PROGRESSION (owner 4a): a progressing toon is born a fresh level-1 toon: no stamped stats, no suit,
            # no Cog HQ loadout (CREATE_HOOKS are the crew's)
            fields.update(progress_pool.freshFields(self.air.dclassesByName['DistributedToon']))
            fields['setTutorialAck'] = (1,)
            progress_pool.markFresh(reg)
            hooks = ()
        for fn in hooks:                        # P8c: later phases add to a new toon (cog suits, HQ loadouts)
            try:
                reg.update(fn(spec, fields) or {})
            except Exception:
                import traceback
                self.notify.warning('[TTBOTS] pool: create hook failed: %s' % traceback.format_exc())
        spec['_reg'] = reg
        self.air.dbInterface.createObject(self.air.dbId, self.air.dclassesByName['DistributedToon'], fields,
                                          lambda avId: self.__toonCreated(spec, accId, avId))

    def __toonCreated(self, spec, accId, avId):
        if not avId:
            return self.__fail(spec, 'toon create')
        self.air.dbInterface.updateObject(self.air.dbId, accId, self.air.dclassesByName['AstronAccount'],
                                          {'ACCOUNT_AV_SET': [avId, 0, 0, 0, 0, 0]})
        e = self.registry.setdefault(spec['key'], {})
        e.update({'accountId': accId, 'avId': avId, 'name': spec['name'], 'home': spec['home'],
                  'seed': spec['seed'], 'gender': spec['gender'], 'tier': spec['tier'], 'style': spec['style']})
        e.update(spec.pop('_reg', None) or {})
        self.save()
        self.created += 1
        self.inFlight.discard(spec['key'])
        if self.created % 25 == 0:
            self.notify.info('[TTBOTS] pool: %d created, %d to go' % (self.created, len(self.pending)))
        if self.onReady:
            self.onReady(spec)
