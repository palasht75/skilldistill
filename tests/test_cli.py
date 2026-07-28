from skilldistill.cli import main


def test_scan_ranks_good_over_threshold(good_session, capsys):
    assert main(["scan", str(good_session.parent), "--min-score", "0.4"]) == 0
    out = capsys.readouterr().out
    assert "good.jsonl" in out and "goal:" in out


def test_distill_offline_end_to_end(good_session, tmp_path, capsys):
    rc = main(
        ["distill", str(good_session), "--offline", "--skills-dir", str(tmp_path / "skills")]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "SKILL.md" in out and "offline" in out


def test_scan_missing_dir_exit_2(tmp_path):
    assert main(["scan", str(tmp_path / "nope")]) == 2
