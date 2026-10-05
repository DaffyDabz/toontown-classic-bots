"""Minigame bot brains (TTBOTS P6). One module per game; toontown/bots/activities/trolley.py runs
the trolley ride, the minigame framework (base.GameSession) and the purchase screen, and asks
brainFor(className) for the brain of the game the trolley picked.

Every Classic trolley game has a brain (P6a: Race, Cannon, Pattern, TugOfWar, Catch, Diving, Photo,
Vine; P6b: Tag, Ring, Maze, Target, Pairing, Ice, CogThief, TwoD). Travel, the
trolley-holiday board game, has one too. A game with no brain module gets NO brain: the bot still
joins, readies and acks the exit (the framework).
"""
import importlib

BRAINS = {                       # dclass name -> module in this package (class Brain inside)
    'DistributedRaceGame': 'race',
    'DistributedCannonGame': 'cannon',
    'DistributedTagGame': 'tag',
    'DistributedPatternGame': 'pattern',
    'DistributedRingGame': 'ring',
    'DistributedMazeGame': 'maze',
    'DistributedTugOfWarGame': 'tugofwar',
    'DistributedCatchGame': 'catch',
    'DistributedDivingGame': 'diving',
    'DistributedTargetGame': 'target',
    'DistributedPairingGame': 'pairing',
    'DistributedVineGame': 'vine',
    'DistributedIceGame': 'ice',
    'DistributedCogThiefGame': 'cogthief',
    'DistributedTwoDGame': 'twod',
    'DistributedPhotoGame': 'photo',
    'DistributedTravelGame': 'travel',      # sweep fix: the trolley-holiday board game (votes like a kid)
}
# games a toon that does nothing cannot stall / abort / cancel (each has its own timer and waits on nobody)
SITOUT_SAFE = ('DistributedTagGame', 'DistributedMazeGame', 'DistributedCogThiefGame')

NAMES = {'DistributedRaceGame': 'Race', 'DistributedCannonGame': 'Cannon', 'DistributedTagGame': 'Tag',
         'DistributedPatternGame': 'Pattern', 'DistributedRingGame': 'Ring', 'DistributedMazeGame': 'Maze',
         'DistributedTugOfWarGame': 'TugOfWar', 'DistributedCatchGame': 'Catch', 'DistributedDivingGame': 'Diving',
         'DistributedTargetGame': 'Target', 'DistributedPairingGame': 'Pairing', 'DistributedVineGame': 'Vine',
         'DistributedIceGame': 'Ice', 'DistributedCogThiefGame': 'CogThief', 'DistributedTwoDGame': 'TwoD',
         'DistributedPhotoGame': 'Photo', 'DistributedTravelGame': 'Travel'}
_cache = {}


def brainFor(className):
    mod = BRAINS.get(className)
    if mod is None:
        return None
    if mod not in _cache:
        name, _, cls = mod.partition(':')
        _cache[mod] = getattr(importlib.import_module('toontown.bots.minigames.' + name), cls or 'Brain')
    return _cache[mod]


def gameName(className):
    return NAMES.get(className, className)
