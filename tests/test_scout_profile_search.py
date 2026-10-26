"""LinkedIn profile-search parsing: company extraction and junk-name rejection."""

from harvey.agents.scout import Scout


def _scout():
    # The helpers only read class constants, so skip the heavy constructor.
    return Scout.__new__(Scout)


def test_company_from_dash_title():
    s = _scout()
    snippet = "Alex Rivera - Founder - Northbeam Studio | LinkedIn\nFounder at Northbeam Studio."
    assert s._extract_linkedin_company(snippet) == "Northbeam Studio"


def test_company_from_at_title():
    s = _scout()
    assert s._extract_linkedin_company("Jordan Lee - Lab In-charge at Examplefield University | LinkedIn") == "Examplefield University"


def test_company_from_experience_field():
    s = _scout()
    snippet = "Sam Carter\nExperience: Exampleworks Systems · Education: State Tech · Location: Jaipur"
    assert s._extract_linkedin_company(snippet) == "Exampleworks Systems"


def test_no_company_or_non_company_is_rejected():
    s = _scout()
    assert s._extract_linkedin_company("") == ""
    assert s._extract_linkedin_company("Robin Vale | LinkedIn") == ""
    assert s._extract_linkedin_company("Dana White - Designer - Freelance | LinkedIn") == ""


def test_title_words_are_not_names():
    s = _scout()
    assert s._looks_like_title_words({"first_name": "Startup", "last_name": "Founder"})
    assert s._looks_like_title_words({"first_name": "Senior", "last_name": "Software"})
    assert not s._looks_like_title_words({"first_name": "Asha", "last_name": "Verma"})
