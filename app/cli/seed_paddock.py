"""Publish a small, reviewed starter desk. Existing articles are never overwritten.

Sources checked 2026-09-08. Summaries/explainers are original RacePulse text;
source articles and photographs are not copied into the app.
"""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.db.database import SessionLocal
from app.models.calendar import CalendarWeekend
from app.models.curated_content import EditorialUpdate
from app.schemas.editorial import EditorialUpdateCreateRequest

TYRES = "https://press.pirelli.com/tyre-compounds-selected-for-zandvoort-monza-and-madrid/"
SAINZ = "https://www.formula1.com/en/latest/article/sainz-agrees-new-multi-year-contract-with-williams-as-team-complete-2027-driver-line-up.66fBQTFaXXt7oIUfzi8NiO"
COLAPINTO = "https://www.formula1.com/en/latest/article/alpine-announce-colapinto-contract-extension-as-team-confirms-unchanged-2027-line-up.DL3dVyZLJm5cHryWcHyPq"
APRIL = "https://www.formula1.com/en/latest/article/bahrain-and-saudi-arabian-grands-prix-will-not-capture-place-in-april.1hnqllVG85RSt8pbFc5Ivx"


def seed(db):
    madrid = db.scalar(
        select(CalendarWeekend).where(
            CalendarWeekend.sync_key
            == "https://www.formula1.com/en/racing/2026/spain"
        )
    )
    stories = [
        dict(
            key=APRIL,
            update_type="FIA_UPDATE",
            title="Explained: the withdrawn April race dates",
            body="On 14 March, Formula 1 announced that the Bahrain and Saudi Arabian races would not take place in April because of the regional situation. No replacement races were planned for that month. This article explains the April decision; consult the current calendar for later scheduling changes.",
            source_url=APRIL,
            publisher="Formula 1",
            published_at="2026-03-14T22:00:00Z",
            context=dict(
                category="WEEKEND",
                evidence="OFFICIAL",
                summary="The original April weekends were withdrawn, leaving a gap in that month's schedule.",
                why_it_matters="An old announcement describes what changed at that time. The current calendar should drive your viewing plans and Fantasy deadlines. A missing race listing alone is not proof of a cancellation.",
            ),
        ),
        dict(
            key=SAINZ,
            update_type="TEAM_UPDATE",
            title="Sainz stays with Williams for 2027",
            body="Carlos Sainz has signed another multi-year Williams agreement. With Alex Albon also staying, the team has announced both drivers for 2027. The report does not specify the final year of Sainz's new contract.",
            source_url=SAINZ,
            publisher="Formula 1",
            published_at="2026-08-19T13:02:00Z",
            context=dict(
                category="DRIVER_MARKET",
                evidence="OFFICIAL",
                summary="Williams has confirmed Sainz and Albon for next season.",
                why_it_matters="Two seats are settled. Keeping the same pairing gives the team continuity while it develops its car; it does not guarantee better results.",
                driver_market=dict(
                    driver_name="Carlos Sainz",
                    team_name="Williams",
                    season=2027,
                    status="CONFIRMED",
                ),
            ),
        ),
        dict(
            key=COLAPINTO,
            update_type="TEAM_UPDATE",
            title="Alpine keeps Colapinto alongside Gasly",
            body="Alpine has announced that Franco Colapinto will remain Pierre Gasly's team-mate in 2027. This is a confirmed contract extension, rather than a prediction about an available seat.",
            source_url=COLAPINTO,
            publisher="Formula 1",
            published_at="2026-08-27T11:02:00Z",
            context=dict(
                category="DRIVER_MARKET",
                evidence="OFFICIAL",
                summary="Alpine will retain its current driver pairing for 2027.",
                why_it_matters="A contract announcement settles who drives for a team next season. Rumours elsewhere should remain labelled as reports until the relevant team or driver confirms them.",
                driver_market=dict(
                    driver_name="Franco Colapinto",
                    team_name="Alpine",
                    season=2027,
                    status="CONFIRMED",
                ),
            ),
        ),
    ]
    if madrid:
        stories.append(
            dict(
                key=TYRES + "#madrid-2026",
                update_type="PIRELLI_UPDATE",
                title="Madrid tyre selection: C2, C3 and C4",
                body="Pirelli nominated its C2, C3 and C4 compounds for Madrid. For this weekend they take the Hard, Medium and Soft roles respectively. Pirelli based the selection for the new circuit on simulations and expected tyre loads, with heat management among its considerations.",
                source_url=TYRES,
                publisher="Pirelli",
                published_at="2026-07-28T09:54:00Z",
                context=dict(
                    category="TYRES",
                    evidence="OFFICIAL",
                    calendar_weekend_id=madrid.id,
                    summary="Madrid uses C2 as Hard, C3 as Medium and C4 as Soft.",
                    why_it_matters="Compound numbers describe the range; Hard, Medium and Soft describe the three roles at this event. Softer tyres usually offer more grip but can wear faster. The nomination alone cannot tell us which strategy will win.",
                    tyres=dict(hard="C2", medium="C3", soft="C4"),
                ),
            )
        )
    created = 0
    for item in stories:
        key = item.pop("key")
        identifier = uuid5(NAMESPACE_URL, key)
        if db.get(EditorialUpdate, identifier):
            continue
        payload = EditorialUpdateCreateRequest(
            **item, publication_status="PUBLISHED", confidence="HIGH"
        )
        values = payload.model_dump(mode="json")
        values["published_at"] = payload.published_at
        db.add(EditorialUpdate(id=identifier, **values))
        created += 1
    db.commit()
    return {"created": created, "madrid_tyres_linked": madrid is not None}


if __name__ == "__main__":
    with SessionLocal() as session:
        print(seed(session))
