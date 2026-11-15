"""Tests use a fixed set of example form/draft IDs (the old 11/25 run) seeded through web.KNOWN_STATE.
Production keeps KNOWN_STATE empty so a reset event starts fresh."""
import os

# Tests never read the officer's private settings: fixed fake account and fake LINE groups.
os.environ["AUTOPILOT_FORMS_ACCOUNT"] = "officer@example.com"
os.environ["AUTOPILOT_LOCAL_CONFIG"] = os.path.join(os.path.dirname(__file__), "local_settings_fake.yaml")

import pytest

LEGACY_STATE = {
    "2026-11-25-candice": {
        "signup": {
            "formId": "FAKESIGNUPFORMID-0000000000000000000000000",
            "edit_url": "https://docs.google.com/forms/d/FAKESIGNUPFORMID-0000000000000000000000000/edit",
            "responder_url": "https://docs.google.com/forms/d/e/1FAIpQLSFAKEFORMID-signup-0000000000000000000000000000/viewform",
        },
        "feedback": {
            "formId": "FAKEFEEDBACKFORMID-000000000000000000000000",
            "edit_url": "https://docs.google.com/forms/d/FAKEFEEDBACKFORMID-000000000000000000000000/edit",
            "responder_url": "https://docs.google.com/forms/d/e/1FAIpQLSFAKEFORMID-feedback-00000000000000000000000000/viewform",
            "prefill_url": "https://docs.google.com/forms/d/e/1FAIpQLSFAKEFORMID-feedback-00000000000000000000000000/viewform?usp=pp_url&entry.1000000001=",
        },
        "drafts": [
            {"file": "2_預告信.html", "label": "預告信", "draft_id": "r1000000000000000001"},
            {"file": "4_提醒信.html", "label": "當天提醒信", "draft_id": "r-1000000000000000002"},
            {"file": "6_課後信.html", "label": "課後信", "draft_id": "r-1000000000000000003"},
        ],
    }
}


@pytest.fixture(autouse=True)
def _legacy_known_state(monkeypatch, request):
    if request.node.get_closest_marker("fresh"):
        return
    monkeypatch.setattr("autopilot.web.KNOWN_STATE", LEGACY_STATE)


def pytest_configure(config):
    config.addinivalue_line("markers", "fresh: run with an empty KNOWN_STATE (as in production)")


@pytest.fixture(autouse=True)
def _fast_canva_timeout(monkeypatch):
    # The Canva step waits for the connector; in tests an un-stubbed wait must fail fast, not hang.
    monkeypatch.setenv("CLUB_CANVA_TIMEOUT_S", "1")
    # Never read a real private Notion export during tests unless a test points at one.
    monkeypatch.setenv("CLUB_NOTION_CACHE", "/nonexistent-notion-cache")
