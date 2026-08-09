from g_team_ops.parser import parse_fba_input


def test_parses_all_supported_separators_and_normalizes_case():
    raw = "fba123456，FBA234567、FBA345678 FBA456789\tFBA567890\nFBA678901,FBA789012"
    result = parse_fba_input(raw)
    assert result.valid == [
        "FBA123456",
        "FBA234567",
        "FBA345678",
        "FBA456789",
        "FBA567890",
        "FBA678901",
        "FBA789012",
    ]
    assert result.invalid == []


def test_rejects_invalid_and_stably_deduplicates():
    result = parse_fba_input("FBA123456, rubbish, fba123456, FB, FBA@123, FBA999999")
    assert result.valid == ["FBA123456", "FBA999999"]
    assert result.duplicates == ["FBA123456"]
    assert result.invalid == ["rubbish", "FB", "FBA@123"]
