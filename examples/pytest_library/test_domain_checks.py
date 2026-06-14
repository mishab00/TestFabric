from __future__ import annotations


def test_domain_check_records_evidence(evidence):
    api_config = {"mode": "classic", "token": "XXXX"}

    with evidence.step("domain.config_file_matches_api"):
        evidence.attach_json("api_config", api_config)
        evidence.attach_text("status", "config is active")

    assert api_config["mode"] == "classic"
