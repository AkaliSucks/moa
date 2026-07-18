from moa.parser.message_router import MudaeMessageRouter


def test_router_detects_supported_timer_and_server_setting_messages() -> None:
    router = MudaeMessageRouter()

    timers = router.detect("ernieuuu, you can claim right now! The next claim reset is in 2h 32 min.")
    settings = router.detect("Server Settings\nClaim reset: every 180 min. ($setclaim)")

    assert timers.kind == "timers"
    assert settings.kind == "settings"


def test_router_detects_roll_limit_and_vote_reset_messages_as_timers() -> None:
    router = MudaeMessageRouter()

    assert router.detect("The roulette is limited to 17 uses per hour. 50 min left.").kind == "timers"
    assert router.detect("Use this command again to reset your rolls timer for ONE server.").kind == "timers"
    assert router.detect(
        "cute_beagle_91130, You can't react to kakera for 34 min. ($ku)"
    ).kind == "reaction_blocked"
    assert router.detect(
        "You can't react to kakera for 11 min.\nPower: 32%\nStock: 33,441:kakera:"
    ).kind == "timers"
    assert router.detect(
        "@ernieuuu, For this server, you can claim once per interval of 3h. "
        "The next interval begins in 53 min."
    ).kind == "timers"


def test_router_detects_divorce_confirmation_prompt() -> None:
    response = (
        "Professor Layton: Do you confirm the divorce? (y/n/yes/no)\n"
        "Characters divorced by $divorce are also removed from the $restorelist "
        "(+54:kakera:if you confirm)"
    )

    assert MudaeMessageRouter().detect(response).kind == "divorce_prompt"
    assert MudaeMessageRouter().detect("Divorce declined.").kind == "divorce_declined"
    assert (
        MudaeMessageRouter()
        .detect("💔 Professor Layton and cute_beagle_91130 are now divorced. 💔 (+54:kakera:)")
        .kind
        == "divorce_complete"
    )


def test_router_detects_roll_reset_details_as_timers_before_rankless_roll_parsing() -> None:
    router = MudaeMessageRouter()

    response = (
        "You have 0 rolls left. Next rolls reset in 36 min.\n"
        "You can react to kakera right now!\n"
        "Power: 100%\n"
        "Each kakera button consumes 100% of your reaction power.\n"
        "Your characters with 10+ keys consume half the power (50%)\n"
        "Stock: 0:kakera:"
    )

    assert router.detect(response).kind == "timers"


def test_router_detects_kakeraloot_purchase_guard_as_loot_state() -> None:
    response = (
        "You need to buy kakeraloots before using this command ($kl)\n"
        "Type $infokl to get more infos about kakeraloots."
    )

    assert MudaeMessageRouter().detect(response).kind == "lootstate"


def test_router_detects_kakeraloot_prerequisite_guard_as_loot_state() -> None:
    response = "Prerequisites: Sapphire I + Ruby I + Emerald I ($infokl)"

    assert MudaeMessageRouter().detect(response).kind == "lootstate"


def test_router_detects_sphere_result_messages() -> None:
    detection = MudaeMessageRouter().detect(
        "You can click 7 times on the buttons below (2 minutes).\n"
        ":sp: +158\n"
        ":spG: +43 (Stock: 3,655)"
    )

    assert detection.kind == "sphere_result"


def test_router_detects_rankless_rolls_after_more_specific_formats() -> None:
    detection = MudaeMessageRouter().detect(
        "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"
    )

    assert detection.kind == "roll"


def test_router_detects_tutorial_progress_before_rankless_roll_fallback() -> None:
    router = MudaeMessageRouter()

    assert router.detect("Step 1 completed!\nReward: +200:kakera:\n2/17 - Tutorial").kind == "tutorial"
    assert router.detect("Substep completed! (See $tuto)").kind == "tutorial"


def test_router_detects_mudae_help() -> None:
    assert MudaeMessageRouter().detect("Looking for a specific command? Try $search").kind == "help"


def test_router_detects_transaction_responses() -> None:
    router = MudaeMessageRouter()

    assert router.detect("ernieuuu, do you really want to give 1:kakera: ? (y/n/yes/no)").kind == "gift_kakera"
    assert router.detect("ernieuuu, do you really want to give 1 :sp: ? (y/n/yes/no)").kind == "gift_spheres"
    assert router.detect("@friend, ernieuuu wants to give you Megumi Sakura. Do you confirm? (y/n/yes/no)").kind == "gift_character"
    assert router.detect("The exchange is over: Tsubame Koyasu vs Megumi Sakura").kind == "trade"


def test_router_detects_mudapin_information_as_help() -> None:
    response = (
        "Mudapins are collectable badges you can display on your profile.\n"
        "Type $mp to see your mudapin inventory."
    )

    assert MudaeMessageRouter().detect(response).kind == "help"


def test_router_detects_mudapin_inventory() -> None:
    assert MudaeMessageRouter().detect(":pin139::pin182::logopin6:").kind == "mudapins"
    assert MudaeMessageRouter().detect(
        "No mudapins found! Collect them with kakeraloots ($kl)"
    ).kind == "mudapins"


def test_router_detects_profile_summary() -> None:
    response = (
        "cute_beagle_91130\n"
        "Collection size: 35 (100%:female: 0% :male:)\n"
        "Pokédex: 2 Pokémon :gulpin: :piloswine:\n"
        "Reacts:\n"
        "1x:kakeraP: 7x:kakera: 1x:kakeraT:\n"
        "812:kakera:\n"
        "Keys: 3:bronzekey:\n"
        "110 :sp:\n"
        "2x:spP: 12x:spB: 7x:spT: 4x:spG: 1x:spY: 1x:sp: 4x:spL:\n"
        ":silvmudae::MudaeBirthday7::MudaeBirthday8::DiamondI:"
    )

    assert MudaeMessageRouter().detect(response).kind == "profile"


def test_router_detects_a_kakera_reaction_receipt() -> None:
    detection = MudaeMessageRouter().detect(":kakeraY: cute_beagle_91130 +497 ($k)")

    assert detection.kind == "reaction_receipt"


def test_router_detects_a_kakera_breakdown_reaction_receipt() -> None:
    detection = MudaeMessageRouter().detect(
        ":kakeraL: breaks down into:kakeraB: +:kakeraR: => ernieuuu +2,202 ($k)"
    )

    assert detection.kind == "reaction_receipt"


def test_router_detects_keyed_harem_pages() -> None:
    detection = MudaeMessageRouter().detect(
        "ernieuuu's harem\nAlbedo \u00b7 :goldkey:  (7) 1,453 ka\nPage 1 / 6"
    )

    assert detection.kind == "harem"


def test_router_detects_ranked_harem_pages() -> None:
    detection = MudaeMessageRouter().detect(
        "ernieuuu's harem\n"
        "#2 - Zero Two 1,440 ka\n"
        "#3 - Rem 1,426 ka\n"
        "Page 1 / 38"
    )

    assert detection.kind == "ranked_harem"


def test_router_detects_markdown_ranked_harem_pages() -> None:
    detection = MudaeMessageRouter().detect(
        "**ernieuuu's harem**\n"
        "AVG: 4,867\n"
        "**Top 15 value: 2,630**\n"
        "**#2** - **Zero Two** **1,052** ka\n"
        "**#4 - Saber 996 ka**\n"
        "Page 1 / 3"
    )

    assert detection.kind == "ranked_harem"


def test_router_keeps_ambiguous_messages_unknown() -> None:
    detection = MudaeMessageRouter().detect("Mudae is online. Have fun!")

    assert detection.kind == "unknown"
