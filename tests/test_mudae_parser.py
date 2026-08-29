import re
from unittest.mock import Mock, call

import pytest

from moa.models.character import DisableListEntry
from moa.parser.mudae import MudaeParseError, MudaeTextParser
from moa.parser.antidisable import AntidisablePageParser
from moa.parser.character_details import CharacterDetailsParser
from moa.parser.claim import ClaimParser
from moa.parser.divorce_confirmation import DivorceConfirmationParser
from moa.parser.divorce_declined import DivorceDeclinedValidator
from moa.parser.divorce_prompt import DivorcePromptParser
from moa.parser.disablelist import DisableListParser
from moa.parser.harem_key import HaremKeyParser
from moa.parser.harem_ranked import RankedHaremParser
from moa.parser.kakera_reaction_blocked import KakeraReactionBlockedParser
from moa.parser.kakera_reaction_receipt import KakeraReactionReceiptParser
from moa.parser.message_router import MudaeMessageRouter
from moa.parser.player_bonus import PlayerBonusParser
from moa.parser.primitives import comma_int, first_named_rank, normalize_custom_emojis
from moa.parser.roll import RollParser
from moa.parser.top import TopParser
from moa.parser.transaction import TransactionParser
from moa.parser.unavailable_characters import UnavailableCharacterPageParser
from moa.parser.wishlist import WishlistParser


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


def test_parse_topo_page_preserves_claimed_owner_names() -> None:
    page = MudaeTextParser().parse_top_page(
        "🏆 TOP 1000\n"
        "#1 - Hatsune Miku \U0001f49e => xuppii - VOCALOID\n"
        "#2 - Zero Two \U0001f49e => ernieuuu - DARLING in the FRANXX\n"
        "Page 1 / 67"
    )

    assert page.characters[0].name == "Hatsune Miku"
    assert page.characters[0].owner_name == "xuppii"
    assert page.characters[1].owner_name == "ernieuuu"


def test_parse_top_page_facade_matches_dedicated_parser() -> None:
    expected = TopParser(MudaeParseError).parse(TOP_PAGE)

    assert MudaeTextParser().parse_top_page(TOP_PAGE) == expected


def test_top_parser_normalizes_emoji_zero_width_blanks_and_comma_numbers() -> None:
    page = TopParser(MudaeParseError).parse(
        "\u200b<:trophy:123> TOP 1,000\n\n"
        "#1,002 - Character\u200bName <:heart:456> - Series Name\n"
        "#1,003 - Claimed Character <:heart:456> => owner - Claimed Series\n"
        "\u200bPage 2 / 10\u200b"
    )

    assert page.limit == 1000
    assert page.page_number == 2
    assert page.page_count == 10
    assert page.characters[0].name == "Character Name"
    assert page.characters[0].claim_rank == 1002
    assert page.characters[0].owner_name is None
    assert page.characters[1].owner_name == "owner"


def test_shared_parser_primitives_preserve_custom_emoji_boundaries() -> None:
    assert normalize_custom_emojis("<:heart:123> <a:star:456>") == ":heart: :star:"
    assert normalize_custom_emojis("<:heart:123 <a:star:456x>") == "<:heart:123 <a:star:456x>"


def test_shared_parser_primitives_convert_comma_ints_and_preserve_value_errors() -> None:
    assert comma_int("1,234,567") == 1234567

    with pytest.raises(ValueError):
        comma_int("not-a-number")


def test_shared_parser_primitives_find_optional_anchored_named_rank() -> None:
    pattern = re.compile(r"^Rank: #(?P<rank>[\d,]+)$")

    assert first_named_rank(["prefix Rank: #9", "Rank: #1,234"], pattern) == 1234
    assert first_named_rank(["No rank here"], pattern) is None


def test_parser_line_helpers_preserve_zero_width_policies() -> None:
    text = "\u200b\nCharacter\u200bName"

    assert MudaeTextParser._lines(text) == ["", "CharacterName"]
    assert RollParser._lines(text) == ["", "CharacterName"]
    assert TopParser._lines(text) == ["Character Name"]


def test_parse_antidisable_page_reads_series_slots_count_and_pages() -> None:
    page = MudaeTextParser().parse_antidisable_page(
        "ernieuuu's Antidisablelist (83/500)\n"
        "2,614 antidisabled characters\n"
        "【OSHI NO KO】\n"
        "Chainsaw Man\n"
        "Page 1 / 6"
    )

    assert page.slots_used == 83
    assert page.slots_capacity == 500
    assert page.antidisabled_character_count == 2614
    assert page.page_number == 1
    assert page.page_count == 6
    assert page.series_names == ("OSHI NO KO", "Chainsaw Man")


def test_parse_antidisable_continuation_page_without_character_count() -> None:
    page = MudaeTextParser().parse_antidisable_page(
        "ernieuuu's Antidisablelist (83/500)\n"
        "Chainsaw Man\n"
        "Page 2 / 6"
    )

    assert page.antidisabled_character_count is None
    assert page.page_number == 2
    assert page.page_count == 6
    assert page.series_names == ("Chainsaw Man",)


def test_antidisable_parser_preserves_zero_evidence_order_duplicates_and_converter_seams() -> None:
    lines = Mock(
        return_value=[
            "prefix Antidisablelist (0/0) suffix",
            "0 antidisabled characters",
            "**【First】**",
            "**First**",
            "Page 0 / 0",
        ]
    )
    number = Mock(return_value=0)

    page = AntidisablePageParser(MudaeParseError, lines, number).parse("raw antidisable")

    lines.assert_called_once_with("raw antidisable")
    number.assert_called_once_with("0")
    assert page.model_dump() == {
        "page_number": 0,
        "page_count": 0,
        "slots_used": 0,
        "slots_capacity": 0,
        "antidisabled_character_count": 0,
        "series_names": ("First", "First"),
    }


def test_antidisable_parser_preserves_line_normalization_and_optional_absence() -> None:
    text = (
        "\u200b**ernieuuu's Antidisablelist (1/5)**\n"
        "\n"
        "**<:series:123>**\n"
        "**【Series Name】**\n"
        "Page 2 / 3\n"
    )

    page = AntidisablePageParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(text)

    assert page.page_number == 2
    assert page.page_count == 3
    assert page.antidisabled_character_count is None
    assert page.series_names == (":series:", "Series Name")


def test_antidisable_parser_facade_matches_dedicated_parser() -> None:
    text = (
        "ernieuuu's Antidisablelist (83/500)\n"
        "2,614 antidisabled characters\n"
        "【OSHI NO KO】\n"
        "Chainsaw Man\n"
        "Page 1 / 6"
    )

    expected = AntidisablePageParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(text)

    assert MudaeTextParser().parse_antidisable_page(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "2 antidisabled characters\nSeries",
            "Expected a Mudae `$adl` header with antidisable slot counts.",
        ),
        (
            "Antidisablelist (1/5)\n0 antidisabled characters\nPage 1 / 1",
            "No antidisable series found in the Mudae `$adl` page.",
        ),
    ],
)
def test_antidisable_parser_rejects_invalid_pages_with_exact_errors(
    text: str, message: str
) -> None:
    with pytest.raises(MudaeParseError) as error:
        AntidisablePageParser(
            MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
        ).parse(text)

    assert str(error.value) == message


def test_parse_character_details_from_copied_im_output() -> None:
    character = MudaeTextParser().parse_character_details(CHARACTER_DETAILS)

    assert character.name == "Mai Sakurajima"
    assert character.series == "Seishun Buta Yarou"
    assert character.gender == "female"
    assert character.roulette == "animanga"
    assert character.kakera_value == 929
    assert character.claim_rank == 9
    assert character.like_rank == 19


def test_parse_character_details_facade_matches_dedicated_parser() -> None:
    expected = CharacterDetailsParser(MudaeParseError).parse(CHARACTER_DETAILS)

    assert MudaeTextParser().parse_character_details(CHARACTER_DETAILS) == expected


def test_parse_claim_confirmation_from_copied_mudae_output() -> None:
    claim = MudaeTextParser().parse_claim_confirmation(
        "💖 **ernieuuu** and **Pakunoda** are now married! 💖\n"
        "+128:kakera:(Emerald IV bonus) +30 :sp:"
    )

    assert claim.account_name == "ernieuuu"
    assert claim.character_name == "Pakunoda"


def test_parse_claim_confirmation_facade_matches_dedicated_parser() -> None:
    expected = ClaimParser(MudaeParseError).parse(
        "💖 **ernieuuu** and **Pakunoda** are now married! 💖"
    )

    assert MudaeTextParser().parse_claim_confirmation(
        "💖 **ernieuuu** and **Pakunoda** are now married! 💖"
    ) == expected


def test_claim_parser_normalizes_lines_and_cleans_claim_fields() -> None:
    claim = ClaimParser(MudaeParseError).parse(
        "\n\u200b\n💖 **<@123>** and **Pa\u200bkuno*da** are NOW married! trailing text\n\n"
    )

    assert claim.account_name == "123>"
    assert claim.character_name == "Pakunoda"


def test_claim_confirmation_remains_claim_router_kind() -> None:
    result = MudaeMessageRouter().detect(
        "💖 **ernieuuu** and **Pakunoda** are now married! 💖"
    )

    assert result.kind == "claim"


@pytest.mark.parametrize(
    "text",
    [
        "ernieuuu and Pakunoda are now married",
        "*** and Pakunoda are now married!",
        "ernieuuu and *** are now married!",
        "not a claim confirmation",
    ],
)
def test_parse_claim_confirmation_rejects_invalid_confirmation(text: str) -> None:
    with pytest.raises(MudaeParseError) as error:
        MudaeTextParser().parse_claim_confirmation(text)

    assert str(error.value) == "Expected a Mudae claim confirmation."


def test_parse_divorce_prompt_from_copied_mudae_output() -> None:
    response = (
        "\u200b**Professor Layton**:\tDO YOU CONFIRM THE DIVORCE?\t(y/n/yes/no)\n"
        "CHARACTERS divorced by $DIVORCE are also removed from the $RESTORELIST "
        "(+**1,234**<:kakera:123>IF YOU CONFIRM)"
    )
    prompt = DivorcePromptParser(MudaeParseError).parse(response)

    assert prompt.character_name == "Professor Layton"
    assert prompt.kakera_refund == 1234
    assert MudaeTextParser().parse_divorce_prompt(response) == prompt
    MudaeTextParser().parse_divorce_declined("Divorce declined.")


def test_divorce_declined_validator_accepts_normalized_line_and_returns_none() -> None:
    response = "\n\u200b unrelated line\n \u200bdIVORCE DECLINED. \n"

    assert DivorceDeclinedValidator(MudaeParseError).parse(response) is None
    assert MudaeTextParser().parse_divorce_declined(response) is None


@pytest.mark.parametrize(
    "response",
    [
        "**Divorce declined.**",
        "Divorce declined",
        "Divorce declined!",
        "Divorce declined. extra text",
        "before Divorce declined. after",
    ],
)
def test_divorce_declined_validator_rejects_non_exact_decline_lines(response: str) -> None:
    with pytest.raises(MudaeParseError) as error:
        DivorceDeclinedValidator(MudaeParseError).parse(response)

    assert str(error.value) == "Expected Mudae's divorce-declined response."


@pytest.mark.parametrize(
    ("response", "refund"),
    [
        ("Professor Layton: Do you confirm the divorce? (y/n/yes/no)", None),
        (
            "Professor Layton: Do you confirm the divorce? (y/n/yes/no)\n"
            "Characters divorced by $divorce are also removed from the $restorelist (+0 if you confirm)",
            0,
        ),
    ],
)
def test_divorce_prompt_parser_preserves_missing_and_zero_refunds(
    response: str, refund: int | None
) -> None:
    prompt = DivorcePromptParser(MudaeParseError).parse(response)

    assert prompt.kakera_refund == refund


def test_divorce_prompt_parser_preserves_exact_error() -> None:
    with pytest.raises(MudaeParseError) as error:
        DivorcePromptParser(MudaeParseError).parse("not a divorce prompt")

    assert str(error.value) == "Expected a Mudae divorce confirmation prompt."


@pytest.mark.parametrize(
    ("response", "refund"),
    [
        (
            "\u200b unrelated line\n"
            "**\U0001f494** **Professor Layton** and **CUTE_BEAGLE_91130** "
            "are NOW DIVORCED. **\U0001f494** (+54:kakera:)",
            54,
        ),
        ("Professor Layton and cute_beagle_91130 are now divorced.", None),
        ("Professor Layton and cute_beagle_91130 are now divorced. (+0)", 0),
        (
            "Professor Layton and cute_beagle_91130 are now divorced. (+1,234 kakera)",
            1234,
        ),
    ],
)
def test_divorce_confirmation_parser_preserves_normalization_and_refund_states(
    response: str, refund: int | None
) -> None:
    divorce = DivorceConfirmationParser(MudaeParseError).parse(response)

    assert divorce.character_name == "Professor Layton"
    assert divorce.account_name.casefold() == "cute_beagle_91130"
    assert divorce.kakera_refund == refund


def test_divorce_confirmation_parser_facade_matches_dedicated_parser() -> None:
    response = "Professor Layton and Cute_Beagle_91130 are now divorced. (+1,234:kakera:)"

    expected = DivorceConfirmationParser(MudaeParseError).parse(
        response, expected_account="cute_beagle_91130"
    )

    assert MudaeTextParser().parse_divorce_confirmation(
        response, expected_account="cute_beagle_91130"
    ) == expected


def test_divorce_confirmation_parser_skips_mismatching_account_candidates() -> None:
    response = (
        "Professor Layton and someone_else are now divorced.\n"
        "Professor Layton and Cute_Beagle_91130 are now divorced. (+54:kakera:)"
    )

    divorce = DivorceConfirmationParser(MudaeParseError).parse(
        response, expected_account="cute_beagle_91130"
    )

    assert divorce.account_name == "Cute_Beagle_91130"
    assert divorce.kakera_refund == 54


@pytest.mark.parametrize(
    "response",
    [
        "prefix Professor Layton and account are now divorced. trailing text",
        "Professor Layton and account are now divorced. trailing text",
        "\U0001f494 and \U0001f494 are now divorced. \U0001f494 (+0)",
    ],
)
def test_divorce_confirmation_parser_requires_anchored_nonempty_identities(
    response: str,
) -> None:
    with pytest.raises(MudaeParseError) as error:
        DivorceConfirmationParser(MudaeParseError).parse(response)

    assert str(error.value) == "Expected a Mudae completed-divorce response."


def test_parse_completed_divorce_from_copied_mudae_output() -> None:
    divorce = MudaeTextParser().parse_divorce_confirmation(
        "💔 Professor Layton and cute_beagle_91130 are now divorced. 💔 (+54:kakera:)"
    )

    assert divorce.character_name == "Professor Layton"
    assert divorce.account_name == "cute_beagle_91130"
    assert divorce.kakera_refund == 54


@pytest.mark.parametrize(
    ("kind", "response"),
    [
        ("gift_kakera", "ernieuuu, do you really want to give 1:kakera: ? (y/n/yes/no)"),
        ("gift_kakera", "@ernieuuu just gifted 1:kakera: to @friend"),
        ("gift_spheres", "ernieuuu, do you really want to give 1 :sp: ? (y/n/yes/no)"),
        ("gift_spheres", "@ernieuuu just gifted 1 :sp: to @friend"),
        ("gift_character", "@friend, ernieuuu wants to give you Megumi Sakura. Do you confirm?"),
        ("gift_character", "Megumi Sakura given to @friend"),
        ("trade", "Type the name(s) of the character(s) to trade:"),
        ("trade", "The exchange is over: Tsubame Koyasu vs Megumi Sakura"),
    ],
)
def test_parse_transaction_steps(kind: str, response: str) -> None:
    MudaeTextParser().parse_transaction(response, kind)


@pytest.mark.parametrize(
    ("kind", "response"),
    [
        ("gift_kakera", "Syntax: $givek @user amount"),
        ("gift_spheres", "Syntax: $givesp @user amount"),
        ("gift_character", "Syntax: $give @user character"),
        ("trade", "Syntax: $trade @user"),
    ],
)
def test_transaction_parser_accepts_syntax_responses(kind: str, response: str) -> None:
    assert TransactionParser(MudaeParseError).parse(response, kind) is None


def test_parse_transaction_facade_matches_dedicated_parser() -> None:
    response = "***ERNIEUUU***, do you really want to give 1:KAKERA: ? (Y/N/YES/NO)"

    expected = TransactionParser(MudaeParseError).parse(response, "gift_kakera")

    assert MudaeTextParser().parse_transaction(response, "gift_kakera") == expected


def test_router_probes_transaction_kinds_in_declared_order() -> None:
    parser = Mock()
    parser.parse_kakera_reaction_receipt.side_effect = MudaeParseError("not receipt")
    parser.parse_kakera_reaction_blocked.side_effect = MudaeParseError("not blocked")
    parser.parse_transaction.side_effect = [
        MudaeParseError("not kakera"),
        MudaeParseError("not spheres"),
        MudaeParseError("not character"),
        None,
    ]

    detection = MudaeMessageRouter(parser).detect("transaction response")

    assert detection.kind == "trade"
    assert parser.parse_transaction.call_args_list == [
        call("transaction response", "gift_kakera"),
        call("transaction response", "gift_spheres"),
        call("transaction response", "gift_character"),
        call("transaction response", "trade"),
    ]


def test_router_consumes_divorce_prompt_as_its_separate_detected_kind() -> None:
    parser = Mock()
    parser.parse_kakera_reaction_receipt.side_effect = MudaeParseError("not receipt")
    parser.parse_kakera_reaction_blocked.side_effect = MudaeParseError("not blocked")
    parser.parse_transaction.side_effect = [MudaeParseError("not transaction")] * 4
    parser.parse_mudapins.side_effect = MudaeParseError("not mudapins")
    parser.parse_claim_confirmation.side_effect = MudaeParseError("not claim")

    detection = MudaeMessageRouter(parser).detect("divorce prompt response")

    assert detection.kind == "divorce_prompt"
    parser.parse_divorce_prompt.assert_called_once_with("divorce prompt response")
    parser.parse_divorce_declined.assert_not_called()
    parser.parse_divorce_confirmation.assert_not_called()


def test_router_probes_divorce_completion_after_prompt_and_declined() -> None:
    parser = Mock()
    parser.parse_kakera_reaction_receipt.side_effect = MudaeParseError("not receipt")
    parser.parse_kakera_reaction_blocked.side_effect = MudaeParseError("not blocked")
    parser.parse_transaction.side_effect = [MudaeParseError("not transaction")] * 4
    parser.parse_mudapins.side_effect = MudaeParseError("not mudapins")
    parser.parse_claim_confirmation.side_effect = MudaeParseError("not claim")
    parser.parse_divorce_prompt.side_effect = MudaeParseError("not prompt")
    parser.parse_divorce_declined.side_effect = MudaeParseError("not declined")

    detection = MudaeMessageRouter(parser).detect("divorce completion response")

    assert detection.kind == "divorce_complete"
    assert [call[0] for call in parser.method_calls] == [
        "parse_kakera_reaction_receipt",
        "parse_kakera_reaction_blocked",
        "parse_transaction",
        "parse_transaction",
        "parse_transaction",
        "parse_transaction",
        "parse_mudapins",
        "parse_claim_confirmation",
        "parse_divorce_prompt",
        "parse_divorce_declined",
        "parse_divorce_confirmation",
    ]


@pytest.mark.parametrize(
    ("kind", "response", "message"),
    [
        ("unknown", "anything", "Unsupported transaction kind: unknown"),
        (
            "trade",
            "This is not a transaction response.",
            "Expected a Mudae trade transaction response.",
        ),
    ],
)
def test_transaction_parser_preserves_exact_errors(
    kind: str, response: str, message: str
) -> None:
    with pytest.raises(MudaeParseError) as error:
        TransactionParser(MudaeParseError).parse(response, kind)

    assert str(error.value) == message


def test_parse_character_details_accepts_discord_custom_emojis_and_same_line_key() -> None:
    character = MudaeTextParser().parse_character_details(
        "Kaede Azusagawa\n"
        "Seishun Buta Yarou <:female:1>\n"
        "Animanga roulette · 238<:kakera:2> · <:bronzekey:3> (**1**)\n"
        "Claim Rank: #505\n"
        "Like Rank: #735"
    )

    assert character.gender == "female"
    assert character.kakera_value == 238
    assert character.key_type == "bronze"
    assert character.key_count == 1


def test_parse_character_details_rejoins_wrapped_series() -> None:
    # Sanitized real Mudae payload with a wrapped $im series identity.
    character = MudaeTextParser().parse_character_details(
        "Mudae\n"
        "Hestia\n"
        "Dungeon ni Deai wo Motomeru no\n"
        "wa Machigatteiru Darou ka :female:\n"
        "Game & Animanga · 624 Kakera · Key (6)\n"
        "Claim Rank: #150\n"
        "Like Rank: #215"
    )

    assert character.name == "Hestia"
    assert character.series == "Dungeon ni Deai wo Motomeru no wa Machigatteiru Darou ka"
    assert character.roulette == "game & animanga"
    assert character.key_count == 6


def test_parse_im_layout_with_game_and_animanga_gender_and_no_key() -> None:
    character = MudaeTextParser().parse_character_details(
        "Hatsune Miku\n"
        "VOCALOID :female:\n"
        "Game & Animanga · 1,195:kakera:\n"
        "Claim Rank: #1\n"
        "Like Rank: #3\n"
    )

    assert character.name == "Hatsune Miku"
    assert character.series == "VOCALOID"
    assert character.gender == "female"
    assert character.roulette == "game & animanga"
    assert character.kakera_value == 1195
    assert character.key_type is None
    assert character.key_count is None


def test_parse_character_details_accepts_ambiguous_gender_markers() -> None:
    character = MudaeTextParser().parse_character_details(
        "Kirby\n"
        "Kirby series :female::male:\n"
        "Game roulette · 500 Kakera\n"
        "Claim Rank: #1\n"
        "Like Rank: #2\n"
    )

    assert character.gender == "female,male"


def test_parse_current_im_layout_with_visual_kakera_and_key_icons() -> None:
    character = MudaeTextParser().parse_character_details(CURRENT_CHARACTER_DETAILS)

    assert character.name == "Mai Sakurajima"
    assert character.series == "Seishun Buta Yarou"
    assert character.gender is None
    assert character.roulette == "animanga"
    assert character.kakera_value == 1385
    assert character.claim_rank == 9
    assert character.like_rank == 19
    assert character.key_count == 6


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


def test_parse_roll_facade_matches_dedicated_parser() -> None:
    expected = RollParser(MudaeParseError).parse(ROLL)

    assert MudaeTextParser().parse_roll(ROLL) == expected


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


def test_parse_roll_ignores_wished_by_prefix_before_character_name() -> None:
    roll = MudaeTextParser().parse_roll(
        "Wished by <@147839232239599616>\n"
        "Marin Kitagawa\n"
        "Sono Bisque Doll wa Koi wo Suru\n"
        "Claims: #22\n"
        "813:kakera:"
    )

    assert roll.name == "Marin Kitagawa"
    assert roll.series == "Sono Bisque Doll wa Koi wo Suru"
    assert roll.claim_rank == 22


def test_parse_roll_accepts_a_positive_kakera_prefix() -> None:
    roll = MudaeTextParser().parse_roll("Mai Sakurajima\nSeishun Buta Yarou\n+1,386:kakera:")

    assert roll.kakera_value == 1386


def test_parse_roll_accepts_a_claim_card_with_a_separate_key_line() -> None:
    roll = MudaeTextParser().parse_roll(
        "Mudae-chan\n"
        "Mudae's Mascot\n"
        ":bronzekey: (1) $embedcolor unlocked!\n"
        "188 :kakera:"
    )

    assert roll.name == "Mudae-chan"
    assert roll.series == "Mudae's Mascot"
    assert roll.kakera_value == 188
    assert roll.displayed_key_type == "bronze"
    assert roll.displayed_key_count == 1


def test_parse_roll_accepts_current_ranked_roll_card_layout() -> None:
    roll = MudaeTextParser().parse_roll(
        "Mai Sakurajima\n"
        "Seishun Buta Yarou :female:\n"
        "Animanga roulette · 1,494:kakera: · :goldkey: (7)\n"
        "Claim Rank: #9\n"
        "Like Rank: #19\n"
    )

    assert roll.name == "Mai Sakurajima"
    assert roll.series == "Seishun Buta Yarou"
    assert roll.claim_rank == 9
    assert roll.kakera_value == 1494
    assert roll.displayed_key_type == "gold"
    assert roll.displayed_key_count == 7


def test_parse_roll_removes_starwish_marker_from_series() -> None:
    roll = MudaeTextParser().parse_roll(
        "Satoru Gojo\n"
        "Jujutsu Kaisen :sw:\n"
        ":bronzekey: (1) $embedcolor unlocked!\n"
        "1,133:kakera:\n"
        "(⭐1) · Belongs to cute_beagle_91130"
    )

    assert roll.name == "Satoru Gojo"
    assert roll.series == "Jujutsu Kaisen"
    assert roll.displayed_key_type == "bronze"
    assert roll.displayed_key_count == 1


def test_parse_roll_rejoins_a_wrapped_long_series_name() -> None:
    roll = MudaeTextParser().parse_roll(
        "Darkness\n"
        "Kono Subarashii Sekai ni Shukufuku\n"
        "wo!\n"
        "55:kakera:"
    )

    assert roll.name == "Darkness"
    assert roll.series == "Kono Subarashii Sekai ni Shukufuku wo!"
    assert roll.kakera_value == 55


def test_parse_roll_rejoins_a_wrapped_series_with_a_multiword_suffix() -> None:
    roll = MudaeTextParser().parse_roll(
        "Hestia\n"
        "Dungeon ni Deai wo Motomeru no\n"
        "wa Machigatteiru Darou ka\n"
        "55:kakera:"
    )

    assert roll.name == "Hestia"
    assert roll.series == "Dungeon ni Deai wo Motomeru no wa Machigatteiru Darou ka"


def test_parse_roll_rejoins_a_wrapped_series_with_an_uppercase_continuation() -> None:
    roll = MudaeTextParser().parse_roll(
        "Dark Rouge\n"
        "Yes! PreCure 5 the Movie: The Mirror\n"
        "Kingdom's Miraculous Adventure!\n"
        "28:kakera:"
    )

    assert roll.name == "Dark Rouge"
    assert roll.series == "Yes! PreCure 5 the Movie: The Mirror Kingdom's Miraculous Adventure!"
    assert roll.kakera_value == 28


def test_parse_roll_rejoins_wrapped_series_after_mudae_embed_title() -> None:
    roll = MudaeTextParser().parse_roll(
        "Mudae\n"
        "Hestia\n"
        "Dungeon ni Deai wo Motomeru no\n"
        "wa Machigatteiru Darou ka\n"
        "55:kakera:"
    )

    assert roll.name == "Hestia"
    assert roll.series == "Dungeon ni Deai wo Motomeru no wa Machigatteiru Darou ka"


def test_parse_roll_rejects_a_likely_title_series_split() -> None:
    with pytest.raises(MudaeParseError, match="Ambiguous Mudae roll identity"):
        MudaeTextParser().parse_roll(
            "Gyaru That Becomes Menhera After\n"
            "10 Days\n"
            "40:kakera:"
        )


def test_parse_timer_state_accepts_specialized_timer_commands() -> None:
    parser = MudaeTextParser()

    assert parser.parse_timer_state("$rt is available!").rt_available is True
    assert (
        parser.parse_timer_state(
            "The cooldown of $rt is not over. Time left: 10h 12 min. ($rtu)"
        ).rt_reset_minutes
        == 612
    )
    assert (
        parser.parse_timer_state(
            "The cooldown of $rt is not over. Time left: 10h 12 min. ($rtu)"
        ).rt_available
        is False
    )
    assert (
        parser.parse_timer_state(
            "You didn't unlock this command yet! Maybe in a distant future... ($kakera)"
        ).rt_available
        is False
    )
    assert parser.parse_timer_state("Next $dk in 5h 47 min.").daily_kakera_ready is False
    assert parser.parse_timer_state("$dk is ready!").daily_kakera_ready is True
    assert (
        parser.parse_timer_state(
            "(Keys LVL 6+) 4,500:kakera:to collect before the next reset (1h 45 min.)"
        ).gold_key_stock_remaining
        == 4500
    )
    timer = parser.parse_timer_state(
        "You have **0** rolls left. Next rolls reset in **49** min."
    )
    assert timer.rolls_left == 0
    assert timer.rolls_reset_minutes == 49


def test_parse_timer_state_accepts_kakera_reaction_cooldown() -> None:
    timer = MudaeTextParser().parse_timer_state(
        "You can't react to kakera for 11 min.\n"
        "Power: 32%\n"
        "Each kakera button consumes 36% of your reaction power.\n"
        "Your characters with 10+ keys consume half the power (18%)\n"
        "Stock: 33,441:kakera:"
    )

    assert timer.can_react_kakera_now is False
    assert timer.reaction_power_percent == 32
    assert timer.kakera_button_power_cost_percent == 36
    assert timer.soulmate_button_power_cost_percent == 18
    assert timer.kakera_stock == 33441


def test_parse_kakera_reaction_blocked_is_not_a_timer_snapshot() -> None:
    parser = MudaeTextParser()
    blocked = parser.parse_kakera_reaction_blocked(
        "**cute_beagle_91130**, You can't react to kakera for **34** min. ($ku)"
    )

    assert blocked.account_name == "cute_beagle_91130"
    assert blocked.cooldown_minutes == 34
    with pytest.raises(MudaeParseError):
        parser.parse_timer_state(
            "**cute_beagle_91130**, You can't react to kakera for **34** min. ($ku)"
        )


@pytest.mark.parametrize(
    ("response", "account_name", "cooldown_minutes"),
    [
        (
            "\u200b unrelated line\n"
            "\u200b<a:wave:123> **cute_beagle_91130**, You can't react to kakera for **2h 32 min**. ($KU)",
            ":wave: cute_beagle_91130",
            152,
        ),
        ("ernieuuu, You can't react to kakera for 7 min. ($ku)", "ernieuuu", 7),
    ],
)
def test_kakera_reaction_blocked_parser_scans_normalized_lines_and_durations(
    response: str, account_name: str, cooldown_minutes: int
) -> None:
    blocked = KakeraReactionBlockedParser(
        MudaeParseError, MudaeTextParser._duration_minutes
    ).parse(response)

    assert blocked.account_name == account_name
    assert blocked.cooldown_minutes == cooldown_minutes


def test_kakera_reaction_blocked_parser_facade_matches_dedicated_parser() -> None:
    response = "\u200b**cute_beagle_91130**, You can't react to kakera for **34** min. ($ku)"

    expected = KakeraReactionBlockedParser(
        MudaeParseError, MudaeTextParser._duration_minutes
    ).parse(response)

    assert MudaeTextParser().parse_kakera_reaction_blocked(response) == expected


@pytest.mark.parametrize(
    "response",
    [
        ", You can't react to kakera for 34 min. ($ku)",
        "prefix ernieuuu, You can't react to kakera for 34 min. ($ku) trailing",
        "***, You can't react to kakera for 34 min. ($ku)",
    ],
)
def test_kakera_reaction_blocked_parser_rejects_empty_or_unanchored_accounts(
    response: str,
) -> None:
    with pytest.raises(MudaeParseError) as error:
        KakeraReactionBlockedParser(
            MudaeParseError, MudaeTextParser._duration_minutes
        ).parse(response)

    assert str(error.value) == "Expected a compact Mudae Kakera reaction-blocked response."


def test_kakera_reaction_blocked_parser_preserves_unsupported_duration_error() -> None:
    with pytest.raises(MudaeParseError) as error:
        KakeraReactionBlockedParser(
            MudaeParseError, MudaeTextParser._duration_minutes
        ).parse("ernieuuu, You can't react to kakera for soon. ($ku)")

    assert str(error.value) == "Unsupported Mudae timer duration: 'soon'"


def test_parse_kakera_reaction_receipt() -> None:
    receipt = MudaeTextParser().parse_kakera_reaction_receipt(
        ":kakeraY: cute_beagle_91130 +497 ($k)"
    )

    assert receipt.reaction_label == ":kakeraY:"
    assert receipt.account_name == "cute_beagle_91130"
    assert receipt.kakera_earned == 497


def test_kakera_reaction_receipt_parser_facade_matches_dedicated_parser() -> None:
    response = "\u200b<a:kakeraY:123> **ernieuuu +1,234** ($k)"

    expected = KakeraReactionReceiptParser(MudaeParseError).parse(response)

    assert MudaeTextParser().parse_kakera_reaction_receipt(response) == expected


def test_parse_bold_kakera_reaction_receipt() -> None:
    receipt = MudaeTextParser().parse_kakera_reaction_receipt(
        ":kakeraY: **ernieuuu +524** ($k)"
    )

    assert receipt.reaction_label == ":kakeraY:"
    assert receipt.account_name == "ernieuuu"
    assert receipt.kakera_earned == 524


def test_parse_free_bold_kakera_reaction_receipt() -> None:
    receipt = MudaeTextParser().parse_kakera_reaction_receipt(
        ":kakeraP: (Free) **ernieuuu +110** ($k)"
    )

    assert receipt.reaction_label == ":kakeraP:"
    assert receipt.account_name == "ernieuuu"
    assert receipt.kakera_earned == 110


def test_parse_kakera_breakdown_reaction_receipt() -> None:
    receipt = MudaeTextParser().parse_kakera_reaction_receipt(
        ":kakeraL: breaks down into:kakeraB: +:kakeraB: +:kakeraB: "
        "+:kakeraR: +:kakeraB: => **ernieuuu +2,202** ($k)"
    )

    assert receipt.reaction_label == ":kakeraL:"
    assert receipt.account_name == "ernieuuu"
    assert receipt.kakera_earned == 2202


def test_kakera_reaction_receipt_scans_normalized_lines_in_source_order() -> None:
    receipt = KakeraReactionReceiptParser(MudaeParseError).parse(
        "unrelated line\n"
        "<:kakeraG:123> **first_account +497** ($k)\n"
        ":kakeraY: second_account +524 ($k)"
    )

    assert receipt.reaction_label == ":kakeraG:"
    assert receipt.account_name == "first_account"
    assert receipt.kakera_earned == 497


def test_kakera_reaction_receipt_prefers_breakdown_pattern_on_each_line() -> None:
    receipt = KakeraReactionReceiptParser(MudaeParseError).parse(
        ":kakeraL: breaks down into:kakeraB: +:kakeraR: => account +2,202 ($k)"
    )

    assert receipt.reaction_label == ":kakeraL:"
    assert receipt.account_name == "account"
    assert receipt.kakera_earned == 2202


def test_kakera_reaction_receipt_rejects_unsupported_text_with_exact_error() -> None:
    with pytest.raises(MudaeParseError) as error:
        KakeraReactionReceiptParser(MudaeParseError).parse("not a receipt")

    assert str(error.value) == (
        "Expected a Mudae Kakera reaction receipt such as `:kakeraY: user +497 ($k)`."
    )


def test_parse_keyed_harem_page_from_mmy_output() -> None:
    page = MudaeTextParser().parse_harem_key_page(HAREM_KEY_PAGE)

    assert page.page_number == 1
    assert page.page_count == 6
    assert len(page.entries) == 15
    assert page.entries[0].name == "Albedo"
    assert page.entries[0].key_type == "gold"
    assert page.entries[0].key_count == 7
    assert page.entries[-1].name == "Nezuko Kamado"


def test_harem_key_parser_normalizes_custom_emojis_and_trimmed_lines() -> None:
    page = HaremKeyParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(
        "\u200b<a:goldkey:123>\u200b\n"
        "\u200bAlbedo\u200b \u00b7 \u200b<:GoLdKeY:456>  (\u200b0\u200b)\u200b\n"
        "\u200bPage 2 / 6\u200b\n"
        "\u200bTotal value: 0:kakera:\u200b"
    )

    assert page.page_number == 2
    assert page.page_count == 6
    assert page.total_harem_value == 0
    assert len(page.entries) == 1
    assert page.entries[0].name == "Albedo"
    assert page.entries[0].key_type == "gold"
    assert page.entries[0].key_count == 0
    assert page.entries[0].kakera_value is None


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


def test_harem_key_parser_accepts_complete_page_with_metadata_absent() -> None:
    page = HaremKeyParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse("  Miku Nakano · :SILVERkey:  (12)  0 ka  \n")

    assert page.page_number is None
    assert page.page_count is None
    assert page.total_harem_value is None
    assert [(entry.name, entry.key_type, entry.key_count, entry.kakera_value) for entry in page.entries] == [
        ("Miku Nakano", "silver", 12, 0)
    ]


def test_harem_key_parser_facade_matches_dedicated_parser() -> None:
    expected = HaremKeyParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(HAREM_KEY_PAGE)

    assert MudaeTextParser().parse_harem_key_page(HAREM_KEY_PAGE) == expected


def test_harem_key_parser_uses_injected_line_and_number_seams() -> None:
    lines = Mock(
        return_value=[
            "Alpha · :goldkey: (7) 1,234 ka",
            "Total value: 2,345 ka",
        ]
    )
    number = Mock(side_effect=comma_int)

    page = HaremKeyParser(MudaeParseError, lines, number).parse("raw response")

    lines.assert_called_once_with("raw response")
    assert number.call_args_list == [call("1,234"), call("2,345")]
    assert page.entries[0].kakera_value == 1234
    assert page.total_harem_value == 2345


def test_harem_key_parser_rejects_text_without_keyed_entries_with_exact_error() -> None:
    with pytest.raises(MudaeParseError) as error:
        HaremKeyParser(
            MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
        ).parse("ernieuuu's harem\nPage 1 / 6")

    assert str(error.value) == "No keyed harem entries found in the Mudae $mmy= output."


def test_parse_ranked_harem_page_from_mmrk_output() -> None:
    page = MudaeTextParser().parse_ranked_harem_page(
        "ernieuuu's harem\n"
        "AVG: 1,361\n"
        "Top 15 value: 169\n"
        "Total value: 163,373:kakera:\n"
        "#2 - Zero Two 1,440 ka\n"
        "#3 - Rem 1,426 ka\n"
        "#4 - Saber 1,478 ka\n"
        "Page 1 / 38"
    )

    assert page.page_number == 1
    assert page.page_count == 38
    assert [(entry.name, entry.claim_rank, entry.kakera_value) for entry in page.entries] == [
        ("Zero Two", 2, 1440),
        ("Rem", 3, 1426),
        ("Saber", 4, 1478),
    ]
    assert all(entry.roulette_types is None for entry in page.entries)


def test_ranked_harem_parser_preserves_full_partial_zero_and_empty_evidence() -> None:
    page = RankedHaremParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(
        "ERNIEUUU'S HAREM\n"
        "#0 - Zero · ($HA, $wg) - :GoldKey: (**0**) 0 ka\n"
        "#2 - Partial\n"
        "#3 - Current empty -\n"
    )

    assert [(entry.name, entry.claim_rank) for entry in page.entries] == [
        ("Zero", 0),
        ("Partial", 2),
        ("Current empty -", 3),
    ]
    assert page.entries[0].kakera_value == 0
    assert page.entries[0].roulette_types == ("ha", "wg")
    assert page.entries[0].key_type == "gold"
    assert page.entries[0].key_count == 0
    assert page.entries[1].kakera_value is None
    assert page.entries[1].roulette_types is None
    assert page.entries[1].key_type is None
    assert page.entries[1].key_count is None
    assert page.page_number is None
    assert page.page_count is None


def test_ranked_harem_parser_normalizes_custom_emojis_and_markdown() -> None:
    page = RankedHaremParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(
        "**Mudae's harem**\n"
        "**#4 - <:Saber:123> \u00b7 ($HA, $WG) - <a:GoldKey:789> (**7**) **1,234** ka**\n"
        "Page 2 / 6"
    )

    assert page.page_number == 2
    assert page.page_count == 6
    assert page.entries[0].name == ":Saber:"
    assert page.entries[0].roulette_types == ("ha", "wg")
    assert page.entries[0].key_type == "gold"
    assert page.entries[0].key_count == 7
    assert page.entries[0].kakera_value == 1234


def test_ranked_harem_parser_facade_matches_dedicated_parser() -> None:
    ranked_page_text = "ernieuuu's harem\n#2 - Zero Two 1,440 ka\nPage 1 / 38"
    expected = RankedHaremParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(ranked_page_text)

    assert MudaeTextParser().parse_ranked_harem_page(ranked_page_text) == expected


def test_ranked_harem_parser_uses_injected_line_and_number_seams() -> None:
    lines = Mock(
        return_value=[
            "Alpha's harem",
            "#7 - Alpha \u00b7 ($ha) - :goldkey: (7) 1,234 ka",
        ]
    )
    number = Mock(side_effect=comma_int)

    page = RankedHaremParser(MudaeParseError, lines, number).parse("raw response")

    lines.assert_called_once_with("raw response")
    assert number.call_args_list == [call("7"), call("1,234")]
    assert page.entries[0].claim_rank == 7
    assert page.entries[0].kakera_value == 1234


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "not a valid response",
            "Expected a Mudae ranked harem header.",
        ),
        (
            "ernieuuu's harem\nPage 1 / 6",
            "No ranked harem entries found in the Mudae `$mmr` output.",
        ),
    ],
)
def test_ranked_harem_parser_rejects_invalid_pages_with_exact_errors(text, message) -> None:
    with pytest.raises(MudaeParseError) as error:
        RankedHaremParser(
            MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
        ).parse(text)

    assert str(error.value) == message


def test_parse_ranked_harem_page_from_mmr_and_mmrk_compact_output() -> None:
    parser = MudaeTextParser()
    mmr = parser.parse_ranked_harem_page(
        "ernieuuu's harem\n"
        "AVG: 4,867\n"
        "Top 15 value: 2,630\n"
        "#2 - Zero Two\n"
        "#4 - Saber\n"
        "Page 1 / 3"
    )
    mmrk = parser.parse_ranked_harem_page(
        "ernieuuu's harem\n"
        "AVG: 4,867\n"
        "Top 15 value: 2,630\n"
        "Total value: 9,588:kakera:\n"
        "#2 - Zero Two 1,052 ka\n"
        "#4 - Saber 996 ka\n"
        "Page 1 / 3"
    )

    assert [(entry.name, entry.kakera_value) for entry in mmr.entries] == [
        ("Zero Two", None),
        ("Saber", None),
    ]
    assert [(entry.name, entry.kakera_value) for entry in mmrk.entries] == [
        ("Zero Two", 1052),
        ("Saber", 996),
    ]
    assert all(entry.roulette_types is None for entry in mmr.entries)
    assert all(entry.roulette_types is None for entry in mmrk.entries)


def test_parse_ranked_harem_page_ignores_discord_markdown_emphasis() -> None:
    page = MudaeTextParser().parse_ranked_harem_page(
        "ernieuuu's harem\n"
        "AVG: 4,867\n"
        "Top 15 value: 2,630\n"
        "**#2** - **Zero Two** **1,052** ka\n"
        "**#4 - Saber 996 ka**\n"
        "Page 1 / 3"
    )

    assert [(entry.name, entry.claim_rank, entry.kakera_value) for entry in page.entries] == [
        ("Zero Two", 2, 1052),
        ("Saber", 4, 996),
    ]


def test_parse_ranked_harem_page_from_mmrt_output_reads_roulette_types() -> None:
    page = MudaeTextParser().parse_ranked_harem_page(
        "cute_beagle_91130's harem\n"
        "35 $wa, 65 $ha, 20 $wg, 20 $hg\n"
        "#11 - Satoru Gojo · ($ha) - :goldkey: (1) 903 ka\n"
        "#18 - Kirby · ($wa, $ha, $wg, $hg) - :silverkey: (3) 271 ka\n"
        "Page 1 / 5"
    )

    assert page.entries[0].roulette_types == ("ha",)
    assert page.entries[1].roulette_types == ("wa", "ha", "wg", "hg")
    assert page.entries[0].key_type == "gold"
    assert page.entries[0].key_count == 1
    assert page.entries[0].kakera_value == 903


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


def test_player_bonus_parser_preserves_partial_zero_unknown_repeated_and_unsupported_metrics() -> None:
    lines = Mock(
        return_value=[
            "Player Bonuses",
            ":addroll: · Rolls per hour: +0",
            "Wishlist slots: unknown",
            "Rolls per hour: malformed",
            "Starwish slots: +0",
            "Starwish slots: +4",
            "Kakera max power: 0",
            "Power cost per kakera button: malformed",
            "Random kakera per light kakera: 0-0",
            "Unsupported modifier: retained",
        ]
    )

    bonus = PlayerBonusParser(MudaeParseError, lines).parse("raw bonus response")

    lines.assert_called_once_with("raw bonus response")
    assert [metric.label for metric in bonus.metrics] == [
        "Rolls per hour",
        "Wishlist slots",
        "Rolls per hour",
        "Starwish slots",
        "Starwish slots",
        "Kakera max power",
        "Power cost per kakera button",
        "Random kakera per light kakera",
        "Unsupported modifier",
    ]
    assert bonus.rolls_per_hour_bonus is None
    assert bonus.wishlist_slot_bonus is None
    assert bonus.starwish_slot_bonus == 4
    assert bonus.kakera_max_power_percent == 0
    assert bonus.kakera_button_power_cost_percent is None
    assert bonus.light_kakera_minimum == 0
    assert bonus.light_kakera_maximum == 0


def test_player_bonus_parser_accepts_metrics_without_header_and_facade_matches() -> None:
    text = (
        "\u200b\n"
        "<:addroll:123> · Rolls per hour: +9\n"
        "\n"
        "Unsupported modifier: retained\n"
    )
    expected = PlayerBonusParser(
        MudaeParseError, MudaeTextParser._lines
    ).parse(text)

    assert MudaeTextParser().parse_player_bonus(text) == expected
    assert [metric.label for metric in expected.metrics] == [
        "Rolls per hour",
        "Unsupported modifier",
    ]
    assert expected.rolls_per_hour_bonus == 9


def test_player_bonus_parser_rejects_missing_metrics_with_exact_error() -> None:
    with pytest.raises(MudaeParseError) as error:
        PlayerBonusParser(MudaeParseError, MudaeTextParser._lines).parse(
            "Player Bonuses\nRolls per hour:\nnot a metric"
        )

    assert str(error.value) == "No player bonus metrics found in the Mudae $bonus output."


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


def test_wishlist_parser_preserves_zero_counts_source_order_marker_evidence_and_noise() -> None:
    lines = Mock(
        return_value=[
            "prefix Wishlist - 0 / 13 $WL, 0 / 2 $SW suffix",
            "**First** \u2705 \u2b50 :kakera:",
            "\u2705 \u2b50 :kakera:",
            "noise",
            "**Second**",
        ]
    )

    wishlist = WishlistParser(MudaeParseError, lines).parse("raw wishlist response")

    lines.assert_called_once_with("raw wishlist response")
    assert (
        wishlist.wishlist_count,
        wishlist.wishlist_capacity,
        wishlist.starwish_count,
        wishlist.starwish_capacity,
    ) == (0, 13, 0, 2)
    assert [entry.model_dump() for entry in wishlist.entries] == [
        {
            "name": "First",
            "is_starwish": True,
            "is_owned_marker_present": True,
            "kakera_marker_present": True,
        },
        {
            "name": "noise",
            "is_starwish": False,
            "is_owned_marker_present": False,
            "kakera_marker_present": False,
        },
        {
            "name": "Second",
            "is_starwish": False,
            "is_owned_marker_present": False,
            "kakera_marker_present": False,
        },
    ]


def test_wishlist_parser_preserves_custom_emoji_zero_width_and_markdown_normalization() -> None:
    text = (
        "\u200b**ernieuuu's WISHLIST - 0/13 $WL, 0/2 $SW**\n"
        "\n"
        "\u200b**Custom** \u2705 <:kakera:123>\n"
        "**Starred** \u2b50\n"
    )

    wishlist = WishlistParser(MudaeParseError, MudaeTextParser._lines).parse(text)

    assert [entry.model_dump() for entry in wishlist.entries] == [
        {
            "name": "Custom",
            "is_starwish": False,
            "is_owned_marker_present": True,
            "kakera_marker_present": True,
        },
        {
            "name": "Starred",
            "is_starwish": True,
            "is_owned_marker_present": False,
            "kakera_marker_present": False,
        },
    ]


def test_parse_wishlist_facade_matches_dedicated_parser() -> None:
    text = (
        "**ernieuuu's Wishlist - 13/13 $wl, 2/2 $sw**\n"
        "**Saber** \u2705:kakera:\n"
        "**Emilia** \u2705 \u2b50\n"
    )
    expected = WishlistParser(MudaeParseError, MudaeTextParser._lines).parse(text)

    assert MudaeTextParser().parse_wishlist(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Wishlist - x/13 $wl, 0/2 $sw\nEntry",
        "not a wishlist response",
    ],
)
def test_wishlist_parser_rejects_missing_or_malformed_header_with_exact_error(text: str) -> None:
    with pytest.raises(MudaeParseError) as error:
        WishlistParser(MudaeParseError, MudaeTextParser._lines).parse(text)

    assert str(error.value) == "Expected a Mudae $wl header with $wl and $sw capacities."


def test_wishlist_parser_rejects_marker_only_output_with_exact_error() -> None:
    with pytest.raises(MudaeParseError) as error:
        WishlistParser(MudaeParseError, MudaeTextParser._lines).parse(
            "Wishlist - 0/13 $wl, 0/2 $sw\n\u2705 \u2b50 :kakera:"
        )

    assert str(error.value) == "No wishlist entries found in the Mudae $wl output."


def test_wishlist_router_detection_remains_loose_until_parser_revalidation() -> None:
    text = "Wishlist - 0/13 $wl, 0/2 $sw\n\u2705 \u2b50 :kakera:"

    assert MudaeMessageRouter().detect(text).kind == "wishlist"
    with pytest.raises(MudaeParseError):
        MudaeTextParser().parse_wishlist(text)


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
    assert disablelist.western_disabled is True
    assert disablelist.irl_disabled is True
    assert [(entry.name, entry.disabled_count) for entry in disablelist.entries] == [
        ("Kadokawa Corporation", 13207),
        ("Mobile Games", 16769),
    ]


def test_parse_disablelist_accepts_totals_split_across_embed_lines() -> None:
    disablelist = MudaeTextParser().parse_disablelist(
        "ernieuuu's Disablelist (13/16)\n"
        "107,529 disabled\n"
        "41,247 $wa, 42,438 $ha, 20,996 $wg, 14,789 $hg\n"
    )

    assert disablelist.total_disabled == 107529
    assert disablelist.disabled_wa == 41247
    assert disablelist.disabled_hg == 14789
    assert disablelist.western_disabled is None
    assert disablelist.irl_disabled is None


def test_disablelist_parser_preserves_seams_zero_evidence_repeated_limits_and_order() -> None:
    lines = Mock(
        return_value=[
            "user's Disablelist (0/0)",
            "0 disabled (0 $wa, 0 $ha, 0 $wg, 0 $hg)",
            "Pool limit reached: 0 $wa",
            "Pool limit reached: 4 $wa",
            "Pool limit reached: 0 $ha",
            "Western animanga series are completely disabled ($togglewestern)",
            "IRL series are completely disabled ($toggleirl)",
            "First bundle (0)",
            "malformed bundle (not-a-number)",
            "Second bundle (0)",
        ]
    )
    number = Mock(side_effect=lambda value: int(value.replace(",", "")))

    state = DisableListParser(MudaeParseError, lines, number).parse("raw disablelist")

    lines.assert_called_once_with("raw disablelist")
    assert state.slots_used == 0
    assert state.slots_capacity == 0
    assert state.total_disabled == 0
    assert state.disabled_wa == 0
    assert state.disabled_ha == 0
    assert state.disabled_wg == 0
    assert state.disabled_hg == 0
    assert state.wa_pool_limit == 4
    assert state.ha_pool_limit == 0
    assert state.western_disabled is True
    assert state.irl_disabled is True
    assert state.entries == (
        DisableListEntry(name="First bundle", disabled_count=0),
        DisableListEntry(name="Second bundle", disabled_count=0),
    )


def test_disablelist_parser_normalizes_custom_emojis_and_preserves_unobserved_toggles() -> None:
    state = DisableListParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(
        "\u200b<:warning:123> user's Disablelist (1/5)\n"
        "1 disabled\n"
        "(0 $wa, 1 $ha, 0 $wg, 0 $hg)\n"
        "<:warning:123> Pool limit reached: 0 $wa\n"
        "<:bundle:456> (0)\n"
    )

    assert state.total_disabled == 1
    assert state.disabled_wa == 0
    assert state.disabled_ha == 1
    assert state.wa_pool_limit == 0
    assert state.ha_pool_limit is None
    assert state.western_disabled is None
    assert state.irl_disabled is None
    assert state.entries == (DisableListEntry(name=":bundle:", disabled_count=0),)


def test_disablelist_parser_facade_matches_dedicated_parser() -> None:
    text = (
        "user's Disablelist (1/5)\n"
        "1 disabled (1 $wa, 0 $ha, 0 $wg, 0 $hg)\n"
        "Bundle (1)\n"
    )

    expected = DisableListParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(text)

    assert MudaeTextParser().parse_disablelist(text) == expected


def test_disablelist_parser_rejects_missing_header_or_totals_with_exact_error() -> None:
    parser = DisableListParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    )

    with pytest.raises(MudaeParseError) as error:
        parser.parse("Disablelist (0/0)")

    assert str(error.value) == "Expected a Mudae $dl header and disabled-pool totals."


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


def test_parse_topx_accepts_the_actual_discord_unavailable_marker() -> None:
    page = MudaeTextParser().parse_unavailable_characters(
        "🏆 TOP 1000\n"
        "#10 - 2B - NieR: Automata 🚫\n"
        "#88 - Venom - Marvel 🚫 ($togglewestern)\n"
        "Page 1 / 67"
    )

    assert [(character.name, character.reason) for character in page.characters] == [
        ("2B", None),
        ("Venom", "$togglewestern"),
    ]


def test_unavailable_character_parser_preserves_seams_zero_metadata_and_row_order() -> None:
    lines = Mock(
        return_value=[
            "prefix TOP 0 suffix",
            "#1,002 - First Character 💞 - First Series 🚫",
            "malformed row",
            "#1,003 - Second Character - Second Series 🚫 (reason)",
            "Page 0 / 0",
        ]
    )
    number = Mock(side_effect=lambda value: int(value.replace(",", "")))

    page = UnavailableCharacterPageParser(
        MudaeParseError, lines, number
    ).parse("raw topx")

    lines.assert_called_once_with("raw topx")
    assert page.limit == 0
    assert page.page_number == 0
    assert page.page_count == 0
    assert [(character.name, character.series, character.claim_rank, character.reason) for character in page.characters] == [
        ("First Character", "First Series", 1002, None),
        ("Second Character", "Second Series", 1003, "reason"),
    ]


def test_unavailable_character_parser_preserves_normalization_and_markdown_text() -> None:
    page = UnavailableCharacterPageParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(
        "\u200b<:trophy:123> TOP 1,000\n"
        "#10 - **Character <:heart:456>** - **Series Name** 🚫 ($togglewestern)\n"
        "\u200bPage 2 / 3\u200b"
    )

    assert page.limit == 1000
    assert page.page_number == 2
    assert page.page_count == 3
    assert page.characters[0].name == "**Character :heart:**"
    assert page.characters[0].series == "**Series Name**"
    assert page.characters[0].reason == "$togglewestern"


def test_unavailable_character_parser_skips_malformed_and_empty_reason_rows() -> None:
    page = UnavailableCharacterPageParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(
        "#1 - Missing marker - Series\n"
        "#bad - Invalid rank - Series 🚫\n"
        "#2 - Missing series - 🚫\n"
        "#3 - Empty reason - Series 🚫 ()\n"
        "#4 - Valid - Series 🚫"
    )

    assert page.limit is None
    assert page.page_number is None
    assert page.page_count is None
    assert [(character.name, character.claim_rank) for character in page.characters] == [
        ("Valid", 4),
    ]


def test_unavailable_character_parser_facade_matches_dedicated_parser() -> None:
    text = (
        "🏆 TOP 1000\n"
        "#10 - 2B - NieR: Automata 🚫\n"
        "#88 - Venom - Marvel 🚫 ($togglewestern)\n"
        "Page 1 / 67"
    )

    expected = UnavailableCharacterPageParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    ).parse(text)

    assert MudaeTextParser().parse_unavailable_characters(text) == expected


def test_unavailable_character_parser_rejects_missing_rows_with_exact_error() -> None:
    parser = UnavailableCharacterPageParser(
        MudaeParseError, MudaeTextParser._lines, MudaeTextParser._number
    )

    with pytest.raises(MudaeParseError) as error:
        parser.parse("🏆 TOP 0\nPage 0 / 0")

    assert str(error.value) == "No unavailable characters found in the Mudae $topx output."


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


def test_parse_kakera_state_accepts_discord_emphasis_and_emoji_spacing() -> None:
    state = MudaeTextParser().parse_kakera_state(
        "How to collect kakera in your server (change the options with $togglekakera):\n"
        "$dailykakera\n"
        "You have **23,523** :kakera: !\n"
        ":BronzeIV: **Bronze IV** · Max reached!\n"
        ":SilverIV: **Silver IV** · Max reached!\n"
        ":GoldIV: **Gold IV** · Max reached!\n"
        ":SapphireIV: **Sapphire IV** · Max reached!\n"
        ":RubyIV: **Ruby IV** · Max reached!\n"
        ":EmeraldIV: **Emerald IV** · Max reached!\n"
        ":DiamondIV: **Diamond IV** · Max reached!"
    )

    assert state.kakera_balance == 23523
    assert len(state.badges) == 7
    assert all(badge.max_reached for badge in state.badges)


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


def test_parse_tower_state_accepts_current_format_without_completed_tower_count() -> None:
    state = MudaeTextParser().parse_tower_state(
        "Your current level is:tow7:\n"
        "The next level costs 40,000:kakera:\n"
        "You have 26,490:kakera:\n"
        "☑️ [1] +2 wishlist slots\n"
        "[6] +30 spheres when you claim a character"
    )

    assert state.current_level == 7
    assert state.completed_towers is None
    assert state.next_level_cost == 40000
    assert state.kakera_balance == 26490
    assert state.built_perk_ids == (1,)


def test_parse_tower_state_preserves_textual_zero_completed_tower_count() -> None:
    state = MudaeTextParser().parse_tower_state(
        "Your current level is:tow2: (+ 0 towers)\n"
        "The next level costs 75,000:kakera:\n"
        "You have 7,673:kakera:\n"
        "☑️ [5] Unveil 1 random button for the $oh command"
    )

    assert state.completed_towers == 0


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


def test_parse_kakeraloot_state_accepts_the_buy_loots_guard_message() -> None:
    state = MudaeTextParser().parse_kakeraloot_state(
        "You need to buy kakeraloots before using this command ($kl)\n"
        "Type $infokl to get more infos about kakeraloots."
    )

    assert not state.has_kakeraloots
    assert state.status_note == "No Kakeraloots bought; Mudae did not report loot statistics."


def test_parse_kakeraloot_state_accepts_prerequisite_guard_message() -> None:
    state = MudaeTextParser().parse_kakeraloot_state(
        "Prerequisites: Sapphire I + Ruby I + Emerald I ($infokl)"
    )

    assert not state.has_kakeraloots


def test_parse_kakeraloot_state_accepts_layout_without_rolls_stacked() -> None:
    state = MudaeTextParser().parse_kakeraloot_state(
        ":disablemore: $disable limits: -102 $wa/$ha, -68 $wg/$hg\n"
        ":wishprotect: Protected wish: LVL 42 (spawn probability: 1/4,642)\n"
        ":mudapin: Mudapins: 22 ($mp)\n"
        ":rtcd: $rt: -2h cooldown\n"
        ":addroll: +1 permanent roll\n"
        ":sw: 1 star branch (+0 $sw)\n"
        "Quantity LVL 23\n"
        "Quality LVL 6\n"
        "$kl usage: 256 (:kakeraC:+1)\n"
        "20,831:kakera:"
    )

    assert state.rolls_stacked is None
    assert state.quantity_level == 23
    assert state.usage_count == 256
    assert state.kakera_balance == 20831


def test_parse_kakeraloot_state_accepts_compact_new_account_layout() -> None:
    state = MudaeTextParser().parse_kakeraloot_state(
        "cute_beagle_91130 - Kakeraloots\n"
        "Quantity LVL 5\n"
        "Quality LVL 0\n"
        "$kl usage: 1\n"
        "31,271:kakera:"
    )

    assert state.quantity_level == 5
    assert state.quality_level == 0
    assert state.usage_count == 1
    assert state.kakera_balance == 31271
    assert state.disable_wa_ha_reduction is None
    assert state.protected_wish_level is None


def test_parse_kakeraloot_state_accepts_live_custom_emoji_format() -> None:
    state = MudaeTextParser().parse_kakeraloot_state(
        "ernieuuu - Kakeraloots\n"
        "<:disablemore:123> $disable limits: -102 $wa/$ha, -68 $wg/$hg\n"
        "<:wishprotect:123> Protected wish: LVL 42 (spawn probability: 1/4,642)\n"
        "<:mudapin:123> Mudapins: 22 ($mp)\n"
        "<:rtcd:123> $rt: -2h cooldown\n"
        "<:addroll:123> +1 permanent roll\n"
        "<:sw:123> 1 star branch (+0 $sw)\n\n"
        "Quantity LVL 23\n"
        "Quality LVL 6\n"
        "$kl usage: 256 (<:kakeraC:123>+1)\n"
        "23,965 <:kakera:123>"
    )

    assert state.quantity_level == 23
    assert state.quality_level == 6
    assert state.usage_count == 256
    assert state.kakera_balance == 23965


def test_parse_kakeraloot_state_accepts_animated_custom_emoji() -> None:
    # Sanitized real Mudae payload using Discord's animated custom-emoji form.
    state = MudaeTextParser().parse_kakeraloot_state(
        "sample-account - Kakeraloots\n"
        "<a:disablemore:100001> $disable limits: -102 $wa/$ha, -68 $wg/$hg\n"
        "<a:wishprotect:100002> Protected wish: LVL 42 (spawn probability: 1/4,642)\n"
        "<a:mudapin:100003> Mudapins: 22 ($mp)\n"
        "<a:rtcd:100004> $rt: -2h cooldown\n"
        "<a:addroll:100005> +1 permanent roll\n"
        "<a:sw:100006> 1 star branch (+0 $sw)\n\n"
        "Quantity LVL 23\n"
        "Quality LVL 6\n"
        "$kl usage: 256 (<a:kakeraC:100007>+1)\n"
        "23,965 <a:kakera:100008>"
    )

    assert state.quantity_level == 23
    assert state.quality_level == 6
    assert state.usage_count == 256
    assert state.kakera_balance == 23965


@pytest.mark.parametrize("missing_field", ["quantity", "quality", "usage", "kakera_balance"])
def test_parse_kakeraloot_state_rejects_missing_required_core_field(missing_field: str) -> None:
    # Each malformed case is derived from this sanitized real Mudae $lk payload.
    core_lines = {
        "quantity": "Quantity LVL 23",
        "quality": "Quality LVL 6",
        "usage": "$kl usage: 256 (:kakeraC:+1)",
        "kakera_balance": "23,965:kakera:",
    }
    payload_lines = [
        "sample-account - Kakeraloots",
        "Rolls stacked: 1 ($us)",
        "$disable limits: -102 $wa/$ha, -68 $wg/$hg",
        "Protected wish: LVL 42 (spawn probability: 1/4,642)",
        "Mudapins: 22 ($mp)",
        "$rt: -2h cooldown",
        "+1 permanent roll",
        "1 star branch (+0 $sw)",
    ]
    payload_lines.extend(
        line for field, line in core_lines.items() if field != missing_field
    )

    with pytest.raises(
        MudaeParseError,
        match=r"Expected a complete Mudae \$lk Kakeraloot stats response\.",
    ):
        MudaeTextParser().parse_kakeraloot_state("\n".join(payload_lines))


def test_parse_sphere_result_reads_color_gains_total_and_stock() -> None:
    state = MudaeTextParser().parse_sphere_result(
        "You can click 7 times on the buttons below (2 minutes).\n"
        "Find 3 purple spheres (out of 4) to turn the 4th purple into a red sphere or more.\n"
        ":spB: +18\n"
        ":spT: +28\n"
        ":spP: (Free) +13\n"
        ":sp: +158\n"
        ":spG: +43 (Stock: 3,655)"
    )

    assert state.clicks_available == 7
    assert state.click_window_minutes == 2
    assert state.purple_target == 3
    assert state.purple_total == 4
    assert [(gain.sphere_type, gain.amount, gain.is_free) for gain in state.gains] == [
        ("b", 18, False),
        ("t", 28, False),
        ("p", 13, True),
        ("g", 43, False),
    ]
    assert state.total_gained == 158
    assert state.stock == 3655


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


def test_parse_personal_rare_accepts_discord_emphasis_and_help_text() -> None:
    state = MudaeTextParser().parse_personal_rare(
        "Syntax: $personalrare <Number between 1 and the current $setrare value for your server>\n"
        "Effect: if you want claimed characters to appear more often during YOUR rolls, lower the value.\n"
        "Current $setrare value for your server (admin command): 4\n"
        "**Your current $personalrare: 1**"
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


def test_parse_kakeraloot_settings_accepts_discord_formatting_and_custom_emoji() -> None:
    settings = MudaeTextParser().parse_kakeraloot_settings(
        "Kakeraloots\n"
        "Each $kl costs **500** <:kakera:123456789> (admins can change this value with $klvalue)\n"
        "Reaching the level 1 of quantity or quality costs **2,000** <:kakera:123456789> "
        "(increased by **200**/level, values can't be changed)"
    )

    assert settings.loot_cost == 500
    assert settings.quantity_quality_base_cost == 2000
    assert settings.quantity_quality_level_increment == 200


def test_profile_parser_characterizes_full_grounded_variant() -> None:
    profile = MudaeTextParser().parse_profile(
        "ernieuuu\n"
        "Collection size: 567 (100%:female: 0% :male:)\n"
        "Pokédex: 4 Pokémon :piplup: :sentret: :toedscool: :psyduck:\n"
        "Reacts:\n"
        "48x:kakeraP: 36x:kakera: 62x:kakeraT: 585x:kakeraY: 9x:kakeraC:\n"
        "Mudapins: 22/2,347\n"
        "23,965:kakera:\n"
        "Keys: 409:bronzekey: 82:silverkey: 6:goldkey:\n"
        "3,827 :sp:\n"
        "208x:spP: 573x:spB: 317x:spT: 105x:sp:\n"
        ":silvmudae::MudaeBirthday7::BronzeIV::DiamondIV:"
    )

    assert profile.profile_name == "ernieuuu"
    assert profile.collection_size == 567
    assert (profile.female_percent, profile.male_percent) == (100, 0)
    assert profile.pokedex_count == 4
    assert profile.pokedex_pokemon == ("piplup", "sentret", "toedscool", "psyduck")
    assert profile.kakera_reacts == {
        ":kakeraP:": 48,
        ":kakera:": 36,
        ":kakeraT:": 62,
        ":kakeraY:": 585,
        ":kakeraC:": 9,
    }
    assert profile.mudapins_collected == 22
    assert profile.mudapins_total == 2347
    assert profile.kakera_balance == 23965
    assert (profile.bronze_keys, profile.silver_keys, profile.gold_keys) == (409, 82, 6)
    assert profile.sphere_stock == 3827
    assert profile.spheres == {
        ":spP:": 208,
        ":spB:": 573,
        ":spT:": 317,
        ":sp:": 105,
    }
    assert profile.displayed_badges == (":silvmudae:", ":MudaeBirthday7:", ":BronzeIV:", ":DiamondIV:")
    assert profile.pokedex_observed is True
    assert profile.reactions_observed is True
    assert profile.mudapins_observed is True
    assert profile.kakera_balance_observed is True
    assert profile.keys_observed is True
    assert profile.bronze_keys_observed is True
    assert profile.silver_keys_observed is True
    assert profile.gold_keys_observed is True
    assert profile.sphere_stock_observed is True
    assert profile.sphere_counts_observed is True
    assert profile.badges_observed is True


def test_profile_parser_characterizes_variant_without_mudapins() -> None:
    profile = MudaeTextParser().parse_profile(
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

    assert profile.profile_name == "cute_beagle_91130"
    assert profile.collection_size == 35
    assert (profile.female_percent, profile.male_percent) == (100, 0)
    assert profile.pokedex_count == 2
    assert profile.pokedex_pokemon == ("gulpin", "piloswine")
    assert profile.kakera_reacts == {
        ":kakeraP:": 1,
        ":kakera:": 7,
        ":kakeraT:": 1,
    }
    assert profile.mudapins_collected is None
    assert profile.mudapins_total is None
    assert profile.kakera_balance == 812
    assert (profile.bronze_keys, profile.silver_keys, profile.gold_keys) == (3, None, None)
    assert profile.sphere_stock == 110
    assert profile.spheres == {
        ":spP:": 2,
        ":spB:": 12,
        ":spT:": 7,
        ":spG:": 4,
        ":spY:": 1,
        ":sp:": 1,
        ":spL:": 4,
    }
    assert profile.displayed_badges == (
        ":silvmudae:",
        ":MudaeBirthday7:",
        ":MudaeBirthday8:",
        ":DiamondI:",
    )
    assert profile.pokedex_observed is True
    assert profile.reactions_observed is True
    assert profile.mudapins_observed is False
    assert profile.kakera_balance_observed is True
    assert profile.keys_observed is True
    assert profile.bronze_keys_observed is True
    assert profile.silver_keys_observed is False
    assert profile.gold_keys_observed is False
    assert profile.sphere_stock_observed is True
    assert profile.sphere_counts_observed is True
    assert profile.badges_observed is True


def test_profile_parser_preserves_minimal_response_absence() -> None:
    profile = MudaeTextParser().parse_profile(
        "moa\n"
        "Collection size: 0 (0%:female: 0% :male:)"
    )

    assert profile.profile_name == "moa"
    assert profile.collection_size == 0
    assert profile.female_percent == 0
    assert profile.male_percent == 0
    assert profile.pokedex_count is None
    assert profile.pokedex_pokemon is None
    assert profile.kakera_reacts is None
    assert profile.mudapins_collected is None
    assert profile.mudapins_total is None
    assert profile.kakera_balance is None
    assert (profile.bronze_keys, profile.silver_keys, profile.gold_keys) == (None, None, None)
    assert profile.sphere_stock is None
    assert profile.spheres is None
    assert profile.displayed_badges is None
    assert profile.pokedex_observed is False
    assert profile.reactions_observed is False
    assert profile.mudapins_observed is False
    assert profile.kakera_balance_observed is False
    assert profile.keys_observed is False
    assert profile.bronze_keys_observed is False
    assert profile.silver_keys_observed is False
    assert profile.gold_keys_observed is False
    assert profile.sphere_stock_observed is False
    assert profile.sphere_counts_observed is False
    assert profile.badges_observed is False


def test_profile_parser_requires_collection_section() -> None:
    with pytest.raises(MudaeParseError):
        MudaeTextParser().parse_profile("moa\nReacts:")


def test_profile_parser_requires_well_formed_collection_section() -> None:
    with pytest.raises(MudaeParseError):
        MudaeTextParser().parse_profile(
            "moa\n"
            "Collection size: not-a-number (0%:female: 0% :male:)"
        )


def test_profile_parser_characterizes_identity_line_assumption() -> None:
    profile = MudaeTextParser().parse_profile(
        "not-an-identity-line\n"
        "Collection size: 35 (100%:female: 0% :male:)"
    )

    # Current parser treats lines[0] as the identity without validating its shape.
    assert profile.profile_name == "not-an-identity-line"
    assert profile.collection_size == 35


def test_profile_parser_known_malformed_reaction_shape_fails_closed() -> None:
    with pytest.raises(MudaeParseError):
        MudaeTextParser().parse_profile(
            "moa\n"
            "Collection size: 35 (100%:female: 0% :male:)\n"
            "Reacts:\n"
            "48:kakeraP:"
        )


def test_parse_mudapins_reads_pin_and_logopin_markers() -> None:
    snapshot = MudaeTextParser().parse_mudapins(
        ":pin139::pin182::pin2157::logopin6::logopin141:"
    )

    assert snapshot.pin_markers == (
        ":pin139:",
        ":pin182:",
        ":pin2157:",
        ":logopin6:",
        ":logopin141:",
    )


def test_parse_mudapins_accepts_empty_inventory() -> None:
    snapshot = MudaeTextParser().parse_mudapins(
        "No mudapins found! Collect them with kakeraloots ($kl)"
    )

    assert snapshot.pin_markers == ()


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
    assert state.can_react_kakera_now is True
    assert state.reaction_power_percent == 72
    assert state.kakera_button_power_cost_percent == 36
    assert state.soulmate_button_power_cost_percent == 18


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


def test_parse_timer_state_normalizes_discord_emphasis() -> None:
    state = MudaeTextParser().parse_timer_state(
        "**ernieuuu**, you can claim right now! The next claim reset is in **6** min.\n"
        "You have **17** rolls left. Next rolls reset in **6** min.\n"
        "You have **1** rolls reset in stock.\n"
        "You may vote again in **10h 50 min**.\n"
        "Next $daily reset in **17h 13 min**.\n"
        "$rt is available!\n"
        "**You can react to kakera right now!**\n"
        "**Power: 110%**\n"
        "Each kakera button consumes **36%** of your reaction power.\n"
        "Your characters with 10+ keys consume half the power (**18%**)\n"
        "**Stock: 23,282:kakera:**\n"
        "**(Keys LVL 6+) 5,000:kakera:to collect before the next reset (6 min.)**\n"
        "Probability to complete + reset $bku on your next $sw: **10%**\n"
        "0 $oh left for today, 0 $oc, 0 $oq (**+1 stored**) and 0 $ot.\n"
        "**52 min before the refill.**"
    )

    assert state.can_claim_now is True
    assert state.claim_reset_minutes == 6
    assert state.rolls_left == 17
    assert state.rolls_reset_stock == 1
    assert state.vote_reset_minutes == 650
    assert state.daily_reset_minutes == 1033
    assert state.rt_available is True
    assert state.can_react_kakera_now is True
    assert state.reaction_power_percent == 110
    assert state.kakera_button_power_cost_percent == 36
    assert state.soulmate_button_power_cost_percent == 18
    assert state.kakera_stock == 23282
    assert state.gold_key_stock_remaining == 5000
    assert state.gold_key_reset_minutes == 6
    assert state.bku_reset_probability_percent == 10
    assert state.oq_stored == 1


def test_parse_timer_state_distinguishes_roll_limit_and_vote_reset_prompt() -> None:
    parser = MudaeTextParser()

    limited = parser.parse_timer_state(
        "ernieuuu, the roulette is limited to 17 uses per hour. 50 min left.\n"
        "Upvote Mudae to reset the timer: $vote."
    )
    prompt = parser.parse_timer_state(
        "Upvote Mudae and use this command again to reset your rolls timer for ONE server "
        "(one vote per 12h)."
    )

    assert limited.rolls_reset_status == "limited_timer"
    assert limited.rolls_per_hour_limit == 17
    assert limited.rolls_reset_minutes == 50
    assert prompt.rolls_reset_status == "vote_required"
    assert prompt.rolls_per_hour_limit is None


def test_parse_timer_state_accepts_bold_roll_limit_duration() -> None:
    state = MudaeTextParser().parse_timer_state(
        "cute_beagle_91130, the roulette is limited to 10 uses per hour. **55** min left.\n"
        "Upvote Mudae to reset the timer: $vote. Website: https://mudae.net/"
    )

    assert state.rolls_reset_status == "limited_timer"
    assert state.rolls_reset_minutes == 55


def test_parse_timer_state_accepts_claim_interval_waiting_response() -> None:
    state = MudaeTextParser().parse_timer_state(
        "@ernieuuu, For this server, you can claim once per interval of 3h. "
        "The next interval begins in **53** min."
    )

    assert state.can_claim_now is False
    assert state.claim_reset_minutes == 53


def test_parse_top_page_rejects_unrecognized_text() -> None:
    with pytest.raises(MudaeParseError) as error:
        MudaeTextParser().parse_top_page("not a Mudae message")

    assert str(error.value) == "No ranked characters found in the Mudae $top output."
