from skilldistill.dedup import find_similar
from skilldistill.distill import distill
from skilldistill.emitter import SkillExistsError, write_skill
from skilldistill.transcripts import parse_session


def test_offline_distill_produces_valid_skill(good_session):
    draft = distill(parse_session(good_session), llm=None)
    assert draft.origin == "offline"
    assert draft.content.startswith("---")
    assert "name:" in draft.content and "description:" in draft.content
    assert "Read" in draft.content and "Bash" in draft.content


def test_llm_distill_uses_callable(good_session):
    calls = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "---\nname: retry-fixer\ndescription: Use this skill when fixing retries.\n---\n\n# Steps\n1. Do it."

    draft = distill(parse_session(good_session), llm=fake_llm)
    assert draft.origin == "llm"
    assert draft.name == "retry-fixer"
    assert "payment client" in calls[0]  # goal made it into the prompt


def test_emit_and_dedup(tmp_path, good_session):
    draft = distill(parse_session(good_session), llm=None)
    target = write_skill(draft, tmp_path / "skills")
    assert target.exists() and target.name == "SKILL.md"
    # same name again -> exists error, and dedup flags it
    try:
        write_skill(draft, tmp_path / "skills")
        raise AssertionError("expected SkillExistsError")
    except SkillExistsError:
        pass
    sims = find_similar(draft, tmp_path / "skills")
    assert sims and sims[0].similarity >= 0.55
