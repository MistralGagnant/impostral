"""Open-ended prompts used during the QUESTION phase.

There is no correct answer. Each prompt encourages players to reveal a personal
style, creating tells: a human may sound overly lived-in, while an LLM may sound
too cautious or polished.
"""
from __future__ import annotations

import random

QUESTIONS: list[str] = [
    "What is the last object you held in your hands today?",
    "Describe a smell that reminds you of childhood.",
    "If you had to lie about your job, which one would you choose?",
    "What is the most useless thing you know by heart?",
    "Tell us about a time you were wrong but refused to admit it.",
    "What sound annoys you enough to make you leave a room?",
    "What did you eat for your last meal, and did you enjoy it?",
    "What habit of yours do you find slightly embarrassing?",
    "Describe the weather around you right now.",
    "What advice would you give someone who is afraid of the dark?",
    "What made you laugh most recently?",
    "If silence had a color, what color would it be for you?",
    "What is the first thing you do after waking up?",
    "Describe a texture you can't stand touching.",
    "What is a small thing that instantly ruins your day?",
    "Tell us about a scar or bruise and how you got it.",
    "What song do you secretly enjoy but never admit to?",
    "What is the pettiest reason you ever held a grudge?",
    "Describe the last dream you can remember.",
    "What food combination do you love that others find disgusting?",
    "What is a chore you actually enjoy doing?",
    "Tell us about a stranger who stuck in your memory.",
    "What is the most expensive mistake you have made?",
    "What noise does your home make that only you would notice?",
    "Describe the messiest corner of the place you live.",
    "What is a skill you pretend to have but really don't?",
    "What is the last thing that made you genuinely nervous?",
    "Tell us about a gift you received and secretly hated.",
    "What is a word you always misspell or mispronounce?",
    "What is the last lie you told, however small?",
    "What object would you grab first if you had to leave in a hurry?",
    "What is a superstition you follow even though you know better?",
    "Tell us about the last time you cried, if you can.",
    "What is the worst haircut you ever had?",
    "What is a childhood fear you never fully outgrew?",
    "What is the strangest thing currently in your pockets or bag?",
    "Tell us about a time you got completely lost.",
    "What is a compliment you received that you still remember?",
    "Describe the last text message you sent.",
    "What is something you always forget to buy?",
    "What is a household object you have broken more than once?",
    "Tell us about a place that always makes you sleepy.",
    "What is the most embarrassing thing in your search history you can admit?",
    "What is a phrase you overuse without meaning to?",
    "What is the last thing you procrastinated on?",
    "Tell us about a minor injury that hurt way more than expected.",
    "What is the most useless app on your phone right now?",
    # Social-deduction prompts about the other players.
    "Which player seems most suspicious to you, and why?",
    "Whose answers have felt too polished to be human?",
    "If you had to bet right now, who is an AI, and what gave them away?",
    "Which player would you trust the least, and why?",
    "Whose voice or wording feels slightly off to you?",
    "Point to one player and explain what makes them believable as a human.",
    "Which answer so far sounded the most fake, and whose was it?",
    "Who has been the quietest, and does that make you suspicious?",
    "If one player had to be eliminated right now, who and why?",
    "Which player reminds you most of a machine, and how?",
    # Prompts that judge the quality of a specific player's answer.
    "Who gave the weakest answer this round, and what was off about it?",
    "Whose last answer sounded the most rehearsed, and why?",
    "Which player dodged the question instead of answering it?",
    "Whose answer told you the least about them as a person?",
    "Which reply was so generic that anyone could have written it?",
    "Who overexplained their answer, and why does that stand out?",
    "Whose answer contradicted something they said earlier?",
    "Which player is clearly trying too hard to sound human?",
    "Who gave the most convincing answer, and what sold it?",
]


def pick_question(exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    pool = [q for q in QUESTIONS if q not in exclude] or QUESTIONS
    return random.choice(pool)
