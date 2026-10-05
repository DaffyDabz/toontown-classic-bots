"""Cog-suit progress for the bots of the higher hoods (TTBOTS P8b).

A real 2013 Brrrgh regular usually had a Sellbot suit and some Cashbot parts; a Dreamland regular
had most suits, at a spread of cog levels, with some merits banked and now and then a suit ready
for promotion. The bots get the same, ONCE, in the DB (setCogParts / setCogTypes / setCogLevels /
setCogMerits through the DB server, while the bot is offline), seeded by the bot's own number so
it is the same regular every time. After that the game keeps it: factories give parts and merits,
a boss battle promotes, exactly as for a player. Nothing here touches the AI's checks: a boss
elevator still asks readyForPromotion, a lobby door still asks for a whole suit.

The registry entry gets 'suitV' (version) and a copy of what was written ('suit'), so a restart
never re-seeds.
"""
import random

from direct.directnotify import DirectNotifyGlobal

from toontown.bots import BotWorld

notify = DirectNotifyGlobal.directNotify.newCategory('BotCogSuits')

VERSION = 4                 # v2: promotions banked mostly by the Dreamland regulars (v1 had 60-laff VP groups)
                            # v3: most Dreamland regulars finished every suit (enough for 6-8 toon boss groups)
                            # v4: enough banked promotions to keep 8 regulars waiting at each boss elevator
C, L, M, S = range(4)                       # SuitDNA.suitDepts order: c l m s
DEPT_NAMES = ('Bossbot', 'Lawbot', 'Cashbot', 'Sellbot')
# home hood -> per dept (chance of a whole suit, (lowest, highest) cog type when whole, partial parts range)
PLAN = {
    BotWorld.BR: {S: (0.70, (0, 3)), M: (0.30, (0, 2)), L: (0.10, (0, 1)), C: (0.03, (0, 0))},
    BotWorld.DDL: {S: (0.95, (3, 7)), M: (0.9, (2, 7)), L: (0.85, (1, 6)), C: (0.75, (0, 5))},
}
READY_CHANCE = {BotWorld.BR: 0.25, BotWorld.DDL: 0.75}   # merits banked up to the next promotion


def _tables():
    from toontown.coghq import CogDisguiseGlobals as CDG
    return CDG


def _partial(rng, dept, count):
    """count parts in the order the game hands them out (the next missing part of a random region)."""
    CDG = _tables()
    parts = [0, 0, 0, 0]
    for _ in range(count):
        opts = [p for p in range(len(CDG.PartsQueryMasks)) if CDG.getNextPart(parts, p, dept)]
        if not opts:
            break
        parts[dept] |= CDG.getNextPart(parts, rng.choice(opts), dept)
    return parts[dept]


def _meritsFor(dept, cogType, cogLevel):
    CDG = _tables()
    table = CDG.MeritsPerLevel[dept * 8 + cogType]
    i = max(0, min(cogLevel - cogType, len(table) - 1))
    return table[i]


def makeSuit(home, seed):
    """{'parts', 'types', 'levels', 'merits'} (4 each, dept order c l m s) or None (lower hoods)."""
    plan = PLAN.get(home)
    if plan is None:
        return None
    CDG = _tables()
    rng = random.Random(seed * 131 + 7)
    parts, types, levels, merits = [0] * 4, [0] * 4, [0] * 4, [0] * 4
    for dept in range(4):
        whole, (lo, hi) = plan[dept]
        if rng.random() < whole:
            parts[dept] = CDG.PartsPerSuitBitmasks[dept]
            t = rng.randint(lo, hi)
            span = 4 if t < 7 else rng.choice((4, 8, 12, 20))
            lvl = t + rng.randint(0, span)
            types[dept], levels[dept] = t, lvl
            need = _meritsFor(dept, t, lvl)
            merits[dept] = need if rng.random() < READY_CHANCE[home] else rng.randint(0, max(0, need - 1))
        else:
            most = CDG.PartsPerSuit[dept] - 1
            k = rng.randint(0, most if dept in (S, M) else most // 2)
            parts[dept] = _partial(rng, dept, k)
    return {'parts': parts, 'types': types, 'levels': levels, 'merits': merits}


def seedAll(director):
    """Write the suits of every offline bot that has none yet (the director's district-up)."""
    pool, air = director.pool, director.air
    n = 0
    for spec in pool.specs:
        e = pool.registry.get(spec['key'])
        if not e or not e.get('avId') or e.get('suitV') == VERSION:
            continue
        suit = makeSuit(e.get('home', spec['home']), spec['seed'])
        if suit is None:
            e['suitV'] = VERSION
            continue
        bot = director.bots.get(e['avId'])
        if bot is not None and bot.state != 'offline':
            continue                       # the DBSS holds it now: next time it is offline
        air.dbInterface.updateObject(air.dbId, e['avId'], air.dclassesByName['DistributedToon'], {
            'setCogParts': (suit['parts'],), 'setCogTypes': (suit['types'],),
            'setCogLevels': (suit['levels'],), 'setCogMerits': (suit['merits'],)})
        e['suitV'] = VERSION
        e['suit'] = suit
        n += 1
    if n:
        pool.save()
        notify.info('[TTBOTS-P8b] cog suits written for %d bots' % n)
    return n


def _onCreate(spec, fields):
    """P8c: a bot made after P8b (the Cog HQ regulars, bots 301-360) gets its suit and its facility loadout in the
    same DB create (it logs in at once, so the offline seeding would never reach it)."""
    home = spec['home']
    out = {'suitV': VERSION}
    suit = makeSuit(home, spec['seed'])
    if suit is not None:
        fields.update({'setCogParts': (suit['parts'],), 'setCogTypes': (suit['types'],),
                       'setCogLevels': (suit['levels'],), 'setCogMerits': (suit['merits'],)})
        out['suit'] = suit
    from toontown.bots.coghq import loadout
    out.update(loadout.onCreate(spec, fields))
    return out


from toontown.bots import BotPool as _BotPool
if _onCreate not in _BotPool.CREATE_HOOKS:
    _BotPool.CREATE_HOOKS.append(_onCreate)


# ---- what a bot has (the owner view first: it follows promotions and new parts) -------------------------
def suitOf(bot):
    f = bot.ownFields
    try:
        return {'parts': list(f['setCogParts'][0]), 'types': list(f['setCogTypes'][0]),
                'levels': list(f['setCogLevels'][0]), 'merits': list(f['setCogMerits'][0])}
    except (KeyError, IndexError, TypeError):
        e = bot.director.pool.registry.get(bot.key) or {}
        return e.get('suit') or {'parts': [0] * 4, 'types': [0] * 4, 'levels': [0] * 4, 'merits': [0] * 4}


def hasWholeSuit(bot, dept):
    CDG = _tables()
    s = suitOf(bot)
    return s['parts'][dept] & CDG.PartsPerSuitBitmasks[dept] == CDG.PartsPerSuitBitmasks[dept]


def readyForPromotion(bot, dept):
    """The AI's own rule (DistributedToonAI.readyForPromotion): merits >= the level's total."""
    s = suitOf(bot)
    return s['merits'][dept] >= _meritsFor(dept, s['types'][dept], s['levels'][dept])


def higherHood(bot):
    return bot.home in (BotWorld.BR, BotWorld.DDL)
