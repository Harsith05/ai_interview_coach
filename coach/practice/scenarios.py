"""Speaking-practice scenarios and levels."""
import random

SCENARIOS = {
    "casual": {
        "title": "Casual chat",
        "description": "Talk about your day, hobbies, food, movies and plans.",
        "role": "a friendly new friend having a relaxed chat",
        "openers": ["Hi! How's your day going so far?",
                    "Hey there! What have you been up to today?",
                    "Hello! Did you do anything fun last weekend?"],
    },
    "introduce": {
        "title": "Introduce yourself",
        "description": "Talk about yourself, your studies, work and goals.",
        "role": "a friendly person meeting the learner for the first time at a networking event",
        "openers": ["Hello, nice to meet you! Could you tell me a little about yourself?",
                    "Hi! I don't think we've met. What's your name, and what do you do?"],
    },
    "hr_round": {
        "title": "HR interview round",
        "description": "Common HR questions: strengths, weaknesses, why this job.",
        "role": "a polite HR interviewer at a technology company, asking one HR question at a time",
        "openers": ["Good morning, thanks for coming in today. To start, could you walk me through your background?",
                    "Hello, welcome! Let's begin with an easy one: why are you interested in this role?"],
    },
    "restaurant": {
        "title": "Ordering at a restaurant",
        "description": "You're a customer; the AI is the waiter.",
        "role": "a friendly waiter at a casual restaurant; the learner is the customer",
        "openers": ["Good evening, welcome! Is it a table for one? Here's the menu. Can I get you something to drink first?"],
    },
    "travel": {
        "title": "Travel & airport",
        "description": "Check in, ask for directions, talk about trips.",
        "role": "an airline check-in agent at the airport; the learner is a traveller",
        "openers": ["Good morning! Where are you flying to today? May I see your passport, please?"],
    },
    "workplace": {
        "title": "Team meeting",
        "description": "Give a project update, discuss problems and deadlines.",
        "role": "a team lead in a weekly stand-up meeting asking the learner for a project update",
        "openers": ["Morning! Let's start the stand-up. What did you work on this week, and is anything blocking you?"],
    },
    "opinion": {
        "title": "Share your opinion",
        "description": "Discuss a topic and give reasons for your views.",
        "role": "a curious discussion partner who asks for the learner's opinion and reasons, "
                "and politely offers other viewpoints",
        "openers": ["Here's a topic for us: do you think AI will create more jobs than it takes away? What's your view?",
                    "Let's discuss something: is it better to work from home or from an office? What do you think?",
                    "Quick question to start: should students learn coding in school? Why or why not?"],
    },
}

LEVELS = {
    "beginner": {"title": "Beginner (A2)", "speech_rate": 0.85,
                 "style": "Use very simple, common words and short sentences (max ~12 words). Speak slowly and clearly."},
    "intermediate": {"title": "Intermediate (B1–B2)", "speech_rate": 0.95,
                     "style": "Use everyday vocabulary and natural sentences of moderate length."},
    "advanced": {"title": "Advanced (C1)", "speech_rate": 1.0,
                 "style": "Speak naturally like a native speaker, including some idioms and richer vocabulary."},
}

MAX_TURNS = 10  # learner turns per session (they can end earlier)


def opener(scenario: str) -> str:
    return random.choice(SCENARIOS[scenario]["openers"])
