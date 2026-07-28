from skilldistill.detect import score_session
from skilldistill.transcripts import parse_session


def test_good_session_outscores_bad(good_session, bad_session):
    good = score_session(parse_session(good_session))
    bad = score_session(parse_session(bad_session))
    assert good.score > bad.score
    assert good.score >= 0.5
    assert any("success signals" in r for r in good.reasons)
