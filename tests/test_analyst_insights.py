"""The analyst insight for queued-but-never-sent prospects.

With the email channel disabled, prospects staying queued is the configured
hold — exactly what "sending off" looks like. The insight used to cry
"BLOCKED: check the Instantly API key" anyway (seen 2026-10-31 with
channels.email.enabled=false), sending someone after an API failure that
never existed. It must name the disabled channel instead.
"""

from harvey.agents.analyst import Analyst
from harvey.config import HarveyConfig

BASE = {
    "persona": {"name": "H", "company": "A", "role": "B", "email": "e",
                "linkedin": "", "tone": "t"},
    "product": {"name": "P", "description": "D", "pricing": "$1",
                "key_benefits": [], "objection_responses": {}},
    "icp": {"industries": [], "company_size": "", "titles": [], "geography": []},
}


def _insights(email_enabled: bool) -> list[str]:
    config = HarveyConfig(**BASE, channels={"email": {"enabled": email_enabled}})
    analyst = Analyst(state=None, config=config)  # state unused by _generate_insights
    report = {
        "pipeline": {"new": 0, "queued": 51, "contacted": 0, "replied": 0,
                     "opted_out": 0},
        "intents": {},
        "campaigns": [],
        "stages": {},
    }
    return analyst._generate_insights(report)


def _queued_insight(insights: list[str]) -> str:
    [insight] = [i for i in insights if "51 prospects" in i]
    return insight


def test_channel_disabled_says_holding_not_blocked():
    insight = _queued_insight(_insights(email_enabled=False))
    assert insight.startswith("HOLD:")
    assert "channels.email.enabled" in insight
    assert "not a failure" in insight
    assert "Instantly" not in insight
    assert "BLOCKED" not in insight


def test_channel_enabled_still_flags_a_real_blockage():
    insight = _queued_insight(_insights(email_enabled=True))
    assert insight.startswith("BLOCKED:")
    assert "Instantly API key" in insight
