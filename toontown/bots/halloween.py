"""HALLOWEEN (owner 10-01): the bots dress for the season and say the Halloween SpeedChat.

DRESS (his "Dress them now"): while the Halloween decorations run, most bots (SHARE) wear an outfit from the 2013
Halloween catalog (CatalogGenerator's Oct entries: hw_ shirts / shorts / skirt, the witch hats, the Halloween
glasses, backpacks and shoes). It is written into the OFFLINE bot's DB toon the way a purchase puts it on
(CatalogClothingItem / CatalogAccessoryItem.recordPurchase: the DNA top / bottom, the skirt torso, setHat ...),
plus the Halloween custom SpeedChat phrases (CustomSCStrings 10000-10019) as if bought. No beans spent. The bot's
own look and phrases are kept in its registry entry ('hw') and written back the first time it is offline after
the season. The login hold (bot.resetHold, progress_pool.waitingReset) keeps it out while the write lands.

CHAT (his "say all the halloween quick chats ... dont let it over write there other stuff"): now and then a
present bot says a Halloween line: the Halloween SpeedChat menu (30250-30252, TTSCHalloweenMenu) or one of its
Halloween custom phrases (setSCCustom). EXTRA lines only: nothing else a bot says is changed, and the line goes
through the zone's own chat budget (lifekit.canSay), so it never crowds a zone.

TRICK-OR-TREAT (his "Yes, earn them"): the real hunt (TrickOrTreatMgrAI). A Trick-or-Treat shop the bot has not found
yet, in a hood it reaches, is a goal (taskplan 'treat', after its ToonTask visits): it walks into that shop
(lifekit.Visit), says "Trick or Treat!" (custom phrase 10003, the client's DistributedTrickOrTreatTarget.phraseSaid)
and sends attemptScavengerHunt to the shop's target, as the client does. The answer (trickOrTreatTargetMet) marks
the shop found in its registry entry ('tot'); all six and the AI puts the pumpkin head on. A bot that cannot reach
a hood yet skips that shop until it can (his "bots that can't reach all 6 yet go without").

Log lines: '[TTBOTS-HWN]' in botai.log.
"""
import random
import time
import traceback

from direct.directnotify import DirectNotifyGlobal

notify = DirectNotifyGlobal.directNotify.newCategory('BotHalloween')

SHARE = 0.85                     # bots in costume (the rest stay as they are, like players who skipped it)
DRESS_EVERY = 10.0               # offline pass (a bot waits for it before it logs in)
WAIT_MAX = 120.0                 # ... but never longer than this (a DB answer that never came)
CHAT_EVERY = 5.0                 # chat pass
CHAT_P = CHAT_EVERY / 240.0      # about one Halloween line per present bot every 4 minutes
FIELDS = ('setDNAString', 'setHat', 'setGlasses', 'setBackpack', 'setShoes', 'setCustomMessages')

# the 2013 Halloween catalog (CatalogGenerator MonthlySchedule, Oct 3 - Nov 2 entries)
SHIRTS = (1001, 1002, 1112, 1113, 1114, 1115, 1116, 1743, 1744, 1770, 1771)
BOY_BOTTOMS = (1117, 1118, 1119, 1120, 1121, 1745, 1746, 1772, 1774)
GIRL_BOTTOMS = (1122, 1123, 1124, 1125, 1126, 1127, 1747, 1748, 1773, 1775)
HATS_BOY, HATS_GIRL = (172,), (171, 172)            # hhw2 any toon, hhw1 girls only
GLASSES_BOY = (224,)                                 # ghw2: boys only
BACKPACKS = (324, 325, 326, 327)
SHOES = (448, 449)
CUSTOM = tuple(range(10000, 10020))                  # the Halloween custom SpeedChat phrases
MENU = (30250, 30251, 30252)                         # Boo! / Happy Halloween! / Spooky!
MAX_CUSTOM = 25                                      # OTPGlobals.MaxCustomMessages
QUIET_ACTS = ('battle', 'trolley', 'coghq', 'boss')


# TrickOrTreatMgrAI.GOALS: the shop interior of each hood's target
TOT_GOALS = {0: 2649, 1: 1834, 2: 4835, 3: 5620, 4: 3707, 5: 9619}
TREAT_PHRASE = 10003                                 # "Trick or Treat!"


def inSeason(t=None, first2013=20):
    """The Halloween window (HolidayManagerAI): decorations Oct 20 - Nov 1 in 2013 (Trick-or-Treat: first2013=27),
    Oct 1 - Nov 1 with the server's halloween-all-october switch."""
    from panda3d.core import ConfigVariableBool
    lt = time.localtime(t if t is not None else time.time())
    first = 1 if ConfigVariableBool('halloween-all-october', False).getValue() else first2013
    return (lt.tm_mon == 10 and lt.tm_mday >= first) or (lt.tm_mon == 11 and lt.tm_mday == 1)


def outfit(dnaBytes, seed):
    """{field: args} of a Halloween outfit for this toon, or None (a bot left out of the costume share)."""
    from toontown.toon import ToonDNA
    from toontown.catalog import CatalogClothingItem as CC, CatalogAccessoryItem as CA
    rng = random.Random(seed)
    if rng.random() > SHARE:
        return None
    dna = ToonDNA.ToonDNA()
    dna.makeFromNetString(dnaBytes)
    girl = dna.getGender() == 'f'
    shirt = rng.choice(SHIRTS)
    defn = ToonDNA.ShirtStyles[CC.ClothingTypes[shirt][CC.CTString]]
    color = rng.randrange(len(defn[2]))
    dna.topTex, dna.topTexColor = defn[0], defn[2][color][0]
    dna.sleeveTex, dna.sleeveTexColor = defn[1], defn[2][color][1]
    bottom = rng.choice(GIRL_BOTTOMS if girl else BOY_BOTTOMS)
    defn = ToonDNA.BottomStyles[CC.ClothingTypes[bottom][CC.CTString]]
    dna.botTex, dna.botTexColor = defn[0], defn[1][rng.randrange(len(defn[1]))]
    if girl:                                          # recordPurchase: the torso follows skirt / shorts
        try:
            pair = ToonDNA.GirlBottoms[dna.botTex]
        except IndexError:
            pair = ToonDNA.GirlBottoms[0]
        if dna.torso[1] == 's' and pair[1] == ToonDNA.SKIRT:
            dna.torso = dna.torso[0] + 'd'
        elif dna.torso[1] == 'd' and pair[1] == ToonDNA.SHORTS:
            dna.torso = dna.torso[0] + 's'
    out = {'setDNAString': (dna.makeNetString(),)}

    def acc(field, table, ids, p):
        if ids and rng.random() < p:
            out[field] = tuple(table[CA.AccessoryTypes[rng.choice(ids)][CA.ATString]])
    acc('setHat', ToonDNA.HatStyles, HATS_GIRL if girl else HATS_BOY, 0.5)
    acc('setGlasses', ToonDNA.GlassesStyles, () if girl else GLASSES_BOY, 0.25)
    acc('setBackpack', ToonDNA.BackpackStyles, BACKPACKS, 0.45)
    acc('setShoes', ToonDNA.ShoesStyles, SHOES, 0.35)
    return out


def _bytes(v):
    return v if isinstance(v, (bytes, bytearray)) else bytes(v)


def _save(fields):
    """Registry copy (JSON) of the bot's own look and phrases."""
    out = {}
    for k in FIELDS:
        if k not in fields:
            continue
        v = fields[k]
        if k == 'setDNAString':
            out[k] = _bytes(v[0]).hex()
        else:
            out[k] = [list(x) if isinstance(x, (list, tuple)) else x for x in v]
    return out


def _load(saved):
    out = {}
    for k, v in saved.items():
        out[k] = (bytes.fromhex(v),) if k == 'setDNAString' else tuple(v)
    return out


class Halloween:
    def __init__(self, director):
        self.d = director
        self.lastDress = 0.0
        self.lastChat = 0.0
        self.busy = set()               # avIds with a DB query / write in flight
        self.said = 0
        self.waitSince = {}             # avId -> when it first waited for its costume

    def tick(self, now):
        try:
            if now - self.lastDress >= DRESS_EVERY:
                self.lastDress = now
                self.__dressPass(now)
            if now - self.lastChat >= CHAT_EVERY:
                self.lastChat = now
                if inSeason():
                    self.__chatPass(now)
        except Exception:
            notify.warning('[TTBOTS-HWN] %s' % traceback.format_exc())

    # ---- costumes ----------------------------------------------------------------------------------------
    def waiting(self, bot):
        """True: this offline bot is not dressed for this season yet (progress_pool.waitingReset asks)."""
        if not inSeason():
            return False
        e = self.d.pool.registry.get(bot.key) or {}
        if not e.get('avId') or (e.get('hw') or {}).get('y') == time.localtime().tm_year:
            self.waitSince.pop(bot.avId, None)
            return False
        now = globalClock.getRealTime()
        return now - self.waitSince.setdefault(bot.avId, now) < WAIT_MAX

    def __dressPass(self, now, budget=500):
        d = self.d
        season = inSeason()
        year = time.localtime().tm_year
        n = 0
        for spec in d.pool.specs:
            if n >= budget:
                break
            e = d.pool.registry.get(spec['key'])
            if not e or not e.get('avId') or e['avId'] in self.busy:
                continue
            hw = e.get('hw')
            if not season and e.get('tot'):
                e.pop('tot', None)                        # next year's hunt starts empty (the UD store resets)
            if season and (hw or {}).get('y') == year:
                continue                                  # dressed (or left out) this season
            if not season and not hw:
                continue
            bot = d.bots.get(e['avId'])
            if bot is not None and bot.state != 'offline':
                continue                                  # the DBSS holds it now: once it has logged out
            if bot is not None:
                bot.resetHold = max(getattr(bot, 'resetHold', 0.0), now + 30.0)
            self.busy.add(e['avId'])
            if season:
                self.__dress(spec['key'], e, bot, year)
            else:
                self.__undress(spec['key'], e, bot)
            n += 1

    def __dress(self, key, e, bot, year):
        air = self.d.air
        dclass = air.dclassesByName['DistributedToon']
        avId = e['avId']

        def got(dc, fields):
            try:
                if not fields or 'setDNAString' not in fields:
                    notify.warning('[TTBOTS-HWN] %s: no DNA in the DB answer' % key)
                    return
                new = outfit(_bytes(fields['setDNAString'][0]), avId * 31 + year) or {}   # {}: no costume
                own = list(fields.get('setCustomMessages', ([],))[0])
                room = max(0, MAX_CUSTOM - len(own))
                add = [m for m in CUSTOM if m not in own]
                if TREAT_PHRASE in add:                   # "Trick or Treat!" first when room is short
                    add.remove(TREAT_PHRASE)
                    add.insert(0, TREAT_PHRASE)
                add = add[:room]
                if add:
                    new['setCustomMessages'] = (own + add,)           # every phrase it had stays
                e['hw'] = {'y': year, 'orig': _save(fields), 'added': add}
                if new:
                    air.dbInterface.updateObject(air.dbId, avId, dclass, new)
                    if bot is not None:
                        bot.ownFields.update(new)
                        bot.resetHold = globalClock.getRealTime() + 15.0
                notify.info('[TTBOTS-HWN] %s dressed: %s' % (key, ' '.join(sorted(new)) or 'nothing (no costume, no room)'))
                self.d.pool.save()
            except Exception:
                notify.warning('[TTBOTS-HWN] dress %s: %s' % (key, traceback.format_exc()))
            finally:
                self.busy.discard(avId)
        air.dbInterface.queryObject(air.dbId, avId, got, dclass, FIELDS)

    def __undress(self, key, e, bot):
        """After the season: the bot's own look back; the Halloween phrases it was given go (any it had stay)."""
        air = self.d.air
        dclass = air.dclassesByName['DistributedToon']
        avId = e['avId']
        hw = e.get('hw') or {}

        def got(dc, fields):
            try:
                back = _load(hw.get('orig') or {})
                added = set(hw.get('added') or ())
                back.pop('setCustomMessages', None)
                if fields and added:
                    now = list(fields.get('setCustomMessages', ([],))[0])
                    back['setCustomMessages'] = ([m for m in now if m not in added],)
                if back:
                    air.dbInterface.updateObject(air.dbId, avId, dclass, back)
                    if bot is not None:
                        bot.ownFields.update(back)
                        bot.resetHold = globalClock.getRealTime() + 15.0
                e.pop('hw', None)
                self.d.pool.save()
                notify.info('[TTBOTS-HWN] %s back in its own clothes' % key)
            except Exception:
                notify.warning('[TTBOTS-HWN] undress %s: %s' % (key, traceback.format_exc()))
            finally:
                self.busy.discard(avId)
        air.dbInterface.queryObject(air.dbId, avId, got, dclass, ('setCustomMessages',))

    # ---- Halloween SpeedChat (extra lines) ---------------------------------------------------------------
    def __chatPass(self, now):
        from toontown.bots.activities import lifekit as kit
        for bot in list(self.d.bots.values()):
            if bot.state != 'present' or bot.travel is not None or random.random() > CHAT_P:
                continue
            act = bot.activity.name if bot.activity is not None else None
            if act in QUIET_ACTS or not kit.canSay(bot, now):
                continue
            own = [m for m in (bot.ownFields.get('setCustomMessages') or ([],))[0] if m in CUSTOM]
            if own and random.random() < 0.65:
                load = getattr(self.d, 'load', None)
                if load is not None and not load.mayChat(bot):
                    continue
                bot.send('setSCCustom', [random.choice(own)])
                bot._lifeNextSay = now + random.uniform(*kit.BOT_GAP)
                kit.LIFE.zoneLast[bot.zoneId] = now
                kit.LIFE.count('sc_lines')
            else:
                kit.say(bot, random.choice(MENU), now, answer=False)
            self.said += 1
            if self.said % 25 == 1:
                notify.info('[TTBOTS-HWN] Halloween lines said: %d' % self.said)


# ---- Trick-or-Treat ------------------------------------------------------------------------------------------
def _entry(bot):
    return bot.director.pool.registry.get(getattr(bot, 'key', None)) or {}


def treatsFor(bot):
    """[(goal, street area, block)] of the Trick-or-Treat shops this bot has not found this season."""
    if not inSeason(first2013=27):
        return []
    tot = _entry(bot).get('tot') or {}
    found = set(tot.get('found', ())) if tot.get('y') == time.localtime().tm_year else set()
    out = []
    for gid, zone in sorted(TOT_GOALS.items()):
        if gid not in found:
            block = zone % 100
            out.append((gid, zone - block - 500, block))
    return out


def trickOrTreat(bot, view, now):
    """In the shop: say "Trick or Treat!" and ask the shop's target, like the client's phraseSaid()."""
    load = getattr(bot.director, 'load', None)
    if load is None or load.mayChat(bot):
        bot.send('setSCCustom', [TREAT_PHRASE])
    targets = [o for o in (view.objects.values() if view is not None else ())
               if o.className == 'DistributedTrickOrTreatTarget']
    if targets:
        bot.send('attemptScavengerHunt', [], doId=targets[0].doId, className='DistributedTrickOrTreatTarget')
    else:
        notify.info('[TTBOTS-HWN] %s: no Trick-or-Treat target in zone %s' % (bot.avId, bot.zoneId))


def treated(bot, goal, beans):
    """The shop answered (beans: 100 new, 0 found before) or not (None: set the shop aside for a while)."""
    from toontown.bots import taskplan
    gid = goal.get('goal')
    if beans is None:
        b = getattr(bot, 'prog', None)
        if b is not None:
            b.blocked[taskplan.key(goal)] = time.time() + 900.0
        notify.info('[TTBOTS-HWN] %s: no answer at Trick-or-Treat shop %s' % (bot.avId, gid))
        return
    e = _entry(bot)
    year = time.localtime().tm_year
    tot = e.get('tot') if (e.get('tot') or {}).get('y') == year else {'y': year, 'found': []}
    if gid not in tot['found']:
        tot['found'].append(gid)
    e['tot'] = tot
    bot.director.pool.save()
    n = len(tot['found'])
    notify.info('[TTBOTS-HWN] %s Trick-or-Treat shop %s (%s beans): %d of %d%s' % (
        bot.avId, gid, beans, n, len(TOT_GOALS), ' - PUMPKIN HEAD' if n == len(TOT_GOALS) else ''))
    try:
        from toontown.bots.activities import lifekit as kit
        kit.say(bot, random.choice(MENU), globalClock.getRealTime(), force=True, answer=False)
    except Exception:
        pass
