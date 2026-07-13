from moa.parser.message_router import MudaeMessageRouter


def test_router_detects_supported_timer_and_server_setting_messages() -> None:
    router = MudaeMessageRouter()

    timers = router.detect("ernieuuu, you can claim right now! The next claim reset is in 2h 32 min.")
    settings = router.detect("Server Settings\nClaim reset: every 180 min. ($setclaim)")

    assert timers.kind == "timers"
    assert settings.kind == "settings"


def test_router_detects_rankless_rolls_after_more_specific_formats() -> None:
    detection = MudaeMessageRouter().detect(
        "Hips\nDekoboko Majo no Oyako Jijou\n30:kakera:"
    )

    assert detection.kind == "roll"


def test_router_detects_a_kakera_reaction_receipt() -> None:
    detection = MudaeMessageRouter().detect(":kakeraY: cute_beagle_91130 +497 ($k)")

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


def test_router_keeps_ambiguous_messages_unknown() -> None:
    detection = MudaeMessageRouter().detect("Mudae is online. Have fun!")

    assert detection.kind == "unknown"
