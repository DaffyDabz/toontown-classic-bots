"""Gag loadouts for the Cog HQ regulars (the owner's rule: a maxed toon carries what a maxed player carries:
full stacks of its top gags, not piles of level-1s).

  seedAll(director)   once per VERSION, while each Brrrgh / Dreamland bot is offline: its pouch in the DB becomes
                      a player's facility loadout for its own track exp (the top three levels it can buy, each
                      to the game's per-level limit, then a couple of the small ones) - InventoryBase's own limits,
                      so the AI accepts it; one bot's before/after goes to the log
  planPurchase(bot)   the gag shop (the P4 Visit, validatePurchase) for a bot flagged p8bLoadout: highest levels
                      first, each to its limit, one bean each, like a player filling up before a run
  quality(bot)        how many of its gags are in its top three levels (a run takes only well-stocked bots)
"""
import random

from direct.directnotify import DirectNotifyGlobal

from toontown.bots import BotWorld

notify = DirectNotifyGlobal.directNotify.newCategory('BotLoadout')
VERSION = 3                  # v2/v3 (P8c): attack gags first, lure / toon-up / trap capped (v1 carried 25 lures)
ORDER = (2, 0, 4, 5, 3, 6, 1)            # lure, toon-up, throw, squirt, sound, drop, trap


class _Toon:
    def __init__(self, exp, carry, tracks):
        self.experience = exp
        self.maxCarry = carry
        self.trackArray = tracks
        self.doId = 0

    def getGameAccess(self):
        from toontown.toonbase import ToontownGlobals
        return ToontownGlobals.AccessFull

    def hasTrackAccess(self, track):
        return bool(self.trackArray[track])

    def getMaxCarry(self):
        return self.maxCarry


def topLevel(exp, track):
    from toontown.toonbase import ToontownBattleGlobals as TBG
    return min(exp.getExpLevel(track), TBG.LAST_REGULAR_GAG_LEVEL)


ATTACK_FIRST = (4, 5, 6, 3, 2, 0, 1)     # P8c: throw, squirt, drop, sound, then lure, toon-up, trap
SUPPORT_CAP = {2: 14, 0: 16, 1: 5}       # a facility regular carries some lures / toon-ups / traps, not 25 lures


def fill(inv, exp, tracks, budget=10 ** 6):
    """Add the best gags first: each track's top three levels to their limit (attack tracks first; lure, toon-up
    and trap only up to SUPPORT_CAP each), then two of each smaller level."""
    n = 0
    mine = [t for t in ATTACK_FIRST if tracks[t]]
    have = lambda t: sum(inv.numItem(t, l) for l in range(7))
    for depth in range(3):
        for t in mine:
            lvl = topLevel(exp, t) - depth
            if lvl < 0:
                continue
            while n < budget and have(t) < SUPPORT_CAP.get(t, 999) and inv.addItem(t, lvl) > 0:
                n += 1
    for t in mine:
        for lvl in range(max(0, topLevel(exp, t) - 3), -1, -1):
            for _ in range(2):
                if n < budget and inv.addItem(t, lvl) > 0:
                    n += 1
    return n


def seedAll(director):
    from toontown.bots.BotPool import makeStats
    from toontown.toon.Experience import Experience
    from toontown.toon.InventoryBase import InventoryBase
    pool, air = director.pool, director.air
    n, shown = 0, False
    for spec in pool.specs:
        e = pool.registry.get(spec['key'])
        if not e or not e.get('avId') or e.get('loadoutV') == VERSION:
            continue
        if e.get('home', spec['home']) not in (BotWorld.BR, BotWorld.DDL):
            e['loadoutV'] = VERSION
            continue
        bot = director.bots.get(e['avId'])
        if bot is not None and bot.state != 'offline':
            continue
        stats = makeStats(e.get('home', spec['home']), e.get('tier', spec.get('tier', 0)), random.Random(spec['seed'] * 31))
        exp = Experience(stats['setExperience'][0])
        tracks = list(stats['setTrackAccess'][0])
        carry = stats['setMaxCarry'][0]
        toon = _Toon(exp, carry, tracks)
        before = InventoryBase(toon, stats['setInventory'][0])
        inv = InventoryBase(toon)
        toon.inventory = inv
        fill(inv, exp, tracks)
        air.dbInterface.updateObject(air.dbId, e['avId'], air.dclassesByName['DistributedToon'],
                                     {'setInventory': (inv.makeNetString(),)})
        if not shown:
            shown = True
            notify.info('[TTBOTS-P8b] loadout of %s (carry %d): before %s -> after %s' % (
                e['avId'], carry, [[before.numItem(t, l) for l in range(7)] for t in range(7)],
                [[inv.numItem(t, l) for l in range(7)] for t in range(7)]))
        e['loadoutV'] = VERSION
        n += 1
    if n:
        pool.save()
        notify.info('[TTBOTS-P8b] facility loadouts written for %d bots' % n)
    return n


def onCreate(spec, fields):
    """P8c: a new Brrrgh / Dreamland bot's pouch is its facility loadout from the start (BotPool.CREATE_HOOKS)."""
    if spec['home'] not in (BotWorld.BR, BotWorld.DDL):
        return {'loadoutV': VERSION}
    from toontown.toon.Experience import Experience
    from toontown.toon.InventoryBase import InventoryBase
    exp = Experience(fields['setExperience'][0])
    tracks = list(fields['setTrackAccess'][0])
    toon = _Toon(exp, fields['setMaxCarry'][0], tracks)
    inv = InventoryBase(toon)
    toon.inventory = inv
    fill(inv, exp, tracks)
    fields['setInventory'] = (inv.makeNetString(),)
    return {'loadoutV': VERSION}


def planPurchase(bot):
    """(blob, newMoney, n) the gag shop accepts (validatePurchase), top gags first."""
    from toontown.bots import battlebrain as bb
    from toontown.toon.InventoryBase import InventoryBase
    inv0, exp = bb.loadInventory(bot)
    if inv0 is None:
        return None
    f = bot.ownFields
    money = f['setMoney'][0]
    tracks = list(f['setTrackAccess'][0])
    toon = _Toon(exp, f['setMaxCarry'][0], tracks)
    inv = InventoryBase(toon, f['setInventory'][0])
    toon.inventory = inv
    n = fill(inv, exp, tracks, budget=money)
    check = InventoryBase(toon, f['setInventory'][0])
    if not check.validatePurchase(inv.inventory, money, money - n):
        return None
    return inv.makeNetString(), money - n, n


def quality(bot):
    from toontown.bots import battlebrain as bb
    inv, exp = bb.loadInventory(bot)
    if inv is None:
        return 0
    return sum(inv.numItem(t, l) for t in range(7) for l in range(7) if l >= topLevel(exp, t) - 2 and l >= 0)
