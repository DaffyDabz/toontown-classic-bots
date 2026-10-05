"""The progressing pool (TTBOTS progression W1, owner 09-30): who is crew, who levels up, the one-time reset.

CREW: bot-0301..bot-0360, the Cog HQ regulars (suits + facility pouches seeded by coghq/suits.py + loadout.py).
Never reset, only placed in Cog HQ places (plus home to heal / restock, which the Cog HQ code does itself).
Everyone else PROGRESSES: a fresh level-1 toon (owner 4a), then levels up by playing.

RESET (owner 4a, backup first): once per registry entry ('progV'), while the bot is OFFLINE, every progress field
of its DB toon is written to a fresh toon's value = the dc defaults of DistributedToon (toon.dc) with the few
overrides below. Name, look (DNA), clothes, friends and account stay. The registry's 'home' becomes TTC and 'tier'
0, so the offline seeders (coghq/suits.py, coghq/loadout.py: Brrrgh / Dreamland homes only) never touch it again.
The reset runs only once the deploy script has made the backup and written <run>/bots-progress-backup.ok.

Also here: the style (personality) from the seed, the home hood of a reward tier, and bots-progress.txt.
"""
import json
import os
import random
import time
import traceback

VERSION = 1
CREW_FIRST, CREW_LAST = 301, 360
BACKUP_MARK = 'bots-progress-backup.ok'
TTC = 2000
# hood order by reward tier (Quests.TT_TIER ... DL_TIER), read from Quests when it imports
_TIERS = None
STYLES = (('grinder', 0.10), ('regular', 0.60), ('casual', 0.30))

# every DistributedToon field that is PROGRESS (a fresh toon has the dc default)
PROGRESS_FIELDS = (
    'setMaxHp', 'setHp', 'setMoney', 'setMaxMoney', 'setBankMoney',
    'setExperience', 'setMaxCarry', 'setTrackAccess', 'setTrackProgress', 'setTrackBonusLevel', 'setInventory',
    'setNPCFriendsDict', 'setDefaultZone', 'setZonesVisited', 'setHoodsVisited', 'setLastHood',
    'setEmoteAccess', 'setResistanceMessages', 'setTeleportAccess',
    'setCogStatus', 'setCogCount', 'setCogRadar', 'setBuildingRadar',
    'setCogLevels', 'setCogTypes', 'setCogParts', 'setCogMerits', 'setCogSummonsEarned', 'setPinkSlips',
    'setQuests', 'setQuestHistory', 'setRewardHistory', 'setQuestCarryLimit', 'setCheesyEffect',
    'setFishCollection', 'setMaxFishTank', 'setFishTank', 'setFishingRod', 'setFishingTrophies',
    'setTickets', 'setKartingHistory', 'setKartingTrophies', 'setKartingPersonalBest', 'setKartingPersonalBest2',
    'setKartBodyType', 'setKartBodyColor', 'setKartAccessoryColor', 'setKartEngineBlockType', 'setKartSpoilerType',
    'setKartFrontWheelWellType', 'setKartBackWheelWellType', 'setKartRimType', 'setKartDecalType',
    'setKartAccessoriesOwned',
    'setGolfHistory', 'setPackedGolfHoleBest', 'setGolfCourseBest',
)
# a fresh toon out of the tutorial stands in TTC (the dc says 0 = never been anywhere)
OVERRIDES = {'setDefaultZone': (TTC,), 'setLastHood': (TTC,), 'setNPCFriendsDict': ([],)}


def keyNumber(key):
    try:
        return int(key.split('-')[1])
    except (IndexError, ValueError):
        return 0


def isCrew(key):
    return CREW_FIRST <= keyNumber(key) <= CREW_LAST


def styleFor(seed):
    r = random.Random(seed * 97 + 13).random()
    for name, share in STYLES:
        if r < share:
            return name
        r -= share
    return STYLES[-1][0]


def _tierTable():
    global _TIERS
    if _TIERS is None:
        try:
            from toontown.quest import Quests as Q
            _TIERS = ((Q.DL_TIER, 9000), (Q.BR_TIER, 3000), (Q.MM_TIER, 4000), (Q.DG_TIER, 5000),
                      (Q.DD_TIER, 1000), (Q.TT_TIER, TTC))
        except Exception:
            _TIERS = ((14, 9000), (11, 3000), (8, 4000), (7, 5000), (4, 1000), (0, TTC))
    return _TIERS


def homeForTier(tier):
    """TT 0-3, DD 4-6, DG 7, MM 8-10, BR 11-13, DL 14+."""
    for lo, hood in _tierTable():
        if tier >= lo:
            return hood
    return TTC


# ---- the fresh toon ----------------------------------------------------------------------------------------
def freshFields(dclass):
    """{fieldName: args} of a fresh level-1 toon: the dc defaults (+ OVERRIDES), unpacked by the dc itself."""
    from panda3d.direct import DCPacker
    out = {}
    for name in PROGRESS_FIELDS:
        if name in OVERRIDES:
            out[name] = OVERRIDES[name]
            continue
        field = dclass.getFieldByName(name)
        if field is None:
            continue
        p = DCPacker()
        p.setUnpackData(field.getDefaultValue())
        p.beginUnpack(field)
        args = field.unpackArgs(p)
        if not p.endUnpack():
            raise ValueError('dc default of %s did not unpack' % name)
        out[name] = tuple(args)
    return out


def formatField(dclass, name, args):
    """The text Astron's YAML DB keeps for a field (dry run / proof)."""
    from panda3d.direct import DCPacker
    field = dclass.getFieldByName(name)
    p = DCPacker()
    p.beginPack(field)
    field.packArgs(p, list(args))
    if not p.endPack():
        raise ValueError('%s %r did not pack' % (name, args))
    return field.formatData(p.getBytes(), False)


# ---- the one-time reset, live (the director calls this at district-up and every 30 s) --------------------
def backupDone(director):
    """The deploy script's mark (checked at most every 10 s: the director asks per offline bot every second)."""
    now = time.time()
    t, ok = getattr(director, '_progBackupCheck', (0.0, False))
    if now - t >= 10.0:
        ok = os.path.exists(os.path.join(director.runDir, BACKUP_MARK))
        director._progBackupCheck = (now, ok)
    return ok


def needsReset(entry, key):
    return bool(entry and entry.get('avId')) and not isCrew(key) and entry.get('progV') != VERSION


def markFresh(entry):
    """Registry side of a fresh toon: TTC home, tier 0, no suit (the offline seeders skip a TTC home)."""
    entry['progV'] = VERSION
    entry['home'] = TTC
    entry['tier'] = 0
    entry['reach'] = []
    entry.pop('suit', None)


def resetOffline(director, budget=500):
    """Write the fresh toon over every offline progressing bot not yet reset. Returns the count written."""
    pool, air = director.pool, director.air
    if not backupDone(director):
        if not getattr(director, '_progNoBackupSaid', False):
            director._progNoBackupSaid = True
            director.notify.warning('[TTBOTS] PROGRESS reset waits: no %s in %s (the deploy script makes the backup '
                                    'first)' % (BACKUP_MARK, director.runDir))
        return 0
    fresh = freshFields(air.dclassesByName['DistributedToon'])
    now = globalClock.getRealTime()
    n = 0
    for spec in pool.specs:
        if n >= budget:
            break
        e = pool.registry.get(spec['key'])
        if not needsReset(e, spec['key']):
            continue
        bot = director.bots.get(e['avId'])
        if bot is not None and bot.state != 'offline':
            continue                      # the DBSS holds it now: next pass, once it has logged out
        air.dbInterface.updateObject(air.dbId, e['avId'], air.dclassesByName['DistributedToon'], dict(fresh))
        markFresh(e)
        if bot is not None:
            bot.ownFields.update(fresh)
            bot.resetHold = now + 15.0    # the DB write lands before its next login
            bot.baseHome = TTC
        n += 1
    if n:
        pool.save()
        director.notify.info('[TTBOTS] PROGRESS reset: %d bots are fresh level-1 toons (%d still to do)' % (
            n, sum(1 for s in pool.specs if needsReset(pool.registry.get(s['key']), s['key']))))
    return n


def waitingReset(director, bot):
    """True: this bot may not log in yet (not reset, or its reset write is still landing)."""
    if getattr(bot, 'resetHold', 0.0) > globalClock.getRealTime():
        return True
    hw = getattr(director, 'halloween', None)          # HALLOWEEN: in costume before it logs in
    if hw is not None and hw.waiting(bot):
        return True
    if bot.crew or not backupDone(director):
        return False
    return needsReset(director.pool.registry.get(bot.key), bot.key)


# ---- what a bot has reached (owner view first, the registry when it has never logged in this run) ---------
def rewardTier(bot):
    rh = bot.ownFields.get('setRewardHistory')
    if rh:
        try:
            return int(rh[0])
        except (TypeError, ValueError, IndexError):
            pass
    e = bot.director.pool.registry.get(bot.key) or {}
    return int(e.get('tier', 0) or 0)


def teleportAccess(bot):
    ta = bot.ownFields.get('setTeleportAccess')
    if ta:
        try:
            return list(ta[0])
        except (TypeError, IndexError):
            pass
    e = bot.director.pool.registry.get(bot.key) or {}
    return list(e.get('reach') or ())


def remember(director):
    """Registry copy of each online bot's tier / teleport access (offline eligibility after a restart)."""
    changed = False
    for bot in director.bots.values():
        if bot.crew or not bot.ownFields.get('setRewardHistory'):
            continue
        e = director.pool.registry.get(bot.key)
        if not e or e.get('progV') != VERSION:
            continue
        tier, reach = rewardTier(bot), sorted(set(teleportAccess(bot)))
        if e.get('tier') != tier or e.get('reach') != reach:
            e['tier'], e['reach'] = tier, reach
            changed = True
    if changed:
        director.pool.save()


# ---- proof: one line per bot, every 60 s ------------------------------------------------------------------
TRACK_LETTERS = 'HTLSTDD'     # toon-up, trap, lure, sound, throw, squirt, drop (shown as a 0/1 string)


def _count(bot, field, i=0):
    v = bot.ownFields.get(field)
    try:
        return v[i]
    except (TypeError, IndexError):
        return None


def writeProgress(director):
    rows = []
    for bot in sorted(director.bots.values(), key=lambda b: b.key):
        tracks = _count(bot, 'setTrackAccess')
        quests = _count(bot, 'setQuests')
        act = bot.activity.name if bot.activity is not None else ('travel' if bot.travel is not None else '-')
        try:
            goal = getattr(bot, 'goalText', '') or ''
        except Exception:
            goal = '?'
        rows.append('%s  %-28s %-8s %-5s tier %2d  laff %3s  tracks %s  beans %4s  tasks %d  %-8s %-14s %-12s %s' % (
            bot.key, bot.name[:28], bot.style, 'CREW' if bot.crew else '', rewardTier(bot),
            _count(bot, 'setMaxHp') if _count(bot, 'setMaxHp') is not None else '?',
            ''.join('1' if t else '0' for t in tracks) if tracks else '?',
            _count(bot, 'setMoney') if _count(bot, 'setMoney') is not None else '?',
            len(quests) // 5 if quests else 0, bot.state, act,
            bot.area.name[:12] if bot.area is not None else '-', goal))
    head = 'TTBOTS PROGRESS  %s  playing=%s  (%s)' % (
        time.strftime('%Y-%m-%d %H:%M:%S'), 'ON' if director.playing else 'OFF', getattr(director, 'playingWhy', ''))
    path = os.path.join(director.runDir, 'bots-progress.txt')
    try:
        os.makedirs(director.runDir, exist_ok=True)
        with open(path + '.new', 'w') as f:
            f.write(head + '\n' + '\n'.join(rows) + '\n')
        os.replace(path + '.new', path)
    except Exception:
        director.error('progress file', traceback.format_exc())
