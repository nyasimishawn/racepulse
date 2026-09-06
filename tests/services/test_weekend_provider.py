import pandas as pd
import pytest

from app.providers.fastf1_provider import FastF1Provider


def test_schedule_resolves_location_and_includes_sprint_sessions(monkeypatch):
    monkeypatch.setattr(FastF1Provider, "__init__", lambda self: None)
    schedule = pd.DataFrame(
        [
            {
                "RoundNumber": 1,
                "EventName": "Test Grand Prix",
                "Location": "Monza",
                "Country": "Italy",
                "Session1": "Practice 1",
                "Session2": "Qualifying",
                "Session3": "Sprint Shootout",
                "Session4": "Sprint",
                "Session5": "Race",
                "Session1DateUtc": pd.Timestamp("2023-09-01T10:00:00"),
            }
        ]
    )
    monkeypatch.setattr(
        "fastf1.get_event_schedule", lambda *args, **kwargs: schedule
    )
    provider = FastF1Provider()
    weekend = provider.get_weekend_schedule(year=2023, event_name="Monza")
    assert [s.identifier for s in weekend.sessions] == [
        "FP1",
        "Q",
        "SS",
        "S",
        "R",
    ]
    assert weekend.sessions[0].scheduled_at.utcoffset().total_seconds() == 0
    with pytest.raises(ValueError):
        provider.get_weekend_schedule(
            year=2023, event_name="nonexistent circuit"
        )


def test_full_session_load_enables_all_provider_channels(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(FastF1Provider, "__init__", lambda self: None)
    calls = []
    session = SimpleNamespace(load=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("fastf1.get_session", lambda *args: session)
    loaded = FastF1Provider().load_full_session(
        year=2023, round_number=14, session_identifier="FP1"
    )
    assert loaded is session
    assert calls == [
        {"laps": True, "telemetry": True, "weather": True, "messages": True}
    ]


def test_ambiguous_country_is_not_cached_as_an_event_alias(monkeypatch):
    monkeypatch.setattr(FastF1Provider, "__init__", lambda self: None)
    schedule = pd.DataFrame(
        [
            {
                "RoundNumber": 1,
                "EventName": "Italian Grand Prix",
                "Location": "Monza",
                "Country": "Italy",
                "Session1": "Race",
            },
            {
                "RoundNumber": 2,
                "EventName": "Emilia Romagna Grand Prix",
                "Location": "Imola",
                "Country": "Italy",
                "Session1": "Race",
            },
        ]
    )
    monkeypatch.setattr(
        "fastf1.get_event_schedule", lambda *args, **kwargs: schedule
    )
    provider = FastF1Provider()
    assert (
        "italy"
        not in provider.get_weekend_schedule(
            year=2023, event_name="Monza"
        ).aliases
    )
    with pytest.raises(ValueError):
        provider.get_weekend_schedule(year=2023, event_name="Italy")
