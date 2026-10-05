# Changes: server files ported or fixed

Toontown Classic's rule: server code may come in from elsewhere only to fill a server file that is an
empty shell in Open Toontown, with everything that was not in the 2013 game stripped, and a fix may only
make the game do what the 2013 game did. One row per file: where it came from, what was stripped, and the
2013 behaviour it restores.

Sources: "PR #92 / #93 / #126" = Open Toontown pull requests (holidays, estates, parties). "Disney leak
7d3b71f" = Disney's original Toontown server source as kept in the toontown-org archive (commit 7d3b71f).
"ours, fix" / "fix" = a fix written for this tree. "built" = written new where no original exists.

| File | Source | Mods stripped | 2013 behaviour it restores |
|------|--------|---------------|----------------------------|
| toontown/ai/ToontownAIRepository.py | PR #93 | none | starts the EstateManagerAI (estates reachable) |
| toontown/estate/DistributedAnimatedStatuaryAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedBankAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedBankMgrAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedChangingStatuaryAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedClosetAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedEstateAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedFireworksCannonAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedFlowerAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedFurnitureItemAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedFurnitureManagerAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedGagTreeAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedGardenAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedGardenBoxAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedGardenPlotAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedHouseAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedHouseDoorAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedHouseInteriorAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedHouseItemAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedLawnDecorAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedMailboxAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedPhoneAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedPlantBaseAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedStatuaryAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedTargetAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedToonStatuaryAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/DistributedTrunkAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/estate/EstateManagerAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/hood/Place.py | PR #93 | none | client crash fix: task.done -> Task.done, going home works again |
| toontown/safezone/DistributedETreasureAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/safezone/EFlyingTreasurePlannerAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/safezone/ETreasurePlannerAI.py | PR #93 | none (original Disney AI source) | estates: filled an empty shell |
| toontown/toonbase/ToontownGlobals.py | PR #93 | none | MAX_NUM_OF_TOONS = 6 constant the estate code reads |

Not taken from PR #93: toontown/estate/DistributedCannonAI.py (ours was not a shell; PR only reformatted it).
| toontown/fishing/DistributedPondBingoManagerAI.py | Disney leak (toontownretro/toontown-org 7d3b71f) | none; py3 only (absolute imports, has_key, keys() snapshots) | fish bingo cards at ponds (runs only while the bingo holiday is on) |
| toontown/fishing/DistributedFishingPondAI.py | Disney leak 7d3b71f | none; py3 only | ponds with moving fish targets and bingo hookup |
| toontown/fishing/DistributedFishingTargetAI.py | Disney leak 7d3b71f | none; py3 only | the shadow targets fish swim to |
| toontown/fishing/FishManagerAI.py | Disney leak 7d3b71f | none; py3 only; `av.bingoCheat` (magic-word attr) read via getattr | catches, tank, collection, trophies, laff bonus |
| toontown/safezone/DistributedFishingSpotAI.py | Disney leak 7d3b71f | none; py3 only (`is -1` -> `== -1`) | fishing docks: cast, catch, sell to the fisherman |
| toontown/ai/ToontownAIRepository.py | Disney leak 7d3b71f | none | findFishingSpots (was a TODO), fishManager, bingoMgr slot, handleAvCatch, createPondBingoMgrAI |
| toontown/ai/RepairAvatars.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyCannonAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyCannonActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyCatchActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyCogActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyDance20ActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyDanceActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyDanceActivityBaseAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyFireworksActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyJukebox40ActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyJukeboxActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyJukeboxActivityBaseAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyTeamActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyTrampolineActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyTugOfWarActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyValentineDance20ActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyValentineDanceActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyValentineJukebox40ActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyValentineJukeboxActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyValentineTrampolineActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyVictoryTrampolineActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyWinterCatchActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyWinterCogActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/parties/DistributedPartyWinterTrampolineActivityAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/safezone/DistributedPartyGateAI.py | PR #126 | none | parties: filled an empty shell |
| toontown/uberdog/DistributedPartyManagerUD.py | PR #126 | none | parties: filled an empty shell |
| (18 non-shell files) | PR #126 | DistributedToonAI kept ours; Jukebox40 compare fixed | py3 enum fixes + UD/AI party wiring, reviewed hunk by hunk (b2d77c9) |
| (47 files) | PR #92 | effects/ Bingo duplicates dropped; HolidayInfoWeekly xrange->range | holidays: HolidayManagerAI schedule, NewsManagerAI, invasions, buff/zero, silly meter, bingo night; all were shells or near-shells, reviewed (KEEP-ALL) |
| toontown/ai/HolidayManagerAI.py | Disney leak 7d3b71f | none | 34 lines the PR had commented out restored: Fish Bingo Wednesday + Silly Saturday bingo (fishing now exists) |
| toontown/classicchars/DistributedWitchMinnieAI.py | Disney leak 7d3b71f | none; py3 only (relative imports) | Halloween Witch Minnie (CostumeManagerAI swaps her in) |
| toontown/classicchars/DistributedVampireMickeyAI.py | Disney leak 7d3b71f | none; py3 only | Halloween Vampire Mickey |
| toontown/classicchars/DistributedWesternPlutoAI.py | Disney leak 7d3b71f | none; py3 only | Western Pluto (costume holiday) |
| toontown/classicchars/DistributedSuperGoofyAI.py | Disney leak 7d3b71f | none; py3 only | Super Goofy (costume holiday) |
| toontown/ai/DistributedHydrantZeroMgrAI.py | Disney leak 7d3b71f | none | Hydrant Zero phase event (Silly Meter holidays) |
| toontown/ai/DistributedMailboxZeroMgrAI.py | Disney leak 7d3b71f | none | Mailbox Zero phase event |
| toontown/ai/DistributedTrashcanZeroMgrAI.py | Disney leak 7d3b71f | none | Trashcan Zero phase event |
| toontown/ai/DistributedPolarPlaceEffectMgrAI.py | Disney leak 7d3b71f | none | Polar Place holiday effect |
| toontown/ai/DistributedResistanceEmoteMgrAI.py | Disney leak 7d3b71f | none | Resistance emote giveaway |
| toontown/ai/DistributedSillyMeterMgrAI.py | Disney leak 7d3b71f | none | Silly Meter in Toon Hall |
| toontown/ai/DistributedTrickOrTreatTargetAI.py | Disney leak 7d3b71f | none | Trick-or-treat doors (Halloween hunt) |
| toontown/uberdog/DistributedMailManagerAI.py | Disney leak 7d3b71f | none | AI half of mail (mail count to the toon); UD half NOT yet ported |
| toontown/uberdog/DistributedDeliveryManagerAI.py | Disney leak 7d3b71f | none; py3 only (print()) | AI half of catalog gifting; UD half NOT yet ported |
| toontown/safezone/DistributedEFlyingTreasureAI.py | Disney leak 7d3b71f | none | estate flying treasures |
| toontown/shtiker/DeleteManagerAI.py | Disney leak 7d3b71f | none; py3 only (has_key) | deleting items from the attic/closet |
| toontown/racing/DistributedProjectileAI.py | Disney leak 7d3b71f | none | kart race projectiles |
| toontown/uberdog/DistributedDeliveryManagerUD.py | Disney leak 7d3b71f | AsyncRequest/DistributedToonUD/LRUlist -> dbInterface.queryObject + field update of setGiftSchedule, per-toon queue; checks unchanged; reject now reports the price (leak refunded 0); givePartyRefund not in leak, not added | catalog gifting UD half; generated in AI + UD repos |
| toontown/uberdog/DistributedMailManagerUD.py | Disney leak 7d3b71f | MySQL ttMaildb -> JSON MailDb (PartyDb pattern) | simple mail = Disney test feature; left ungenerated like the leak (vanilla) |
| toontown/estate/DistributedPhoneAI.py (checkAvatarThenGift) | ours, fix | OTP DatabaseObject -> dbInterface.queryObject | gift target check; line 137 houseId DatabaseObject still OTP (dead on Astron) |

| toontown/catalog/CatalogManagerAI.py | Disney leak 7d3b71f | none; py3 only (relative import, has_key, dropped 2 unused imports RepairAvatars/Functor) | weekly catalog, purchaseItem, payForGiftItem, refundMoney; isItemReleased not in leak, not added (only behind want-check-accessory-sanity, default off) |
| toontown/catalog/Catalog{Chat,Flooring,Moulding,PetTrick,Wainscoting,Wallpaper}Item.py | ours, fix | 2to3 bug: local `list` shadowed builtin `list(...)` -> iterate .keys() directly | CatalogGenerator could not import at all |
| toontown/coderedemption/TTCodeRedemptionMgrAI.py | Disney leak 7d3b71f | stress test dropped; py3 only (next(), PythonUtil imports), delete() cancels retry timers | per-district code redemption manager: client redeemCode -> UD with retry, result back to the toon; generated in the AI repo (UberZone, want-code-redemption) |
| toontown/coderedemption/TTCodeRedemptionMgrUD.py | Disney leak 7d3b71f (MgrUD + TTCodeRedemptionDB) | HTML admin page, reCAPTCHA, MySQL, self-tests dropped -> JSON CodeDb at astron/databases/coderedemption.json (config coderedemptiondb-local-file); lot methods kept as plain Python (createManualLot/createLot/deleteLot/lookup...), createLot uses random.SystemRandom instead of NonRepeatableRandomSource; admin-form checks moved into createManualLot; client-direct redeemCode (global 4695) answered too | code checks, spam block, auto codes single-use + expiry, manual codes unlimited (leak: no expiry check on manual lots); generated in the UD repo |
| toontown/coderedemption/TTCodeDict.py | Disney leak 7d3b71f | none; py3 only (print(), raise Exception, //) | code characters, readable-code normalisation, code-space obfuscation (new file here) |
| toontown/coderedemption/TTCodeRedemptionSpamDetector.py | Disney leak 7d3b71f | none; py3 only (imports, list(keys), range, next()) | too-many-bad-codes time penalty (new file here) |
| toontown/rpc/AwardManagerUD.py | Disney leak 7d3b71f | HTML award pages dropped (dc path only); GetToonsRequest -> dbInterface.queryObject into GiftToon, per-toon queue + written-blob cache; bad bean amount / unknown type replies UnknownError (leak asserted); non-toon -> NonToon | giveAwardToToon: validate, append to the award schedule (1 minute), reply; generated in the UD repo |
| toontown/uberdog/DistributedDeliveryManagerUD.py (GiftToon) | Disney leak 7d3b71f (toon/DistributedToonUD.py) | none | GiftToon gains name + checkForDuplicateItem/checkForItemInCloset/checkForItemAlreadyWorn for the award manager |
| toontown/coderedemption/TTCodeRedemptionConsts.py, toontown/rpc/AwardManagerConsts.py, TTCodeRedemptionMgr.py | ours, fix | decompile: Enum was never defined -> IntEnum(start=0); client mgr missing SerialMaskedGen import | nothing could import them |
| (code redemption) 2013 codes | Disney leak 7d3b71f | none | the leak holds no real codes (they lived in MySQL); only self-test strings ('stresstest', '!!!', 'HWF'), not shipped; no lots seeded |
| toontown/ai/DistributedGreenToonEffectMgrAI.py | built (no Disney source) | n/a | Ides of March: phrase 30450 in zone 5819 -> CEGreenToon, 1440 min (Disney blog: "lasted only for a day"), hood 0; pattern = leak PolarPlace mgr |
| toontown/ai/GreenToonEventMgrAI.py + HolidayManagerAI IDES_OF_MARCH | built (no Disney source) | n/a | Mar 14-20 yearly; end date = 2012 Disney blog, start date from fan rebuild toontownretro/toontown ccf7b87 |
| toontown/estate/DistributedEstateAI.py | fix | n/a | types 234-237 spawn DistributedAnimatedStatuaryAI (Flappy Cog; plain class loaded a prefix path) |
| toontown/friends/TTFriendsManagerUD.py + TTFriendsManager.py (global 4666), etc/toon.dc, astrond.yml | built (TTR/Stride pattern, no Disney source) | n/a | friends list + online dots: answers the 2013 CLIENT_GET_FRIEND_LIST / CLIENT_FRIEND_ONLINE / CLIENT_FRIEND_OFFLINE as field updates fed to the unchanged 2013 client handlers; friends are declared to the client so whisper / teleport from the list reach them anywhere |
| toontown/distributed/ToontownClientRepository.py (getAvatarDetails, removeFriend, sendGetFriendsListRequest) | fix | n/a | Details panel reads laff, gags, tracks, district and location from the database for any toon, same zone or not (2013 CLIENT_GET_AVATAR_DETAILS; was "Unable to get details"); removing a friend goes through the UD |
| otp/friends/FriendManagerAI.py (_tellFriendsList) | fix | n/a | after a friendship is made both online toons get their refreshed friends list with the new friend's online dot, as the 2013 server sent it |
| toontown/minigame/DistributedIceGameAI.py (enterWaitEndingPositions) | fix | n/a | Ice Slide: the endingPositions wait times out into processEndingPositions (its own, never-wired endingPositionsTimeout) instead of an illegal FSM request that froze the match forever when one client never reported; averages the positions that did arrive, as the 2013 game carried on |
| toontown/minigame/DistributedTargetGameAI.py (handleTimeout) | fix | n/a | Toon Slingshot: the 120 s per-round setPlayerDone wait finishes the round on timeout (allAvatarsScore) instead of doing nothing, so a silent or stuck client no longer freezes the round for everyone |
| toontown/suit/DistributedBossbotBossAI.py (requestServeFood) | fix | n/a | CEO serving round: a toon that tries to serve a hungry diner before it has ever picked up food no longer raises a KeyError in the AI (toonFoodStatus.get(avId)); serving still needs food in hand, as in 2013 |
| toontown/minigame/DistributedTagGame.py (imports) | fix | n/a | Toon Tag: the client imports NametagGlobals again (the 2013 client had it through its star imports); without it the client crashed in onstage the moment a Tag game started, which aborted the game for every player |
| toontown/minigame/DistributedCogThiefGame.py (handleEnterBarrel) | fix | n/a | Cog Thief: a leftover developer pdb.set_trace() (a Cog knocked back by a pie in the same frame it touched a barrel) froze the client until the server ejected it for a missed heartbeat; it now logs a warning and carries on |
| toontown/battle/MovieLure.py (doLures, small Cogs) | fix | n/a | a lure movie that the battle cuts short (finish()) re-parents the trap prop only if it still exists (the file's own safeWrtReparentTo, as the big-Cog path already did); the 2013 release Panda ignored the empty-node call, the Panda 1.11 client raised an assertion and crashed in a boss battle |
| toontown/toon/DistributedToonAI.py (setLocation) | fix (Astron-port bug, not a 2013 change) | n/a | the Astron port wrote setLastHood + setDefaultZone (db fields) to the database on EVERY location change, so a toon walking a street rewrote itself on every visgroup; now written only when the hood changes (same values as before, P10b) |
| toontown/shtiker/PurchaseManagerAI.py (timeIsUp) | fix | n/a | a purchase screen only shut down on a final setInventory or an exit event, so one made after every player had left, or waiting on a player who never reports, stayed forever with its minigame zone; after its countdown it now shuts down at once when nobody is left to report, else after a 30 s grace for stragglers (P10b) |

| toontown/suit/DistributedLawbotBossAI.py (hitDefensePan, hitProsecutionPan) | fix | n/a | toon.dc declares both clsend but the AI had no receivers; restored as validated receivers with the 2013 behaviour: the 2013 client scores a defense-pan pie through hitBoss(panDamage) and ignores the prosecution pan, so neither changes the fight |
| toontown/minigame/DistributedTravelGame.py (import) | 2013 client (NametagGlobals came in through `pandac.PandaModules`/libotp) | n/a | the Trolley Tracks board game used NametagGlobals with no import (the Astron port dropped libotp's star import), so every client dealt it crashed (NameError at enterInputChoice); now imported from panda3d.otp as every other client module does (sweep fix) |
| toontown/ai/HolidayManagerAI.py (Day enum) | fix (Python 3 port bug, not a 2013 change) | n/a | the 2013 Day enum counts MONDAY = 0 (time.localtime().tm_wday); the port's IntEnum counted from 1, so every weekly holiday ran a day late (Trolley Tracks, the 2013 Thursday trolley holiday, ran on Fridays); start=0 as HolidayInfoRelatively already has (sweep fix) |
| toontown/ai/ToontownAIRepository.py (bankMgr) | fix | n/a | the estate bank's DistributedBankAI.transferMoney calls air.bankMgr.transferMoneyForAv, but nothing started DistributedBankMgrAI (the original Disney file, already in the tree): any bank visit, even walking away (it sends 0), raised AttributeError and closed the whole district. Started in the management zone like the other managers; 2013 rules (an over-limit move is refused) |
| toontown/estate/EstateManagerAI.py (__handleUnexpectedExit) | fix | n/a | getEstateZone registers the exit handler with the toon object, but it looked the toon up as an avId, found nothing and never cleaned up: after the owner quit or crashed at the estate, the estate stayed active and the owner's next visit hung in teleportOut forever. Now cleans up (unmap + the 5 s unload) |
