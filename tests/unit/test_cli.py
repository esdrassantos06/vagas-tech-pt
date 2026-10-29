import ptjobs.__main__ as cli


def test_run_dispatches_scrape_then_notify(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        cli, "run_scrape", lambda sources=None: calls.append(f"scrape:{sources}")
    )
    monkeypatch.setattr(cli, "run_notify", lambda: calls.append("notify"))
    assert cli.main(["run"]) == 0
    assert calls == ["scrape:None", "notify"]


def test_scrape_passes_source(monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(
        cli, "run_scrape", lambda sources=None: captured.append(sources)
    )
    assert cli.main(["scrape", "--source", "itjobs"]) == 0
    assert captured == [["itjobs"]]


def test_notify_dispatches(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(cli, "run_notify", lambda: called.append("notify"))
    assert cli.main(["notify"]) == 0
    assert called == ["notify"]


def test_openapi_prints_schema(capsys) -> None:
    assert cli.main(["openapi"]) == 0
    out = capsys.readouterr().out
    assert '"openapi"' in out
    assert "/jobs" in out


def test_unknown_source_rejected(monkeypatch) -> None:
    monkeypatch.setattr(cli, "run_scrape", lambda sources=None: None)
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["scrape", "--source", "bogus"])


def test_scrape_commits_each_source_separately(monkeypatch) -> None:
    from contextlib import contextmanager

    scopes: list[int] = []

    @contextmanager
    def fake_scope():
        scopes.append(1)
        yield object()

    monkeypatch.setattr(cli, "session_scope", fake_scope)
    monkeypatch.setattr(
        cli, "run_source", lambda name, fetcher, *, session, alert, describe: None
    )
    monkeypatch.setattr(
        cli, "_scrapers", lambda client: {"itjobs": None, "net_empregos": None}
    )
    cli.run_scrape(["itjobs", "net_empregos"])
    assert len(scopes) == 2


def test_scrape_describes_itjobs_and_linkedin_jobs(monkeypatch) -> None:
    from contextlib import contextmanager

    @contextmanager
    def fake_scope():
        yield object()

    describers: dict[str, object] = {}

    def fake_run_source(name, fetcher, *, session, alert, describe):
        describers[name] = describe

    monkeypatch.setattr(cli, "session_scope", fake_scope)
    monkeypatch.setattr(cli, "run_source", fake_run_source)
    monkeypatch.setattr(
        cli,
        "_scrapers",
        lambda client: {"itjobs": None, "linkedin": None, "net_empregos": None},
    )
    cli.run_scrape()
    assert callable(describers["itjobs"])
    assert callable(describers["linkedin"])
    assert describers["net_empregos"] is None
