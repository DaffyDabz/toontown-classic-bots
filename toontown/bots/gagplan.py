"""The coordinated gag pick (the owner's rules, 2026-09-24): every bot picks AFTER the real players and after
the bots before it, seeing every pick already queued this round (setChosenToonAttacks), and fits in:

  1. heal first when a toon is low, counting the Toon-Ups already queued: one healer unless one heal can't
     cover it; nobody heals when the queued heals already bring everyone up
  2. back the real player up: his Trap -> exactly one bot lures that Cog; his Sound -> the bots sound too;
     any other attack -> the same track on his target
  3. never a wasted or conflicting pick: never two lures on one Cog (one lure a round at most), no lure in a
     round with Sound (Sound wakes lured Cogs), no Sound once a lure is queued or a Cog is lured, no Drop on a
     lured Cog, no trap where a trap already is
  4. exact damage: the damage already queued on each Cog (its track combo bonus, the lure knock-back, a trap
     springing on a lure) against its laff; the gag that finishes a Cog with the least overkill; when no one
     gag finishes one, the gag that adds the most to the Cog closest to dying
The damage of a gag is the game's own (ToontownBattleGlobals.getAvPropDamage with the picker's track exp;
a real player's exp is unknown to a bot, so his gags count at their base damage). Hits are assumed (the
movie can still miss: accuracy is the game's). battlebrain.py runs the pick order (Hub.__pickTick).

Every pick is logged for the proof: <run>/bots-picks.log (one line per pick) and counters in STATS
(pick_double_lure, pick_lure_sound, pick_redundant_heal, pick_overkill_total / pick_kills).
"""
import math
import os
import time

HEAL, TRAP, LURE, SOUND, THROW, SQUIRT, DROP = range(7)
ATTACKS = (SOUND, THROW, SQUIRT, DROP)
PASS, NO_ATTACK, UN_ATTACK = 98, -1, -2
NAMES = ('heal', 'trap', 'lure', 'sound', 'throw', 'squirt', 'drop')
LOW = 0.55                 # a toon below this share of his laff needs a Toon-Up (before damage)
TRIVIAL = 0.25             # a hit smaller than this share of a Cog's laff left is not worth a turn (unless it finishes it)


def _tbg():
    from toontown.toonbase import ToontownBattleGlobals as TBG
    return TBG


def isGroup(track, level):
    from toontown.battle.BattleBase import attackAffectsGroup
    return bool(attackAffectsGroup(track, level))


def gagDamage(track, level, exp=None):
    TBG = _tbg()
    try:
        if exp is not None:
            return TBG.getAvPropDamage(track, level, exp.getExp(track))
    except Exception:
        pass
    return TBG.AvPropDamage[track][level][0][0]


class Board:
    """What the round already holds: the picks queued, per Cog the damage queued, per toon the heal queued."""

    def __init__(self, brain, chosen, suits, suitHp, toons, toonHp, toonMax, lured, traps, expOf):
        self.brain = brain
        self.suits = list(suits)                      # active Cogs, battle order
        self.suitHp = dict(suitHp)
        self.toons = list(toons)                      # active toons
        self.toonHp = dict(toonHp)
        self.toonMax = dict(toonMax)
        self.lured = set(lured)                       # lured before this round
        self.traps = dict(traps)                      # Cog -> trap level already set
        self.picks = []                               # (avId, track, level, target) queued, in pick order
        for av, (t, l, tg) in chosen.items():
            if 0 <= t <= DROP:
                self.picks.append((av, t, l, tg))
        self.expOf = expOf                            # avId -> Experience or None
        self.defense = {}                             # Cog -> its defence (SuitAttributes 'def' row), set by the builder
        self.pouches = None                           # toon -> {track: levels} it still carries (None: unknown), by the builder

    # -- what the queued picks do ----------------------------------------------------------------------------
    def luring(self):
        out = set()
        for av, t, l, tg in self.picks:
            if t == LURE:
                out |= set(self.suits) if isGroup(t, l) else {tg}
        return out

    def trapping(self):
        return {tg: l for av, t, l, tg in self.picks if t == TRAP}

    def dropTargets(self):
        return set(tg for av, t, l, tg in self.picks if t == DROP)

    def soundQueued(self):
        return any(t == SOUND for av, t, l, tg in self.picks)

    def healQueued(self):
        """toon -> laff the queued Toon-Ups bring (a group heal splits over the other active toons)."""
        out = {}
        for av, t, l, tg in self.picks:
            if t != HEAL:
                continue
            amt = gagDamage(HEAL, l, self.expOf(av))
            if isGroup(HEAL, l):
                others = [x for x in self.toons if x != av]
                for x in others:
                    out[x] = out.get(x, 0) + int(math.ceil(amt / float(max(1, len(others)))))
            else:
                out[tg] = out.get(tg, 0) + amt
        return out

    def damage(self, extra=None):
        """Cog -> damage the queued picks (plus extra (av, t, l, tg)) do, with combo, knock-back and traps."""
        picks = self.picks + ([extra] if extra else [])
        sound = any(p[1] == SOUND for p in picks)
        lureNow = set()
        for av, t, l, tg in picks:
            if t == LURE:
                lureNow |= set(self.suits) if isGroup(t, l) else {tg}
        lured = (self.lured | lureNow) if not sound else set()
        per = {}                                       # (cog, track) -> [damage]
        for av, t, l, tg in picks:
            if t not in ATTACKS:
                continue
            d = gagDamage(t, l, self.expOf(av))
            targets = self.suits if isGroup(t, l) else [tg]
            for s in targets:
                if t == DROP and s in (self.lured | lureNow):
                    continue                           # a Drop misses a lured Cog
                per.setdefault((s, t), []).append(d)
        out = {}
        for (s, t), ds in per.items():
            tot = sum(ds)
            if len(ds) > 1:
                tot += int(math.ceil(tot * 0.2))      # the same-track combo (BattleCalculatorAI.DamageBonuses)
            if t in (THROW, SQUIRT) and s in lured:
                tot += tot * 0.5                       # the knock-back on a lured Cog
            out[s] = out.get(s, 0) + tot
        traps = dict(self.traps)
        traps.update({tg: l for av, t, l, tg in picks if t == TRAP})
        for s in lureNow:
            if s in traps and s not in self.lured:
                out[s] = out.get(s, 0) + gagDamage(TRAP, traps[s])
        return out

    def remaining(self, extra=None):
        dmg = self.damage(extra)
        return dict((s, self.suitHp.get(s, 0) - dmg.get(s, 0)) for s in self.suits)

    def canCash(self):
        """Can anyone in the fight still cash in a lure - a Throw or Squirt on the lured Cog (the knock-back)?
        (09-30) the soak's 26-round street battle: every pouch was out of Throw and Squirt, yet a bot lured every
        round and the Sound/Drop bots held back for a knock-back nobody could give. A real player's pouch is
        unknown to the bots: counted as yes (so is a board without pouches)."""
        if self.pouches is None or any(t in (THROW, SQUIRT) for av, t, l, tg in self.picks):
            return True
        for av in self.toons:
            p = self.pouches.get(av)
            if p is None or THROW in p or SQUIRT in p:
                return True
        return False

    def canLure(self):
        """Can anyone in the fight still lure (a trap waits for one)? Unknown pouches count as yes."""
        if self.pouches is None:
            return True
        return any(self.pouches.get(av) is None or LURE in self.pouches[av] for av in self.toons)

    def trapped(self):
        """Cogs with a trap waiting under them (set, or being set this round): a lure on one does damage."""
        return set(self.traps) | set(self.trapping())


def plan(brain, board, have, exp, players, bots):
    """(track, level, target, why) for this bot, or PASS."""
    me = brain.bot.avId
    usable = lambda t: sorted(have.get(t, []))

    def mine(t, l, tg):
        return (me, t, l, tg)

    luring = board.luring()
    sound = board.soundQueued()
    anyLured = bool(board.lured)
    # (09-30) a lure only pays when someone can cash it in (a Throw/Squirt knock-back, or a trap under it); with
    # nobody able to, Sound on the lured Cogs is the damage there is (it wakes them - they would stay lured for nothing)
    cash = board.canCash()
    alive = [s for s in board.suits if board.remaining().get(s, 0) > 0]
    if not board.suits:
        return PASS, -1, -1, 'no Cog to hit this round (they are still joining)'

    # 0. SURVIVAL (the owner's rule): in danger nobody may go sad: heal who would, then stop the most threat
    inDanger = False
    if getattr(board, 'threat', None):
        inDanger, left, weight, atRisk = danger(board)
        if inDanger:
            pick = dangerPick(board, me, have, exp, left, weight, atRisk)
            if pick is not None:
                return pick

    # 1. Toon-Up first when a toon is low (the queued heals counted)
    if usable(HEAL):
        queued = board.healQueued()
        low = []
        for t in board.toons:
            if t == me:
                continue
            mx = board.toonMax.get(t) or 0
            hp = board.toonHp.get(t)
            if not mx or hp is None or hp <= 0:
                continue
            after = min(mx, hp + queued.get(t, 0))
            if after < LOW * mx:
                low.append((after / float(mx), t, mx - after))
        if low:
            low.sort()
            frac, t, need = low[0]
            singles = [l for l in usable(HEAL) if not isGroup(HEAL, l)]
            groups = [l for l in usable(HEAL) if isGroup(HEAL, l)]
            if len(low) >= 2 and groups:
                enough = [l for l in groups if gagDamage(HEAL, l, exp) / float(max(1, len(board.toons) - 1)) >= need]
                return HEAL, (min(enough) if enough else max(groups)), -1, 'group toon-up (%d low)' % len(low)
            if singles:
                enough = [l for l in singles if gagDamage(HEAL, l, exp) >= need]
                return HEAL, (min(enough) if enough else max(singles)), t, 'toon-up %s (needs %d)' % (t, need)
    if not alive:
        # (09-29) 'goes down' counted every queued gag as a hit; a kid's cupcake misses a quarter of the time and
        # the Cog lives and hits (the sim). A Cog that dies only through a hit likely to miss gets insurance first.
        # P8c: a spare turn (every Cog already goes down) tops up the laff actually missing, like a good kid
        # in a Cog HQ run (no playground between cells): one healer, the smallest heal that covers it
        top = topUp(board, me, usable(HEAL), exp, getattr(brain, 'kind', ''))
        if top is not None:
            return top
        ins = insurance(board, me, have, exp)          # a hurt friend first, then the insurance, then pass
        if ins is not None:
            return ins
        return PASS, -1, -1, 'every Cog already goes down this round'

    # 2. back the real player up
    for av, t, l, tg in board.picks:
        if av not in players:
            continue
        if t == TRAP and tg not in luring and tg not in board.lured and not sound:
            ls = [x for x in usable(LURE) if not isGroup(LURE, x)] or usable(LURE)
            if ls and tg in board.suits:
                l2 = max(ls)
                return LURE, l2, (-1 if isGroup(LURE, l2) else tg), 'lure onto his trap'
        if t == SOUND and usable(SOUND) and not luring:
            return pickSound(board, me, usable(SOUND), exp, 'with his sound')
        if t in (THROW, SQUIRT, DROP) and tg in board.suits and board.remaining().get(tg, 0) > 0 and usable(t):
            if t == DROP and (tg in board.lured or tg in luring):
                continue
            best = finish(board, me, [(t, x) for x in usable(t)], [tg], exp)
            # (09-29, the owner's rule 4 - least overkill): his track only when it wastes no more than another gag
            # that finishes his Cog (the sim: 'backs up his drop' wasted 42 laff a pick on a Cog needing a tap)
            other = finish(board, me, [(tt, x) for tt in (THROW, SQUIRT, DROP) for x in usable(tt)], [tg], exp)
            if best and other and 'finishes' in other[3] and waste(board, me, other) + 3 < waste(board, me, best):
                return other[0], other[1], other[2], 'finishes his Cog (%s would waste %d)' % (
                    NAMES[t], waste(board, me, best))
            if best:
                return best[0], best[1], best[2], 'backs up his %s' % NAMES[t]

    # 3. a lure when it pays (one lure a round, never with Sound)
    drops = board.dropTargets()                    # a lure would make a queued Drop miss: never both
    if usable(LURE) and not luring and not sound and not anyLured and len(alive) >= (3 if getattr(brain, 'kind', '') == 'boss' else 2)             and (cash or board.trapped() & set(alive)):
        groups = [x for x in usable(LURE) if isGroup(LURE, x)] if not drops else []
        if groups:
            return LURE, max(groups), -1, 'group lure'
        free_ = [x for x in alive if x not in drops and (cash or x in board.trapped())]
        if free_:
            s = max(free_, key=lambda x: board.remaining().get(x, 0))
            singles = [x for x in usable(LURE) if not isGroup(LURE, x)]
            if singles:
                return LURE, max(singles), s, 'lure the toughest Cog'
    # a trap on the Cog being lured this round (never where a trap is)
    if usable(TRAP) and luring and not sound:
        cands = [s for s in luring if s in alive and s not in board.traps and s not in board.trapping()
                 and s not in board.lured]
        if cands:
            s = max(cands, key=lambda x: board.remaining().get(x, 0))
            return TRAP, max(usable(TRAP)), s, 'trap under the lure'

    # 4. damage: finish a Cog with the least overkill, else add the most to the Cog closest to going down
    opts = []
    for t in ATTACKS:
        for l in usable(t):
            if t == SOUND and (luring or (anyLured and cash)):
                continue
            opts.append((t, l))
    if not opts and usable(SOUND) and not luring:
        # P8c: Sound is all this toon has left and a Cog is still lured from an earlier round: waking it beats
        # passing (no lure is queued this round, so it is not a lure + sound round)
        opts = [(SOUND, l) for l in usable(SOUND)]
        best = pickSound(board, me, usable(SOUND), exp, 'the only attack left (wakes the lured Cogs)')
        if best is not None:
            return best
    if any(o[0] != DROP for o in opts):
        # P8c: a Drop on an unlured Cog is the least accurate gag in the game (50 + track exp against the Cog's
        # defence: ~55% on a level-10+ Cog, and every Drop on it shares the one roll); a competent kid throws,
        # squirts or sounds instead while he has them (a boss battle saw three rounds of paired Drops all miss)
        opts = [o for o in opts if o[0] != DROP]
    if not opts:
        return support(board, me, usable, exp, brain, alive, 'no attack gags')
    # budget over the whole fight (the owner's rule 3): in a boss's Cog rounds, outside danger, the big gags stay
    # in the pouch for the later rounds unless they finish a Cog (finish() still sees them when nothing else can)
    boss = getattr(brain, 'kind', '') == 'boss' and not getattr(brain, 'lastGagRound', False)
    small = [o for o in opts if o[1] < (5 if boss else 6)]
    # Sound scored over every Cog it hits (the owner's rule 4): Cogs it brings down and its total damage,
    # against the best single-target pick
    if usable(SOUND) and not luring and not (anyLured and cash) and len(alive) >= 2:
        rem = board.remaining()
        sBest = None
        for l in usable(SOUND):
            after = board.remaining((me, SOUND, l, -1))
            kills = sum(1 for s in alive if after.get(s, 0) <= 0)
            total = sum(min(rem[s], rem[s] - after.get(s, 0)) for s in alive)
            key = (kills, total, -l)
            if sBest is None or key > sBest[0]:
                sBest = (key, l)
        single = finish(board, me, [o for o in (small or opts) if o[0] != SOUND], alive, exp)
        sKills, sTotal, _ = sBest[0]
        oneKill = 1 if single and 'finishes' in single[3] else 0
        oneDmg = 0
        if single:
            tg = single[2]
            oneDmg = sum(max(0, rem[s] - board.remaining((me, single[0], single[1], tg)).get(s, 0)) for s in alive)
        if sKills > oneKill or (sKills == oneKill and sTotal >= 1.5 * max(1, oneDmg)):
            return SOUND, sBest[1], -1, 'SOUND over %d Cogs: %d go down, %d damage in all (best single: %d down, %d)' % (
                len(alive), sKills, int(sTotal), oneKill, int(oneDmg))
    best = finish(board, me, small or opts, alive, exp, threatFirst=inDanger)
    if (not best or 'finishes' not in best[3]) and small and len(small) < len(opts):
        big = finish(board, me, opts, alive, exp, threatFirst=inDanger)   # a bigger gag only if it finishes a Cog the small ones can't
        if big and 'finishes' in big[3]:
            best = big
        elif best is None and big is not None:
            # P8c: ... or when every small gag left is too small to matter (the gag-size rule): a kid throws his
            # big gag rather than stand there (a VP round-one battle went 60 rounds of all four bots passing
            # with 25 throws each, all kept back)
            best = big
    if len(alive) == 1 and any(o[1] == 6 for o in opts):
        big = finish(board, me, opts, alive, exp)          # a tier-7 gag that ends the fight is worth it
        if big and 'finishes' in big[3] and (not best or 'finishes' not in best[3]):
            best = big
    if not best:
        best = finish(board, me, opts, alive, exp, chip=True)
    if best:
        return best[0], best[1], best[2], best[3]
    return support(board, me, usable, exp, brain, alive, 'nothing useful')


def support(board, me, usable, exp, brain, alive, why):
    """No attack gag can do anything this round: a kid still plays what he has before he ever passes (the owner,
    09-25) - a lure on the toughest Cog nobody is luring (no Sound queued), a trap on a Cog with none, a Toon-Up on
    whoever is missing laff. PASS only when none of those is possible."""
    luring = board.luring()
    sound = board.soundQueued()
    cash = board.canCash()
    free_ = [s for s in alive if s not in luring and s not in board.lured and s not in board.dropTargets()]
    if not cash:
        # (09-30) nobody can cash a lure in: it only stalls the fight (the soak's 26 rounds) - unless a trap waits
        free_ = [s for s in free_ if s in board.trapped()]
    if usable(LURE) and free_ and not sound and not luring:
        groups = [x for x in usable(LURE) if isGroup(LURE, x)]
        singles = [x for x in usable(LURE) if not isGroup(LURE, x)]
        if groups and not board.dropTargets() and (cash or not singles):
            return LURE, max(groups), -1, 'group lure (%s)' % why
        if singles:
            s = max(free_, key=lambda x: board.remaining().get(x, 0))
            return LURE, max(singles), s, 'lure the toughest Cog (%s)' % why
    traps = [s for s in alive if s not in board.traps and s not in board.trapping() and s not in board.lured]
    if usable(TRAP) and traps and board.canLure():       # (09-30) never a trap nobody can lure onto
        s = max(traps, key=lambda x: board.remaining().get(x, 0))
        return TRAP, max(usable(TRAP)), s, 'trap for a later lure (%s)' % why
    top = topUp(board, me, usable(HEAL), exp, 'any')
    if top is not None:
        return top
    if (usable(SOUND) or usable(DROP)) and (board.lured or luring) and cash:
        # holding is the smart play here: Sound wakes every lured Cog, a Drop misses a lured one
        return PASS, -1, -1, 'holding: my Sound would wake the lured Cogs, my Drops miss them'
    if usable(DROP) and (board.lured or luring):
        return PASS, -1, -1, 'my Drops miss the lured Cogs (%s)' % why
    return PASS, -1, -1, why


# a spare turn heals a toon below this share of his laff (P8c): a facility run has no playground between cells, so it
# tops up early; a boss battle's Toon-Ups must last the whole fight, so only a real gap
TOPUP = {'level': 0.8, 'boss': 0.6, 'any': 1.0}           # 'any': a Toon-Up is all this toon can play


def topUp(board, me, heals, exp, kind=''):
    """(HEAL, level, target, why) for a spare turn, or None: only when no Toon-Up is queued yet this round (one
    healer), on the toon missing the most laff (a group Toon-Up when two or more are low), sized to the gap."""
    if not heals or board.healQueued():
        return None
    low = []
    for t in board.toons:
        if t == me:
            continue
        mx = board.toonMax.get(t) or 0
        hp = board.toonHp.get(t)
        if not mx or hp is None or hp <= 0 or hp >= TOPUP.get(kind, 0.7) * mx:
            continue
        low.append((mx - hp, t))
    if not low:
        return None
    low.sort(reverse=True)
    need, t = low[0]
    singles = [l for l in heals if not isGroup(HEAL, l)]
    groups = [l for l in heals if isGroup(HEAL, l)]
    if len(low) >= 2 and groups:
        per = lambda l: gagDamage(HEAL, l, exp) / float(max(1, len(board.toons) - 1))
        enough = [l for l in groups if per(l) >= low[1][0]]
        return HEAL, (min(enough) if enough else max(groups)), -1, 'spare turn: group toon-up (%d missing laff)' % len(low)
    if singles:
        enough = [l for l in singles if gagDamage(HEAL, l, exp) >= need]
        return HEAL, (min(enough) if enough else max(singles)), t, 'spare turn: toon-up %s (missing %d)' % (t, need)
    if groups:
        return HEAL, min(groups), -1, 'spare turn: group toon-up (%s missing %d)' % (t, need)
    return None


def dangerPick(board, me, have, exp, left, weight, atRisk):
    """In danger: (a) Toon-Up whoever would go sad (group heals when several), (b)/(c) the play that leaves the
    least threat able to attack next round (a lure, Sound over every Cog, a kill on a different Cog), big gags
    allowed; stacking on one Cog only when it is the only Cog that can be stopped this round."""
    usable = lambda t: sorted(have.get(t, []))
    others = [t for t in atRisk if t != me]
    # (owner 09-25) a Toon-Up no longer wins by default: it is scored with the stopping plays below on the same
    # terms (who is still at risk, then the threat left), so a bot that can end the danger by stopping a Cog does -
    # a healer in 'danger' every round against two level-1 Cogs healed 17 rounds with 2 drops in its pouch
    heals = []
    if others and usable(HEAL):
        groups = [l for l in usable(HEAL) if isGroup(HEAL, l)]
        singles = [l for l in usable(HEAL) if not isGroup(HEAL, l)]
        if len(others) >= 2 and groups:
            heals.append((HEAL, max(groups), -1, 'group toon-up for %d at risk' % len(others)))
        if singles:
            t = min(others, key=lambda x: board.toonHp.get(x) or 0)
            lift = [l for l in singles if t not in danger(board, (me, HEAL, l, t))[3]]
            if lift:                                        # the smallest heal that lifts him out of reach
                heals.append((HEAL, min(lift), t, 'toon-up %s out of reach' % t))
            else:
                heals.append((HEAL, max(singles), t, 'biggest toon-up on %s' % t))
        elif groups:
            heals.append((HEAL, max(groups), -1, 'group toon-up'))
    luring = board.luring()
    sound = board.soundQueued()
    cands = []
    for t in (LURE, SOUND, THROW, SQUIRT, DROP, TRAP):
        for l in usable(t):
            if t == LURE:
                if sound:
                    continue
                if isGroup(t, l):
                    if luring or board.lured or board.dropTargets():
                        continue
                    cands.append((t, l, -1))
                else:
                    for s in left:
                        if s not in luring and s not in board.lured and s not in board.dropTargets():
                            cands.append((t, l, s))
            elif t == SOUND:
                if luring or (board.lured and board.canCash()):
                    continue
                cands.append((t, l, -1))
            elif t == TRAP:
                for s in luring:
                    if s in left and s not in board.traps and s not in board.trapping() and s not in board.lured:
                        cands.append((t, l, s))
            else:
                if isGroup(t, l):
                    continue
                for s in left:
                    if t == DROP and (s in board.lured or s in luring):
                        continue
                    cands.append((t, l, s))
    if not cands and not heals:
        return None
    rem0 = board.remaining()
    scored = []
    for t, l, tg in cands:
        ext = (me, t, l, tg)
        d, lft, w, risk = danger(board, ext)
        # no stop: credit the progress on the most threatening Cog (so a lone stoppable Cog gets the stack)
        prog = 0.0
        if w >= weight and tg in board.suits and rem0.get(tg, 0) > 0:
            delta = rem0[tg] - board.remaining(ext).get(tg, 0)
            prog = board.threat.get(tg, (0, 0, 0))[0] * min(1.0, delta / float(rem0[tg]))
        kind = {LURE: 'lure', SOUND: 'sound over every Cog', TRAP: 'trap under the lure'}.get(t, 'threat kill')
        # ties: never the Drop when another gag does the same (a Drop on an unlured Cog is the least accurate gag:
        # a danger play must land), then the smaller gag
        scored.append((len(risk), w - prog, 0, t == DROP, l, t, tg, kind))
    for t, l, tg, why in heals:
        risk = danger(board, (me, t, l, tg))[3]
        scored.append((len(risk), weight, 1, False, l, t, tg, 'heal: ' + why))   # a heal stops no Cog: ties go to a stop
    scored.sort(key=lambda x: x[:5])
    nrisk, w, isHeal, isDrop, l, t, tg, kind = scored[0]
    return t, l, tg, 'DANGER %s: leaves threat %d (was %d), %d at risk' % (kind, int(w), int(weight), nrisk)


def losing(board, me, have, exp, healers=False):
    """(urgent, why) when this bot is losing its fight, else None (the owner, 09-29: "make the bots run from fights
    they're losing" - the caller keeps his other rules: never with a real player in the fight, never in a boss
    battle, street battles only). urgent: the next Cog hit sends it sad (no Toon-Up queued for it and no healer
    in the fight). Else the sums: rounds its team needs to beat the Cogs (their laff left / its best gag x the
    toons, ~75% of hits landing; a Drop ~55%) against the rounds until it goes sad (its laff / its share of the
    Cogs' expected hits) - losing when the Cogs win by more than a round."""
    hp, mx = board.toonHp.get(me), board.toonMax.get(me)
    if not hp or hp <= 0 or not getattr(board, 'threat', None):
        return None
    inDanger, left, weight, atRisk = danger(board)
    if not left:
        return None
    heal = board.healQueued().get(me, 0)
    biggest = max([board.threat.get(s, (0, 0, 0))[1] for s in left] or [0])
    if me in atRisk and hp + heal <= biggest and not healers:
        return True, 'one more hit sends me sad (%d laff, the Cogs hit up to %d)' % (hp, biggest)
    cogs = [s for s in board.suits if board.remaining().get(s, 0) > 0]
    soundAll = not (board.lured and board.canCash())

    def bestOf(pouch, e):
        best = 0.0
        for t in ATTACKS:
            for l in pouch.get(t, []):
                # a Sound hits every Cog: its laff a round is its damage x the Cogs standing
                many = len(cogs) if t == SOUND and soundAll else 1
                best = max(best, gagDamage(t, l, e) * (0.55 if t == DROP else 0.75) * many)
        return best
    cogHp = sum(max(0, v) for v in board.remaining().values())
    if cogHp <= 0:
        return None
    # (09-30) the team's damage from each bot's own pouch (a healer or a lure-only bot is not losing a fight its
    # team is winning); a toon whose pouch is unknown (a real player) counted like me
    mine = bestOf(have, exp)
    pouches = getattr(board, 'pouches', None) or {}
    team = sum(mine if av == me or pouches.get(av) is None else bestOf(pouches[av], board.expOf(av))
               for av in board.toons)
    if team <= 0:
        # (09-30 soak) four bots out of every attack gag (Toon-Up and Trap left, no Lure) healed and passed 26 rounds
        # against one Cog: a team with no damage never wins, however little laff the Cog has left
        return False, 'losing: nobody here has an attack gag left (%d Cog laff to go)' % cogHp
    toWin = cogHp / max(1.0, team)
    toSad = (hp + heal) / max(0.5, weight / float(max(1, len(board.toons))))
    if toWin > toSad + 1:
        return False, 'losing: ~%.0f rounds to beat the Cogs, ~%.0f until I go sad' % (toWin, toSad)
    return None


def pickSound(board, me, levels, exp, why):
    """The smallest Sound that brings down the most Cogs together with what is queued."""
    rem = board.remaining()
    best = None
    for l in levels:
        after = board.remaining((me, SOUND, l, -1))
        kills = sum(1 for s in board.suits if rem.get(s, 0) > 0 >= after.get(s, 0))
        key = (-kills, l)
        if best is None or key < best[0]:
            best = (key, l)
    return SOUND, best[1], -1, why


SURE = 0.9              # a queued hit this likely to land counts as landed


def hitChance(board, av, t, l, tg):
    """How likely this pick lands, by the game's own rule (BattleCalculatorAI.__calcToonAtkHit): the gag's
    accuracy + the toon's track exp bonus (AttackExpPerTrack; unknown for a real player: none) - the Cog's defence,
    at most MaxToonAcc; a lured Cog takes every hit but a Drop (which misses it); a trap is always set. (The hits
    before it in the round add a bonus in the game; not counted - this errs towards insurance.)"""
    TBG = _tbg()
    if t in (TRAP, HEAL):
        return 1.0
    targets = board.suits if isGroup(t, l) else [tg]
    lured = board.lured | board.luring()
    if t != LURE and targets and all(s in lured for s in targets):
        return 0.0 if t == DROP else 1.0
    from toontown.battle.BattleCalculatorAI import BattleCalculatorAI
    e = board.expOf(av)
    bonus = BattleCalculatorAI.AttackExpPerTrack[e.getExpLevel(t)] if e is not None else 0
    defense = min([board.defense.get(s, 0) for s in targets] or [0])
    return min(TBG.MaxToonAcc, TBG.AvPropAccuracy[t][l] + bonus - defense) / 100.0


def insurance(board, me, have, exp):
    """(track, level, target, why) or None: every Cog goes down on paper - but does one die only through a hit that
    may miss (below SURE)? Then a gag of ANOTHER track on it (the same track on the same Cog shares that one roll,
    so it adds no chance), the smallest that still finishes it when the doubtful hits miss."""
    sure = Board.__new__(Board)
    sure.__dict__.update(board.__dict__)
    sure.picks = [p for p in board.picks if hitChance(board, p[0], p[1], p[2], p[3]) >= SURE]
    left = sure.remaining()
    doubtful = [s for s in board.suits if left.get(s, 0) > 0]
    if not doubtful:
        return None
    s = max(doubtful, key=lambda x: (board.threat.get(x, (0, 0, 0))[0] if getattr(board, 'threat', None) else 0, left[x]))
    used = set(p[1] for p in board.picks if p[3] == s or isGroup(p[1], p[2]))
    opts = []
    for t in ATTACKS:
        if t in used or (t == DROP and (s in board.lured or s in board.luring())):
            continue
        if t == SOUND and (board.luring() or (board.lured and board.canCash())):
            continue
        for l in sorted(have.get(t, [])):
            opts.append((gagDamage(t, l, exp), t, l))
    if not opts:
        return None
    enough = [o for o in opts if o[0] >= left[s]]
    d, t, l = min(enough) if enough else max(opts)
    return t, l, (-1 if isGroup(t, l) else s), 'insurance on %s (%d laff rides on hits that may miss)' % (s, left[s])


def waste(board, me, pick):
    """Laff a pick throws away: the damage past what the Cogs it hits have left (after what is queued)."""
    t, l, tg = pick[0], pick[1], pick[2]
    r0, r1 = board.remaining(), board.remaining((me, t, l, tg))
    return int(sum(-r1[s] for s in r0 if r0[s] > 0 and r1[s] < 0))


def finish(board, me, opts, targets, exp, chip=False, threatFirst=True):
    """(track, level, target, why): the least-overkill kill, else the biggest help to the nearest kill. chip=True
    (the owner, 09-25: a bot never stands there passing with gags in its pouch): when every gag left is too small
    to matter, the biggest chip at the Cog closest to going down (its hit still adds up, and joins a combo).
    threatFirst=False (09-29, out of danger): the kill that wastes the least laff first, then the biggest threat
    (threat-first put a maxed bot's wedding cake on a Cog with 6 laff left: the sim, 10 laff wasted a kill)."""
    rem = board.remaining()
    kills, helps, chips = [], [], []
    for s in targets:
        r = rem.get(s, 0)
        if r <= 0:
            continue
        for t, l in opts:
            if t == DROP and (s in board.lured or s in board.luring()):
                continue
            if isGroup(t, l) and t != SOUND:
                continue
            tg = -1 if isGroup(t, l) else s
            after = board.remaining((me, t, l, tg)).get(s, 0)
            delta = r - after
            if delta <= 0:
                continue
            thr = board.threat.get(s, (0, 0, 0))[0] if getattr(board, 'threat', None) else 0
            if after <= 0:
                kills.append((-thr, -after, l, t, s, tg))    # the most threatening Cog, least overkill, smaller gag
            elif delta >= TRIVIAL * min(r, board.suitHp.get(s, r) or r):
                helps.append((-thr, r, -delta, t, l, s, tg)) # the most threatening, nearest to dying, most damage
            else:
                # a trivial hit on a big Cog (the owner's gag-size rule): only when nothing bigger is left (chip)
                chips.append((r, -thr, -delta, t, l, s, tg))
    if kills:
        kills.sort(key=(lambda k: k) if threatFirst else (lambda k: (k[1], k[0], k[2])))
        thr, over, l, t, s, tg = kills[0]
        return t, l, tg, 'finishes %s (overkill %d)' % (s, over)
    if helps:
        helps.sort()
        thr, r, nd, t, l, s, tg = helps[0]
        return t, l, tg, 'adds %d to %s (%d left)' % (-nd, s, r)
    if chip and chips:
        chips.sort()                                          # nearest to going down, most threatening, most damage
        r, thr, nd, t, l, s, tg = chips[0]
        return t, l, tg, 'chips %d at %s (%d left): nothing bigger left' % (-nd, s, r)
    return None



# ---- threat: what each Cog can do to the toons next round (the owner's survival rule) ------------------------
def cogLevel(dnaBlob, rel):
    """A Cog's real level from its setLevelDist. setLevelDist is NOT the level: it is the row (0-4) of the Cog's
    own SuitAttributes tables (DistributedSuitBaseAI.setLevel); the level is SuitBase.getActualLevel's
    getActualFromRelativeLevel(name, row) + 1 (a level-1 Flunky sends 0, a level-9 Big Cheese sends 2)."""
    try:
        from toontown.suit import SuitDNA
        from toontown.battle import SuitBattleGlobals as SBG
        dna = SuitDNA.SuitDNA()
        dna.makeFromNetString(dnaBlob)
        return SBG.getActualFromRelativeLevel(dna.name, max(0, min(4, rel))) + 1
    except Exception:
        return rel + 1


def cogDefense(dnaBlob, rel):
    """A Cog's defence against a toon's hit (SuitAttributes 'def' in its row; BattleCalculatorAI.__targetDefense)."""
    try:
        from toontown.suit import SuitDNA
        from toontown.battle import SuitBattleGlobals as SBG
        dna = SuitDNA.SuitDNA()
        dna.makeFromNetString(dnaBlob)
        return SBG.SuitAttributes[dna.name]['def'][max(0, min(4, rel))]
    except Exception:
        return 0


def suitThreat(dnaBlob, rel):
    """(expected damage, biggest single hit, biggest group hit) of a Cog's attack next round, from the game's
    own tables (SuitBattleGlobals.SuitAttributes: damage x frequency in the Cog's row). rel = its setLevelDist,
    which IS the row (09-25 fix: it was taken for the level, so every Cog above its type's lowest level read as
    its weakest row - bots under-read what mid and high Cogs hit for)."""
    level = rel
    try:
        from toontown.suit import SuitDNA
        from toontown.battle import SuitBattleGlobals as SBG
        dna = SuitDNA.SuitDNA()
        dna.makeFromNetString(dnaBlob)
        data = SBG.SuitAttributes[dna.name]
        i = max(0, min(4, rel))
        tot = sum(a[3][i] for a in data['attacks']) or 1
        exp = sum(a[1][i] * a[3][i] for a in data['attacks']) / float(tot)
        single = max([a[1][i] for a in data['attacks'] if not SBG.SuitAttacks[a[0]][1]] or [0])
        group = max([a[1][i] for a in data['attacks'] if SBG.SuitAttacks[a[0]][1]] or [0])
        return exp, max(single, group), group
    except Exception:
        return level * 2.0, level * 3.0, 0


def neutralized(board, extra=None):
    """Cogs the queued picks (plus extra) stop this round: killed, or lured (and no Sound to wake them)."""
    rem = board.remaining(extra)
    picks = board.picks + ([extra] if extra else [])
    sound = any(p[1] == SOUND for p in picks)
    lure = set()
    for av, t, l, tg in picks:
        if t == LURE:
            lure |= set(board.suits) if isGroup(t, l) else {tg}
    out = set(s for s in board.suits if rem.get(s, 0) <= 0)
    if not sound:
        # (09-29) a hit on a lured Cog knocks it back awake (BattleCalculatorAI.__clearLuredSuitsByAttack) and it
        # attacks this same round: only a lured Cog nobody hits is stopped (the sim: a kid's throw woke one the
        # bots counted as stopped, and it took him from 15 laff to sad)
        dmg = board.damage(extra)
        out |= set(s for s in (lure | board.lured) if dmg.get(s, 0) <= 0)
    return out


def danger(board, extra=None):
    """(in danger, cogs left able to attack, their weighted threat, toons that could go sad) given the picks."""
    stop = neutralized(board, extra)
    left = [s for s in board.suits if s not in stop]
    weight = sum(board.threat.get(s, (0, 0, 0))[0] for s in left)
    biggest = max([board.threat.get(s, (0, 0, 0))[1] for s in left] or [0])
    groups = sum(board.threat.get(s, (0, 0, 0))[2] for s in left)
    heal = board.healQueued() if extra is None else Board.healQueued(_with(board, extra))
    atRisk = []
    for t in board.toons:
        hp = board.toonHp.get(t)
        if hp is None or hp <= 0:
            continue
        hp = min(board.toonMax.get(t) or hp, hp + heal.get(t, 0))
        # one big hit, or the group attacks plus a fair share of the rest
        if hp <= biggest or hp <= groups + weight / float(max(1, len(board.toons))):
            atRisk.append(t)
    return bool(atRisk), left, weight, atRisk


def _with(board, extra):
    b = Board.__new__(Board)
    b.__dict__.update(board.__dict__)
    b.picks = board.picks + [extra]
    return b

# ---- the proof log -----------------------------------------------------------------------------------------
_LOG = [None]


def log(director, line):
    try:
        if _LOG[0] is None:
            _LOG[0] = open(os.path.join(director.runDir, 'bots-picks.log'), 'a')
        _LOG[0].write('%s %s\n' % (time.strftime('%H:%M:%S'), line))
        _LOG[0].flush()
    except Exception:
        pass


def audit(board, players, stats):
    """At the movie: the round's final picks, the bot-made conflicts counted (a player's own pick is his)."""
    order = board.picks
    lureTargets = {}
    sound = False
    heals = []
    for av, t, l, tg in order:
        if t == LURE:
            for s in (board.suits if isGroup(t, l) else [tg]):
                lureTargets.setdefault(s, []).append(av)
        if t == SOUND:
            sound = True
        if t == HEAL:
            heals.append((av, t, l, tg))
    dbl = sum(1 for s, avs in lureTargets.items() if len(avs) > 1 and any(a not in players for a in avs[1:]))
    conflict = 1 if sound and lureTargets and any(av not in players for av, t, l, tg in order if t in (LURE, SOUND)) else 0
    # a heal is redundant if the heals before it already brought every toon to LOW or better
    redundant = 0
    for i, h in enumerate(heals):
        if h[0] in players:
            continue
        before = Board.__new__(Board)
        before.__dict__.update(board.__dict__)
        before.picks = heals[:i]
        q = before.healQueued()
        ok = all((board.toonHp.get(t) or 0) + q.get(t, 0) >= LOW * (board.toonMax.get(t) or 1)
                 for t in board.toons if t != h[0] and (board.toonHp.get(t) or 0) > 0)
        if ok and i > 0:
            redundant += 1
    dmg = board.damage()
    over = 0
    kills = 0
    for s in board.suits:
        hp = board.suitHp.get(s, 0)
        if dmg.get(s, 0) >= hp > 0:
            kills += 1
            over += dmg[s] - hp
    weakHits = 0
    b0 = Board.__new__(Board)
    b0.__dict__.update(board.__dict__)
    b0.picks = []
    if getattr(board, 'threat', None) and danger(b0)[0]:
        stats.count('pick_danger_rounds')
        leftAfter = set(danger(board)[1])
        alive0 = [s for s in board.suits if board.suitHp.get(s, 0) > 0]
        if len(alive0) >= 2:
            thr = lambda s: board.threat.get(s, (0, 0, 0))[0]
            weakest = min(alive0, key=thr)
            for av, t, l, tg in order:
                if av in players or t not in ATTACKS or isGroup(t, l) or tg != weakest:
                    continue
                if any(thr(s) > thr(weakest) for s in leftAfter):
                    weakHits += 1
        stats.count('pick_danger_weakest_target', weakHits)
    stats.count('pick_rounds')
    stats.count('pick_double_lure', dbl)
    stats.count('pick_lure_sound', conflict)
    stats.count('pick_redundant_heal', redundant)
    stats.count('pick_kills', kills)
    stats.count('pick_overkill_total', int(over))
    return dbl, conflict, redundant, kills, int(over), weakHits
