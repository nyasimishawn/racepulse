"""Frozen, sourced starter bank used by migration 20260930_trivia.

Future bank revisions belong in new migrations. Never change this seed file.
Questions use historical seasons or explicit tyre eras to avoid ambiguity.
"""

from uuid import NAMESPACE_URL, uuid5

FLAGS = "https://www.formula1.com/en/latest/article/the-beginners-guide-to-formula-1-flags.T5DqOqbWI6S4Va8Y5yMld"
TYRES = "https://www.formula1.com/en/latest/article/the-beginners-guide-to-f1-tyres.61SvF0Kfg29UR2SPhakDqd"
SILVERSTONE = "https://www.formula1.com/en/information/great-britain-silverstone-circuit.2DtFVI1FjkYgLVdGhbAIv0"
GLOSSARY = "https://www.formula1.com/en/latest/article/f1-glossary-p-t.GBhLQtpeu9b8LrK5VM0F1"


def standings(year):
    return f"https://www.formula1.com/en/results/{year}/drivers"


# Correct answer is the first item here; service shuffles all options per race.
ROWS = [
    (
        "easy",
        "In F1's tyre colour system introduced in 2019, what colour marks the soft dry tyre?",
        ["Red", "Yellow", "White", "Blue"],
        "The soft dry compound is marked red.",
        TYRES,
    ),
    (
        "easy",
        "In F1's tyre colour system introduced in 2019, what colour marks the medium dry tyre?",
        ["Yellow", "Red", "White", "Green"],
        "The medium dry compound is marked yellow.",
        TYRES,
    ),
    (
        "easy",
        "In F1's tyre colour system introduced in 2019, what colour marks the hard dry tyre?",
        ["White", "Red", "Yellow", "Blue"],
        "The hard dry compound is marked white.",
        TYRES,
    ),
    (
        "easy",
        "Which flag signals the end of an F1 race?",
        ["Chequered", "Yellow", "Blue", "Green"],
        "The black and white chequered flag ends a session or race.",
        FLAGS,
    ),
    (
        "easy",
        "What does a yellow flag warn an F1 driver about?",
        [
            "A hazard ahead",
            "The race has ended",
            "A mandatory pit stop",
            "A race win",
        ],
        "Yellow flags warn of danger; drivers must slow down and must not overtake.",
        FLAGS,
    ),
    (
        "easy",
        "Which flag is used to suspend an F1 session?",
        ["Red", "Green", "Blue", "White"],
        "A red flag suspends the session because conditions require it.",
        FLAGS,
    ),
    (
        "easy",
        "What does a green flag indicate?",
        [
            "The track is clear",
            "A car is disqualified",
            "The race has ended",
            "A slippery surface",
        ],
        "Green means the track ahead is clear after a hazard.",
        FLAGS,
    ),
    (
        "easy",
        "During an F1 race, which flag tells a driver they are about to be lapped?",
        ["Blue", "Red", "Green", "Chequered"],
        "Blue flags alert a driver to a faster car coming through to lap them.",
        FLAGS,
    ),
    (
        "easy",
        "What is pole position?",
        [
            "First place on the starting grid",
            "Last place on the grid",
            "The fastest pit stop",
            "The final corner",
        ],
        "Pole position is the first starting position on the grid.",
        GLOSSARY,
    ),
    (
        "easy",
        "Where do F1 teams normally change a car's tyres during a race?",
        [
            "The pit lane",
            "The podium",
            "The grandstand",
            "The starting straight",
        ],
        "Teams service the car at their pit box in the pit lane.",
        GLOSSARY,
    ),
    (
        "medium",
        "In which year did the F1 World Championship begin?",
        ["1950", "1946", "1960", "1970"],
        "The inaugural championship started in 1950.",
        SILVERSTONE,
    ),
    (
        "medium",
        "Which circuit hosted the first F1 World Championship race?",
        ["Silverstone", "Monza", "Monaco", "Spa-Francorchamps"],
        "Silverstone hosted the opening championship round on 13 May 1950.",
        SILVERSTONE,
    ),
    (
        "medium",
        "Who won the 2009 F1 Drivers' Championship?",
        ["Jenson Button", "Sebastian Vettel", "Lewis Hamilton", "Mark Webber"],
        "Jenson Button won the 2009 title with Brawn Mercedes.",
        standings(2009),
    ),
    (
        "medium",
        "Which team did Jenson Button drive for when he won the 2009 title?",
        ["Brawn Mercedes", "McLaren Mercedes", "Ferrari", "RBR Renault"],
        "Button's championship season was with Brawn Mercedes.",
        standings(2009),
    ),
    (
        "medium",
        "Who won the 2007 F1 Drivers' Championship?",
        [
            "Kimi Raikkonen",
            "Lewis Hamilton",
            "Fernando Alonso",
            "Felipe Massa",
        ],
        "Kimi Raikkonen finished first in the 2007 standings.",
        standings(2007),
    ),
    (
        "medium",
        "Which team did Lewis Hamilton drive for when he won the 2008 title?",
        ["McLaren Mercedes", "Mercedes", "Ferrari", "Renault"],
        "Hamilton won his first title with McLaren Mercedes in 2008.",
        standings(2008),
    ),
    (
        "medium",
        "Who finished second in the 2009 Drivers' Championship?",
        [
            "Sebastian Vettel",
            "Rubens Barrichello",
            "Mark Webber",
            "Lewis Hamilton",
        ],
        "Sebastian Vettel was runner-up with 84 points.",
        standings(2009),
    ),
    (
        "medium",
        "Who won the 2006 F1 Drivers' Championship?",
        [
            "Fernando Alonso",
            "Michael Schumacher",
            "Felipe Massa",
            "Kimi Raikkonen",
        ],
        "Fernando Alonso won the 2006 title for Renault.",
        standings(2006),
    ),
    (
        "medium",
        "How many points separated Raikkonen and Hamilton in the 2007 final standings?",
        ["1", "2", "5", "10"],
        "Raikkonen scored 110 points; Hamilton scored 109.",
        standings(2007),
    ),
    (
        "medium",
        "Which team did Kimi Raikkonen represent when he won the 2007 title?",
        ["Ferrari", "McLaren Mercedes", "Renault", "BMW Sauber"],
        "Raikkonen's 2007 championship was with Ferrari.",
        standings(2007),
    ),
    (
        "hard",
        "In which year did Lewis Hamilton win his 34th F1 Grand Prix?",
        ["2015", "2014", "2016", "2013"],
        "His 34th victory came at the 2015 Australian Grand Prix.",
        "https://www.autosport.com/f1/news/australian-gp-lewis-hamilton-beats-nico-rosberg-to-win-5009727/5009727/",
    ),
    (
        "hard",
        "Who won the 1982 F1 Drivers' Championship?",
        ["Keke Rosberg", "Alain Prost", "Didier Pironi", "Niki Lauda"],
        "Keke Rosberg won the 1982 championship for Williams Ford.",
        standings(1982),
    ),
    (
        "hard",
        "Who won the 1976 F1 Drivers' Championship?",
        ["James Hunt", "Niki Lauda", "Jody Scheckter", "Clay Regazzoni"],
        "James Hunt topped the 1976 standings with 69 points.",
        standings(1976),
    ),
    (
        "hard",
        "Who was runner-up in the 1994 Drivers' Championship?",
        ["Damon Hill", "Ayrton Senna", "Gerhard Berger", "Mika Hakkinen"],
        "Damon Hill finished one point behind Michael Schumacher.",
        standings(1994),
    ),
    (
        "hard",
        "Who finished third in the 2006 Drivers' Championship?",
        [
            "Felipe Massa",
            "Michael Schumacher",
            "Giancarlo Fisichella",
            "Kimi Raikkonen",
        ],
        "Felipe Massa finished third with 80 points.",
        standings(2006),
    ),
    (
        "hard",
        "Who was classified third in the 2007 Drivers' Championship?",
        ["Fernando Alonso", "Lewis Hamilton", "Felipe Massa", "Nick Heidfeld"],
        "Alonso was third, tied with Hamilton on 109 points.",
        standings(2007),
    ),
    (
        "hard",
        "Who finished fourth in the 2009 Drivers' Championship?",
        [
            "Mark Webber",
            "Lewis Hamilton",
            "Rubens Barrichello",
            "Nico Rosberg",
        ],
        "Mark Webber was fourth with 69.5 points.",
        standings(2009),
    ),
    (
        "hard",
        "How many championship points did Jenson Button score in 2009?",
        ["95", "84", "110", "134"],
        "Button won the 2009 title with 95 points under that season's scoring rules.",
        standings(2009),
    ),
    (
        "hard",
        "How many championship points did Kimi Raikkonen score in 2007?",
        ["110", "109", "112", "121"],
        "Raikkonen's 110 points beat Hamilton and Alonso by one point.",
        standings(2007),
    ),
    (
        "hard",
        "How many championship points did Fernando Alonso score in 2006?",
        ["134", "121", "133", "148"],
        "Alonso won the 2006 championship with 134 points.",
        standings(2006),
    ),
]


def seed_rows():
    for index, (difficulty, prompt, labels, explanation, source) in enumerate(
        ROWS
    ):
        yield {
            "id": uuid5(NAMESPACE_URL, f"racepulse:trivia:v1:{index}"),
            "difficulty": difficulty,
            "version": 1,
            "published": True,
            "content": {
                "prompt": prompt,
                "options": [
                    {"id": str(i), "label": label}
                    for i, label in enumerate(labels)
                ],
                "correct_option_id": "0",
                "explanation": explanation,
                "source_url": source,
            },
        }
