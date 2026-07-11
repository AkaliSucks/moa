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


ROLL = """Airi Jinguuji
Mob kara Hajimaru Tansaku Eiyuutan
Claims: #55,003
27:kakera:
Image
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


def test_parse_roll_from_copied_mudae_output() -> None:
    roll = MudaeTextParser().parse_roll(ROLL)

    assert roll.name == "Airi Jinguuji"
    assert roll.series == "Mob kara Hajimaru Tansaku Eiyuutan"
    assert roll.claim_rank == 55003
    assert roll.kakera_value == 27


def test_parse_top_page_rejects_unrecognized_text() -> None:
    with pytest.raises(MudaeParseError, match="No ranked characters"):
        MudaeTextParser().parse_top_page("not a Mudae message")
