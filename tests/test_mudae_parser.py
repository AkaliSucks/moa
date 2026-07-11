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


def test_parse_keyed_harem_page_from_mmy_output() -> None:
    page = MudaeTextParser().parse_harem_key_page(HAREM_KEY_PAGE)

    assert page.page_number == 1
    assert page.page_count == 6
    assert len(page.entries) == 15
    assert page.entries[0].name == "Albedo"
    assert page.entries[0].key_type == "gold"
    assert page.entries[0].key_count == 7
    assert page.entries[-1].name == "Nezuko Kamado"


def test_parse_top_page_rejects_unrecognized_text() -> None:
    with pytest.raises(MudaeParseError, match="No ranked characters"):
        MudaeTextParser().parse_top_page("not a Mudae message")
