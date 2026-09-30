from scripts.check_query_sync import find_mismatches


def test_cwli_files_match_query_definitions():
    mismatches = find_mismatches()
    assert mismatches == [], f"out of sync: {mismatches}"
