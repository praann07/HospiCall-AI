"""Fixed safety copy and the output guard for LLM text.

The LLM never acts and never has the last word on anything clinical: rules decide emergencies and
refusals, and any LLM-written sentence must pass ``guard_reply`` before a caller hears it.
"""
import re

NO_ADVICE = "I can't give medical advice, but a doctor can. "
MEDICAL_REFUSAL = NO_ADVICE + "Would you like me to book an appointment?"

CHEST_PAIN_CAUTION = ("If the chest pain is severe or happening right now, please call {number} first. ")


def emergency_reply(number: str) -> str:
    # Honest by design: this is software, it cannot dispatch anyone or alert staff.
    return (f"This sounds like an emergency. Please call {number} right now or go to the nearest "
            f"emergency room. I'm an automated assistant and can't send help myself.")


def self_harm_reply(emergency_number: str, crisis_number: str) -> str:
    return (f"I'm really sorry you're going through this. You deserve support right now. Please call "
            f"{crisis_number} to talk to a trained counsellor, or {emergency_number} if you are in immediate "
            f"danger. I'm an automated assistant and can't send help myself.")


MAX_LLM_REPLY_CHARS = 280

_UNITS = r"(?:mg|mcg|ml|iu|units?|tablets?|pills?|capsules?|drops?|spoons?|teaspoons?|tablespoons?|puffs?)"
_NUMBER_WORD = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|half|couple|twice|once)"
_DRUG_STEMS = (r"aspirin|ibuprofen|paracetamol|acetaminophen|antibiotics?|steroids?|crocin|dolo|calpol|combiflam|"
               r"metformin|insulin|amoxicillin|azithromycin|cetirizine|pantoprazole|painkillers?|"
               r"\w+(?:cillin|mycin|prazole|olol|statin|pril|sartan)")
# Things a receptionist bot must never claim: it cannot dispatch, notify or transfer anyone.
_FALSE_HELP = (r"on\s+the\s+way|dispatch\w*|i(?:'ve|\s+have)\s+(?:informed|notified|alerted|sent|called|connected|"
               r"transferred)|(?:i'?ll|i\s+will)\s+(?:send|call|alert|notify|connect|transfer)|help\s+is\s+coming|"
               r"a\s+nurse\s+will|a\s+doctor\s+will\s+(?:call|come)")

_BLOCKED = re.compile(
    rf"""\b\d+(?:\.\d+)?\s?{_UNITS}\b
      |\b{_NUMBER_WORD}(?:\s+an?)?\s+{_UNITS}\b
      |\bevery\s+(?:\d+|{_NUMBER_WORD})\s+(?:hours?|days?)\b
      |\b(?:diagnos\w*|prescri\w*|dosage|dose|overdose)\b
      |\byou\s+(?:probably|likely|might|may|could)?\s*have\b
      |\b(?:you\s+(?:should|must|can|could)|try)\s+(?:take|taking|use|using|apply|drink|eat|give|rest)\b
      |\b(?:it|this)\s+(?:is|'s)\s+(?:probably|likely|just|nothing|common|normal)\b
      |\b(?:sounds?\s+like|could\s+be|might\s+be|side\s+effects?)\b
      |\b(?:{_DRUG_STEMS})\b
      |\b(?:{_FALSE_HELP})
      |ignore\s+(?:all|any|previous|the above)|system\s+prompt|as\s+an\s+ai\b
      |https?://|www\.|\d{{6,}}
    """,
    re.IGNORECASE | re.VERBOSE,
)


def guard_reply(text: str | None) -> str | None:
    """Return the text if it is safe to speak, else None (caller falls back to canned copy)."""
    if not text:
        return None
    text = " ".join(text.split())
    if not text or len(text) > MAX_LLM_REPLY_CHARS or _BLOCKED.search(text):
        return None
    return text
