import pytest

from ptjobs.normalize import (
    fingerprint,
    normalize_city,
    normalize_company,
    normalize_tags,
    normalize_title,
    normalize_work_mode,
    parse_salary,
)


class TestNormalizeCity:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Lisboa", "lisboa"),
            ("LISBOA", "lisboa"),
            ("Lisbon", "lisboa"),
            ("Oporto", "porto"),
            ("Porto, Portugal", "porto"),
            ("Braga ", "braga"),
            ("Remote - Portugal", None),
            ("", None),
            (None, None),
        ],
    )
    def test_cities(self, raw: str | None, expected: str | None) -> None:
        assert normalize_city(raw) == expected


class TestNormalizeWorkMode:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Remoto", "remote"),
            ("100% remote", "remote"),
            ("Teletrabalho", "remote"),
            ("Híbrido", "hybrid"),
            ("Hybrid (2 days office)", "hybrid"),
            ("Presencial", "onsite"),
            ("On-site", "onsite"),
            ("", "unknown"),
            (None, "unknown"),
        ],
    )
    def test_work_modes(self, raw: str | None, expected: str) -> None:
        assert normalize_work_mode(raw) == expected


class TestParseSalary:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("30k-40k", (30000, 40000)),
            ("35-45k", (35000, 45000)),
            ("30.000 - 40.000 EUR", (30000, 40000)),
            ("40 000 EUR", (40000, 40000)),
            ("€2.500/mês", (30000, 30000)),
            ("1 500 €/mês", (18000, 18000)),
            ("mensal 2000€", (24000, 24000)),
            ("2500 EUR per month", (30000, 30000)),
            ("1500€ por ano", (1500, 1500)),
            ("8k/year", (8000, 8000)),
            ("2.5k", (2500, 2500)),
            ("50000", (50000, 50000)),
            ("2500", (30000, 30000)),
            ("15€/hora", (None, None)),
            ("$50,000", (None, None)),
            ("Competitive", (None, None)),
            ("", (None, None)),
            (None, (None, None)),
        ],
    )
    def test_salaries(
        self, raw: str | None, expected: tuple[int | None, int | None]
    ) -> None:
        assert parse_salary(raw) == expected


class TestNormalizeTags:
    def test_extracts_known_stack(self) -> None:
        text = "We need a Python developer with React and PostgreSQL experience"
        assert set(normalize_tags(text)) == {"python", "react", "postgresql"}

    def test_trailing_punctuation(self) -> None:
        assert normalize_tags("Experience with Python.") == ["python"]
        assert normalize_tags("We use Java, React.") == ["java", "react"]

    def test_dotnet_and_csharp(self) -> None:
        assert "dotnet" in normalize_tags("Strong ASP.NET background")
        assert "csharp" in normalize_tags("C# and .NET developer")

    def test_deduplicates_and_sorts(self) -> None:
        assert normalize_tags("Python python PYTHON") == ["python"]

    def test_aliases(self) -> None:
        assert "javascript" in normalize_tags("Strong JS and Node.js skills")
        assert "golang" in normalize_tags("Experience with Go developer role")

    def test_no_false_positive_go(self) -> None:
        assert "golang" not in normalize_tags("Go-to-market strategy lead")

    def test_empty(self) -> None:
        assert normalize_tags("") == []
        assert normalize_tags(None) == []


class TestNormalizeTitleCompany:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Senior Python Developer (m/f)", "senior python developer"),
            ("Backend Engineer  (M/F/D)", "backend engineer"),
            ("Data Scientist h/m", "data scientist"),
            ("DevOps Engineer (m/w/d)", "devops engineer"),
        ],
    )
    def test_title_strips_gender_markers(self, raw: str, expected: str) -> None:
        assert normalize_title(raw) == expected

    def test_title_keeps_slashed_role(self) -> None:
        assert normalize_title("Back-end/Full-stack Developer") == (
            "back-end/full-stack developer"
        )

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Critical Software, S.A.", "critical software"),
            ("Feedzai Lda", "feedzai"),
            ("OutSystems Unipessoal Lda.", "outsystems"),
            ("Talkdesk", "talkdesk"),
        ],
    )
    def test_company_strips_legal_suffixes(self, raw: str, expected: str) -> None:
        assert normalize_company(raw) == expected


class TestFingerprint:
    def test_stable_across_sources(self) -> None:
        a = fingerprint("Senior Python Developer (m/f)", "Feedzai Lda", "Lisboa")
        b = fingerprint("senior python developer", "Feedzai, S.A.", "Lisbon")
        assert a == b

    def test_differs_on_different_role(self) -> None:
        a = fingerprint("Python Developer", "Feedzai", "Lisboa")
        b = fingerprint("Java Developer", "Feedzai", "Lisboa")
        assert a != b

    def test_separator_is_escaped(self) -> None:
        a = fingerprint("z", "x|y", None)
        b = fingerprint("y|z", "x", None)
        assert a != b

    def test_is_hex_sha1(self) -> None:
        fp = fingerprint("Python Developer", "Feedzai", "Lisboa")
        assert len(fp) == 40
        int(fp, 16)
