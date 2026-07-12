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


def test_router_keeps_ambiguous_messages_unknown() -> None:
    detection = MudaeMessageRouter().detect("Mudae is online. Have fun!")

    assert detection.kind == "unknown"
