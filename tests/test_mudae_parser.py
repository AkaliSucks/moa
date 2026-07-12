import pytest

from moa.parser.mudae import MudaeParseError, MudaeTextParser


TOP_PAGE = """🏆 TOP 1000

#1 - Hatsune Miku - VOCALOID
#2 - Zero Two 💞 - DARLING in the FRANXX
#3 - Rem - Re:Zero kara Hajimeru Isekai Seikatsu
#4 - Saber - Fate/stay night
#5 - Megumin - Kono Subarashii Sekai ni Shukufuku wo!
#6 - Rias Gremory - High School DxD
#7 - Power - Chainsaw Man
#8 - Nami - One Piece
#9 - Mai Sakurajima 💞 - Seishun Buta Yarou
#10 - 2B - NieR: Automata
#11 - Satoru Gojo - Jujutsu Kaisen
#12 - Makima - Chainsaw Man
#13 - Asuna - Sword Art Online
#14 - Albedo - Overlord
#15 - Mikasa Ackerman - Attack on Titan
Image
Page 1 / 67
"""


CHARACTER_DETAILS = """$im mai sakurajima
Mudae
APP
Mai Sakurajima
Seishun Buta Yarou :female:
Animanga roulette · 929:kakera:
Claim Rank: #9
Like Rank: #19
Bunny Girl Senpai (+4)
bunny
Image
"""

CURRENT_CHARACTER_DETAILS = (
    "Mai Sakurajima\n"
    "Seishun Buta Yarou\n"
    "Animanga roulette \u00b7 1,385 Kakera \u00b7 Keys (6)\n"
    "Claim Rank: #9\n"
    "Like Rank: #19\n"
    "Bunny Girl Senpai (+4)\n"
)


ROLL = """Airi Jinguuji
Mob kara Hajimaru Tansaku Eiyuutan
Claims: #55,003
27:kakera:
Image
"""

HAREM_KEY_PAGE = """Albedo · :goldkey:  (7)
Mai Sakurajima · :goldkey:  (6)
Miku Nakano · :goldkey:  (6)
Ishtar · :goldkey:  (6)
Saber · :silverkey:  (5)
Megumin · :silverkey:  (5)
Power · :silverkey:  (5)
Emilia · :silverkey:  (5)
Xenovia Quarta · :silverkey:  (5)
Cosmo (CSM) · :silverkey:  (5)
Zero Two · :silverkey:  (4)
Rem · :silverkey:  (4)
Nami · :silverkey:  (4)
Mikasa Ackerman · :silverkey:  (4)
Nezuko Kamado · :silverkey:  (4)
Image
Page 1 / 6
"""


def test_parse_top_page_from_copied_mudae_output() -> None:
    page = MudaeTextParser().parse_top_page(TOP_PAGE)

    assert page.limit == 1000
    assert page.page_number == 1
    assert page.page_count == 67
    assert len(page.characters) == 15
    assert page.characters[1].name == "Zero Two"
    assert page.characters[1].claim_rank == 2


def test_parse_top_page_accepts_selected_rank_rows_without_embed_metadata() -> None:
    page = MudaeTextParser().parse_top_page(
        "#9 - Mai Sakurajima 💞 - Seishun Buta Yarou\n#10 - 2B - NieR: Automata"
    )

    assert page.limit is None
    assert page.page_number is None
    assert page.page_count is None
    assert [character.name for character in page.characters] == ["Mai Sakurajima", "2B"]


def test_parse_character_details_from_copied_im_output() -> None:
    character = MudaeTextParser().parse_character_details(CHARACTER_DETAILS)

    assert character.name == "Mai Sakurajima"
    assert character.series == "Seishun Buta Yarou"
    assert character.gender == "female"
    assert character.roulette == "animanga"
    assert character.kakera_value == 929
    assert character.claim_rank == 9
    assert character.like_rank == 19


def test_parse_current_im_layout_with_visual_kakera_and_key_icons() -> None:
    character = MudaeTextParser().parse_character_details(CURRENT_CHARACTER_DETAILS)

    assert character.name == "Mai Sakurajima"
    assert character.series == "Seishun Buta Yarou"
    assert character.gender is None
    assert character.roulette == "animanga"
    assert character.kakera_value == 1385
    assert character.claim_rank == 9
    assert character.like_rank == 19


def test_parse_im_layout_without_the_word_roulette() -> None:
    character = MudaeTextParser().parse_character_details(
        "Ishtar\n"
        "Fate/Grand Order\n"
        "Game & Animanga \u00b7 624 Kakera \u00b7 Key (6)\n"
        "Claim Rank: #150\n"
        "Like Rank: #215\n"
    )

    assert character.name == "Ishtar"
    assert character.series == "Fate/Grand Order"
    assert character.roulette == "game & animanga"
    assert character.kakera_value == 624
    assert character.claim_rank == 150
    assert character.like_rank == 215


def test_parse_roll_from_copied_mudae_output() -> None:
    roll = MudaeTextParser().parse_roll(ROLL)

    assert roll.name == "Airi Jinguuji"
    assert roll.series == "Mob kara Hajimaru Tansaku Eiyuutan"
    assert roll.claim_rank == 55003
    assert roll.kakera_value == 27


def test_parse_roll_without_claim_rank_when_rank_display_is_disabled() -> None:
    roll = MudaeTextParser().parse_roll(
        "Hips\n"
        "Dekoboko Majo no Oyako Jijou\n"
        "30:kakera:"
    )

    assert roll.name == "Hips"
    assert roll.series == "Dekoboko Majo no Oyako Jijou"
    assert roll.claim_rank is None
    assert roll.kakera_value == 30


def test_parse_roll_accepts_a_positive_kakera_prefix() -> None:
    roll = MudaeTextParser().parse_roll("Mai Sakurajima\nSeishun Buta Yarou\n+1,386:kakera:")

    assert roll.kakera_value == 1386


def test_parse_kakera_reaction_receipt() -> None:
    receipt = MudaeTextParser().parse_kakera_reaction_receipt(
        ":kakeraY: cute_beagle_91130 +497 ($k)"
    )

    assert receipt.reaction_label == ":kakeraY:"
    assert receipt.account_name == "cute_beagle_91130"
    assert receipt.kakera_earned == 497


def test_parse_keyed_harem_page_from_mmy_output() -> None:
    page = MudaeTextParser().parse_harem_key_page(HAREM_KEY_PAGE)

    assert page.page_number == 1
    assert page.page_count == 6
    assert len(page.entries) == 15
    assert page.entries[0].name == "Albedo"
    assert page.entries[0].key_type == "gold"
    assert page.entries[0].key_count == 7
    assert page.entries[-1].name == "Nezuko Kamado"


def test_parse_value_sorted_keyed_harem_page_from_mmyk_output() -> None:
    page = MudaeTextParser().parse_harem_key_page(
        "ernieuuu's harem\n"
        "Total value: 63,610:kakera:\n"
        "Megumin · :silverkey:  (5) 1,505 ka\n"
        "Saber · :silverkey:  (5) 1,476 ka\n"
        "Albedo · :goldkey:  (7) 1,453 ka\n"
        "Page 1 / 6"
    )

    assert page.total_harem_value == 63610
    assert page.entries[0].name == "Megumin"
    assert page.entries[0].kakera_value == 1505
    assert page.entries[2].key_type == "gold"


def test_parse_player_bonus_preserves_metrics_and_extracts_key_modifiers() -> None:
    bonus = MudaeTextParser().parse_player_bonus(
        "Player Bonuses\n"
        ":addroll: · Rolls per hour: +9 (6 $k + 1 $kl + 2 $kt) -3 ($bw)\n"
        ":wlslot: · Wishlist slots: +8 (6 $k + 0 $kl + 2 $kt) -2 ($sw)\n"
        ":wlslot: · Spawn bonus for wishes: +210% ($k + $bw + slash)\n"
        ":sw: · Additional % spawn bonus for $starwish: +180% ($kt + $bw + $tuto) (= 390%)\n"
        ":sw: · Starwish slots: +1 (0 $kl + 1 $sw)\n"
        ":morekakera: · Kakera max power: 110% ($kt)\n"
        ":morekakera: · Power cost per kakera button: 36% (-60% $k -4% $kt)\n"
        ":morekakera: · Additional bonus for kakera buttons on starwishes: +20% ($sw)\n"
        ":kakeraL: · Random kakera per light kakera: 4-5 (1 $kt)\n"
        ":chaoskey: · Chance to get an additional key on wishes: +10% ($kt)\n"
    )

    assert len(bonus.metrics) == 10
    assert bonus.rolls_per_hour_bonus == 9
    assert bonus.wishlist_slot_bonus == 8
    assert bonus.wish_spawn_bonus_percent == 210
    assert bonus.starwish_spawn_bonus_percent == 180
    assert bonus.starwish_total_spawn_bonus_percent == 390
    assert bonus.additional_wish_key_chance_percent == 10
    assert bonus.light_kakera_minimum == 4
    assert bonus.light_kakera_maximum == 5


def test_parse_wishlist_reads_starwish_markers_from_wl_output() -> None:
    wishlist = MudaeTextParser().parse_wishlist(
        "**ernieuuu's Wishlist - 13/13 $wl, 2/2 $sw**\n"
        "**Saber** ✅:kakera:\n"
        "**Emilia** ✅ ⭐\n"
        "**Power** ✅ ⭐\n"
        "**Xenovia Quarta** ✅:kakera:\n"
    )

    assert wishlist.wishlist_count == 13
    assert wishlist.wishlist_capacity == 13
    assert wishlist.starwish_count == 2
    assert wishlist.starwish_capacity == 2
    assert [entry.name for entry in wishlist.entries] == [
        "Saber",
        "Emilia",
        "Power",
        "Xenovia Quarta",
    ]
    assert [entry.name for entry in wishlist.entries if entry.is_starwish] == ["Emilia", "Power"]


def test_parse_disablelist_reads_pool_limits_toggles_and_bundles() -> None:
    disablelist = MudaeTextParser().parse_disablelist(
        "$dl\n"
        "Mudae\n"
        "APP\n"
        "ernieuuu's Disablelist (13/16)\n"
        "107,529 disabled (41,247 $wa, 42,438 $ha, 20,996 $wg, 14,789 $hg)\n"
        "⚠️ Pool limit reached: 40,861 $wa (series above this limit are not disabled)\n"
        "⚠️ Pool limit reached: 42,213 $ha (series above this limit are not disabled)\n"
        "Western animanga series are completely disabled ($togglewestern)\n"
        "IRL series are completely disabled ($toggleirl)\n"
        "Kadokawa Corporation (13,207)\n"
        "Mobile Games (16,769)\n"
    )

    assert disablelist.slots_used == 13
    assert disablelist.slots_capacity == 16
    assert disablelist.total_disabled == 107529
    assert disablelist.disabled_wa == 41247
    assert disablelist.wa_pool_limit == 40861
    assert disablelist.ha_pool_limit == 42213
    assert disablelist.western_disabled
    assert disablelist.irl_disabled
    assert [(entry.name, entry.disabled_count) for entry in disablelist.entries] == [
        ("Kadokawa Corporation", 13207),
        ("Mobile Games", 16769),
    ]


def test_parse_topx_reads_direct_unavailable_character_evidence() -> None:
    page = MudaeTextParser().parse_unavailable_characters(
        "🏆 TOP 1000\n"
        "#10 - 2B - NieR: Automata  🚫\n"
        "#88 - Venom - Marvel 🚫  ($togglewestern)\n"
        "#119 - Ado - Utaite 🚫  ($toggleirl)\n"
        "Page 1 / 67"
    )

    assert page.limit == 1000
    assert page.page_number == 1
    assert page.page_count == 67
    assert [(character.name, character.reason) for character in page.characters] == [
        ("2B", None),
        ("Venom", "$togglewestern"),
        ("Ado", "$toggleirl"),
    ]


def test_parse_kakera_state_reads_balance_and_maxed_badges() -> None:
    state = MudaeTextParser().parse_kakera_state(
        "You have 7,673:kakera:!\n"
        ":BronzeIV: Bronze IV · Max reached!\n"
        "Silver IV · Max reached!\n"
        "Gold IV · Max reached!\n"
        "Sapphire IV · Max reached!\n"
        "Ruby IV · Max reached!\n"
        "Emerald IV · Max reached!\n"
        "Diamond IV · Max reached!"
    )

    assert state.kakera_balance == 7673
    assert [(badge.badge_name, badge.level, badge.max_reached) for badge in state.badges] == [
        ("bronze", 4, True),
        ("silver", 4, True),
        ("gold", 4, True),
        ("sapphire", 4, True),
        ("ruby", 4, True),
        ("emerald", 4, True),
        ("diamond", 4, True),
    ]


def test_parse_tower_state_reads_current_level_cost_balance_and_built_perks() -> None:
    state = MudaeTextParser().parse_tower_state(
        "Your current level is:tow2: (+ 1 tower)\n"
        "The next level costs 75,000:kakera:\n"
        "You have 7,673:kakera:\n"
        "☑️ [5] Unveil 1 random button for the $oh command\n"
        "[6] +30 spheres with $dk\n"
        "☑️ [11] +1 roll per hour"
    )

    assert state.current_level == 2
    assert state.completed_towers == 1
    assert state.next_level_cost == 75000
    assert state.kakera_balance == 7673
    assert state.built_perk_ids == (5, 11)


def test_parse_kakeraloot_state_reads_progress_and_balance() -> None:
    state = MudaeTextParser().parse_kakeraloot_state(
        "ernieuuu - Kakeraloots\n"
        "Rolls stacked: 1 ($us)\n"
        "$disable limits: -102 $wa/$ha, -68 $wg/$hg\n"
        "Protected wish: LVL 42 (spawn probability: 1/4,642)\n"
        "Mudapins: 22 ($mp)\n"
        "$rt: -2h cooldown\n"
        "+1 permanent roll\n"
        "1 star branch (+0 $sw)\n\n"
        "Quantity LVL 23\n"
        "Quality LVL 6\n"
        "$kl usage: 256 (:kakeraC:+1)\n"
        "9,210:kakera:"
    )

    assert state.quantity_level == 23
    assert state.quality_level == 6
    assert state.usage_count == 256
    assert state.kakera_balance == 9210
    assert state.protected_wish_denominator == 4642


def test_parse_kakeraloot_state_accepts_the_no_loots_message() -> None:
    state = MudaeTextParser().parse_kakeraloot_state(
        "No kakeraloots bought! ($kl)\n"
        "Type $infokl to get more infos about kakeraloots."
    )

    assert not state.has_kakeraloots
    assert state.status_note == "No Kakeraloots bought; Mudae did not report loot statistics."
    assert state.quantity_level is None


def test_parse_server_settings_reads_core_rules_and_visible_options() -> None:
    settings = MudaeTextParser().parse_server_settings(
        "🛠️ Server Settings 🛠️\n"
        "(Server not premium)\n"
        "· Prefix: $ ($prefix)\n"
        "· Lang: en ($lang)\n"
        "· Claim reset: every 180 min. ($setclaim)\n"
        "· Exact minute of the reset: xx:14 ($setinterval)\n"
        "· Reset shifted: by +0 min. ($shifthour)\n"
        "· Rolls per hour: 10 ($setrolls)\n"
        "· Time before the claim reaction expires: 45 sec. ($settimer)\n"
        "· Spawn rarity multiplier for already claimed characters: 4 ($setrare)\n"
        "· % kakera bonus: +0 ($setkakerabonus)\n"
        "· % sphere bonus: +0 ($setspherebonus)\n"
        "· Game mode: 1 ($gamemode)\n"
        "· This channel instance: 1 ($channelinstance)\n"
        "· Slash commands: enabled ($toggleslash)"
    )

    assert not settings.server_premium
    assert settings.prefix == "$"
    assert settings.claim_reset_minutes == 180
    assert settings.rolls_per_hour == 10
    assert settings.game_mode == 1
    assert len(settings.metrics) == 13


def test_parse_personal_rare_reads_the_account_override() -> None:
    state = MudaeTextParser().parse_personal_rare(
        "Current $setrare value for your server (admin command): 4\n"
        "Your current $personalrare: 1"
    )

    assert state.personal_rare_multiplier == 1


def test_parse_kakeraloot_settings_reads_server_costs() -> None:
    settings = MudaeTextParser().parse_kakeraloot_settings(
        "Kakeraloots\n"
        "Each $kl costs 500:kakera: (admins can change this value with $klvalue)\n"
        "Reaching the level 1 of quantity or quality costs 2,000:kakera: "
        "(increased by 200/level, values can't be changed)"
    )

    assert settings.loot_cost == 500
    assert settings.quantity_quality_base_cost == 2000
    assert settings.quantity_quality_level_increment == 200


def test_parse_timer_state_keeps_detailed_action_categories() -> None:
    state = MudaeTextParser().parse_timer_state(
        "ernieuuu, you can claim right now! The next claim reset is in 2h 32 min.\n"
        "You have 0 rolls left. Next rolls reset in 32 min.\n"
        "You have 0 rolls reset in stock.\n"
        "You may vote again in 5h 50 min.\n"
        "Next $daily reset in 8h 16 min.\n"
        "$dk is ready!\n"
        "$rt is available!\n"
        "You can react to kakera right now!\n"
        "Power: 72%\n"
        "Each kakera button consumes 36% of your reaction power.\n"
        "Your characters with 10+ keys consume half the power (18%)\n"
        "Stock: 12,114:kakera:\n"
        "(Keys LVL 6+) 5,000:kakera:to collect before the next reset (2h 32 min.)\n"
        "Probability to complete + reset $bku on your next $sw: 10%\n"
        "0 $oh left for today, 0 $oc, 0 $oq (+1 stored) and 0 $ot.\n"
        "15h 18 min before the refill."
    )

    assert state.can_claim_now
    assert state.claim_reset_minutes == 152
    assert state.rolls_left == 0
    assert state.daily_kakera_ready
    assert state.kakera_stock == 12114
    assert state.oq_stored == 1


def test_parse_timer_state_accepts_a_shorter_customized_layout() -> None:
    state = MudaeTextParser().parse_timer_state(
        "ernieuuu, you can't claim for another 14 min.\n"
        "You have 0 rolls left. Next rolls reset in 14 min.\n"
        "Next $daily reset in 8h 13 min.\n"
        "You can react to kakera right now!\n"
        "Power: 100%\n"
        "Each kakera button consumes 60% of your reaction power.\n"
        "Your characters with 10+ keys consume half the power (30%)\n"
        "Stock: 37:kakera:\n"
        "Next $dk in 19h 26 min.\n"
        "You may vote again in 5h 47 min.\n"
        "0 $oh left for today, 0 $oc, 0 $oq and 0 $ot.\n"
        "15h 15 min before the refill."
    )

    assert state.can_claim_now is False
    assert state.claim_reset_minutes == 14
    assert state.rolls_reset_stock is None
    assert state.daily_kakera_ready is False
    assert state.rt_available is None
    assert state.oq_stored == 0


def test_parse_top_page_rejects_unrecognized_text() -> None:
    with pytest.raises(MudaeParseError, match="No ranked characters"):
        MudaeTextParser().parse_top_page("not a Mudae message")
