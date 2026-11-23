"""Deterministic natural-language understanding for a phone receptionist.

Everything here is a pure function of the caller's words: word-boundary regexes, no model, no I/O, so it
is fast (microseconds), explainable, and unit-testable. The LLM only gets involved when ``parse`` returns
no intent (see ``agent.py``). Safety-relevant detection (emergency, self-harm, medical advice) lives here
so it can never be skipped by a model failure.
"""
import re
from dataclasses import dataclass

# ----------------------------------------------------------------------------------------------- safety
# Recall beats precision here: a wrongly escalated caller hears "call 108" and can still ask again,
# a missed emergency is booked into a department queue. Hinglish terms are included on purpose.
_SELF_HARM = re.compile(
    r"\b(?:suicid\w*|kill(?:ing)? (?:myself|himself|herself|themselves)|"
    r"end(?:ing)? (?:my|his|her|their) (?:own )?life|take my own life|want to die|wanna die|"
    r"(?:don'?t|do not) want to (?:live|be alive)|self[- ]harm|end it all|"
    r"hang(?:ing)? (?:myself|himself|herself)|slit(?:ting)? (?:my )?wrists?|"
    r"jump(?:ing)? (?:off|from) (?:a |the |my )?(?:roof|bridge|building|terrace|cliff|balcony)|"
    r"wish i (?:was|were) dead|better off dead|(?:no reason|not worth) (?:to |for )?(?:live|living)|"
    r"life (?:is )?not worth living|marna chah\w*|mar jaunga|mar jaana|aatmahatya|atmahatya|khud ?kushi|"
    r"jaan de (?:dunga|dungi|du)|jeena nahi chah\w*)\b")
# "I hurt myself playing cricket" is an injury, not a crisis: soft phrases only count without such context.
_SELF_HARM_SOFT = re.compile(r"\b(?:hurt(?:ing)?|harm(?:ing)?|cut(?:ting)?) myself\b")
_ACCIDENT_CONTEXT = re.compile(r"\b(?:fell|fallen|falling|slipped|tripped|playing|cooking|shaving|chopping|"
                               r"accidentally|by accident|gym|workout|exercising)\b")

# Acute states: always escalate, whatever else the caller says.
_ACUTE = re.compile(
    r"\b(?:can'?t breathe?|cannot breathe?|can not breathe?|unable to breathe|not able to breathe|"
    r"(?:isn'?t|aren'?t|is not|are not|am not|not|stopped|stops) breathing|gasping|struggling to breathe|"
    r"difficulty breathing|trouble breathing|shortness of breath|short of breath|turning blue|blue lips|"
    r"throat (?:is )?(?:closing|swelling)|unconscious|unconcious|unconsious|unresponsive|not responding|"
    r"not waking up|won'?t wake up|(?:severe|heavy|massive|excessive|a lot of|lots of) bleeding|"
    r"bleeding (?:heavily|badly|a lot|profusely|won'?t stop)|choking|ambulance|anaphyla\w+|"
    r"face (?:is )?droop\w*|droop\w* (?:face|mouth)|slurred speech|speech (?:is )?slurred|slurring|"
    r"(?:can'?t|cannot|unable to) (?:move|feel|lift) (?:my|his|her|one|an?|the) (?:arm|leg|side|hand)|"
    r"one side (?:of (?:the |my |his |her )?(?:body|face) )?(?:is )?(?:numb|weak|paraly\w+)|"
    r"sudden (?:numbness|weakness|confusion|loss of vision)|paraly[sz]ed|seizing|"
    r"(?:i'?m|i am|he'?s|she'?s|is|we'?re|they'?re) dying(?! (?:to|for))|"
    r"behosh|saans nahi|sans nahi|khoon (?:bahut|zyada|beh)|zeher|zahar|dil ka daura)\b")

# Events that may be history ("follow-up for my father's stroke last year"): escalate unless clearly past.
_EVENT = re.compile(
    r"\b(?:heart attack|cardiac arrest|stroke|collapsed(?! lung)|overdos\w+|poison(?:ed|ing)|allergic reaction|"
    r"passed out|fainted|blacked out|(?:had|having|has|got) (?:an? )?(?:seizures?|convuls\w+)|convuls\w+|"
    r"(?:had|got) an? fit|having (?:an? )?fits?|"
    r"(?:road|car|bike|serious|major|bad|met with an?|had an?) accident|accident(?! (?:and|&|department|dept|ward|"
    r"insurance|claim))|(?:bad|severe|serious|major|deep) burns?|burn(?:ed|t) (?:badly|himself|herself|myself)|"
    r"head injury|snake ?bite|electric shock|electrocuted|drown\w*|water (?:has |just )?broke|labou?r pains?|"
    r"in labou?r|swallowed (?:poison|something|a bottle)|"
    r"(?:swallowed|drank|drunk|ate|consumed) (?:\w+ ){0,3}(?:poison|bleach|kerosene|pesticide|phenyl|acid|"
    r"detergent|insecticide)|"
    r"(?:took|taken|swallowed|ate|consumed) (?:too many|many|several|lots of|a (?:whole )?(?:bottle|strip|packet|"
    r"handful) of|\d{2,}) (?:\w+ ){0,2}(?:pills|tablets|capsules|medicines?))\b")
_PAST = re.compile(r"\b(?:last (?:year|month|week|decade)|(?:years?|months?|weeks?|decades?) ago|follow[- ]?up|"
                   r"history of|when i was|recover\w*|previous\w*|earlier this year|used to)\b")
_NOW = re.compile(r"\b(?:right now|just now|at the moment|this minute|is happening|just happened|happening now|"
                  r"currently)\b")

# "emergency" on its own escalates, but not when negated ("it's not an emergency") and not when the caller is
# asking about the emergency department / number / services.
_EMERGENCY_WORD = re.compile(r"\bemergenc(?:y|ies)\b")
_NEGATED_BEFORE = re.compile(r"(?:\bnot|\bisn'?t|\bno|\bnon)[- ](?:an? )?$")
_FACILITY_TAIL = re.compile(r"\s+(?:department|dept|ward|room|hours|numbers?|contact|wing|unit|clinic|helpline|line)\b")
_CARE_TAIL = re.compile(r"\s+(?:care|services?)\b")
_ASKING = re.compile(r"\b(?:do you|is there|are there|where (?:is|are)|which|your|timings?|hours|number|contact|"
                     r"available|open|24/7|cost|fees?|offer|provide)\b")

_CHEST = re.compile(r"\b(?:chest\s+(?:pain|tightness|tight|pressure|discomfort|hurts?|hurting|heav(?:y|iness))|"
                    r"pain in (?:my|his|her|the) chest|"
                    r"(?:seene|sine|chest|chhati|chati)\s+(?:me|mein|mai|main)\s+dard)\b")
_CHEST_SEVERE = re.compile(
    r"\b(?:severe|crushing|sudden|intense|unbearable|worst|radiating|spreading|(?:left |right )?arm|jaw|sweating|"
    r"numb\w*|right now|happening now|going on now)\b")

_MEDICINE = (r"(?:medicines?|medications?|drugs?|tablets?|pills?|capsules?|syrups?|antibiotics?|painkillers?|"
             r"paracetamol|ibuprofen|aspirin|crocin|dolo|calpol|combiflam|metformin|insulin|"
             r"amoxicillin|azithromycin|cetirizine|pantoprazole)")
# "general medicine" is a department, not a drug; bare "can I take the 10 AM slot" is not a dosage question.
_ADVICE = re.compile(
    rf"\b(?:(?:should|can|could|may) (?:i|we) (?:take|have|use|give|apply|drink) (?:\w+ ){{0,3}}"
    rf"(?<!general ){_MEDICINE}|"
    r"(?:should|can|could|may) (?:i|we) take (?:something|anything)|"
    r"what (?:medicine|medication|drug|tablet|pill)s?|which (?:medicine|medication|drug|tablet)s?|"
    r"what dos(?:e|age)|dosage|how many (?:tablets|pills|mg|capsules|drops|spoons)|"
    rf"how much (?:\w+ ){{0,3}}(?:should|can|could|do) (?:i|we|he|she|they) (?:take|give|use|have)|"
    rf"is it (?:ok|okay|safe|fine) to (?:take|give|use|mix) (?:\w+ ){{0,3}}{_MEDICINE}|"
    r"is it safe to (?:eat|drink|take|give|use|mix)|side effects?|drug interactions?|"
    r"how often (?:should|can|do) (?:i|we)|\d+\s?(?:mg|ml)|"
    r"prescribe|prescription|diagnos\w+|what(?:'s| is) wrong with me|"
    r"what do i have(?! (?:scheduled|booked|coming|planned|on|this|next|tomorrow|today|for))|"
    r"is it (?:serious|dangerous|cancer|normal|safe)|do i have (?:cancer|diabetes|a tumou?r)|"
    r"am i (?:sick|dying)|what causes|home remed\w+|remed(?:y|ies)|treatment for|"
    r"how (?:do|can) i (?:treat|cure|stop|get rid))\b")
# Words that suggest the caller may be unwell even though no rule fired (used to add a safety line to a miss).
_DISTRESS = re.compile(r"\b(?:help|pain|hurts?|bleeding|blood|sick|dying|urgent|serious|worse|worsening|attack|"
                       r"fell|injur\w+|accident|swollen|swelling|vomit\w*|faint\w*|dizzy|scared|panic\w*|"
                       r"breath\w*|burn\w*|bite|bitten|fits?)\b")

# ---------------------------------------------------------------------------------------------- topics
# specialty -> (explicit department/doctor-type words, symptom/body-part words)
_SPECIALTY_PATTERNS: dict[str, tuple[str, str]] = {
    "Cardiology": (r"cardio\w*|heart\s+(?:doctor|specialist|department)",
                   r"heart|palpitations?|blood\s+pressure|bp|hypertension|chest"),
    "Neurology": (r"neuro\w*",
                  r"headaches?|migraines?|seizures?|numbness|dizz\w+|brain|nerves?|memory\s+loss"),
    "Orthopedics": (r"orthop[ae]di\w*|ortho|bone\s+doctor",
                    r"bones?|fracture\w*|joints?|knees?|back\s+pain|shoulder|spine|sprain\w*|ankle|hip|neck\s+pain"),
    "Pediatrics": (r"p[ae]diatri\w*|child\s+specialist",
                   r"child|children|kids?|babies|baby|infant|toddler|newborn|son|daughter"),
    "Gynecology": (r"gyn[ae]co\w*|ob-?gyn|maternity",
                   r"pregnan\w*|periods?|menstrua\w*|women'?s\s+health"),
    "Dermatology": (r"dermato\w*", r"skin|rash(?:es)?|acne|eczema|itch\w*|hair\s+loss|pimples?"),
    "Ophthalmology": (r"ophthalmo\w*|eye\s+(?:doctor|specialist|department)",
                      r"eyes?|vision|eyesight|cataract|glasses|spectacles"),
    "ENT": (r"ent|e\.n\.t|ear\s+nose\s+(?:and\s+)?throat|otolaryng\w*",
            r"ears?|throat|sinus\w*|nose|nasal|tonsil\w*|hearing"),
    "General Medicine": (r"general\s+(?:medicine|physician|doctor|practitioner)|physician|gp",
                         r"fever|cold|cough|flu|stomach|diabetes|diabetic|vomiting|nausea|body\s+ache|"
                         r"weakness|check-?up|health\s+check\w*"),
}
_SPECIALTY_RE = {name: (re.compile(rf"\b(?:{names})\b"), re.compile(rf"\b(?:{symptoms})\b"))
                 for name, (names, symptoms) in _SPECIALTY_PATTERNS.items()}

_BOOK = re.compile(r"\b(?:book\w*|schedul\w*|appointments?|slots?|reserve)\b")
_BOOK_WEAK = re.compile(r"\b(?:see a doctor|consult\w*|visit|meet|check-?up|available|availability)\b")
_CANCEL = re.compile(r"\b(?:cancel\w*(?!\s+(?:policy|policies|charges?|fees?|rules?|terms))|call off)\b")
_RESCHEDULE = re.compile(r"\b(?:reschedul\w*|rebook\w*|postpone\w*|"
                         r"(?:move|change|shift|push)\s+(?:my|the|this|that)?\s*(?:appointment|booking|slot|visit|time))\b")
_MY_APPTS = re.compile(r"\b(?:my|upcoming|existing|current)\s+(?:appointments?|bookings?|visits?)\b|"
                       r"\bdo i have (?:an?|any) (?:appointments?|bookings?)\b|\bappointment status\b|"
                       r"\b(?:what|which) (?:appointments?|bookings?) (?:do|have|did) i\b|"
                       r"\b(?:appointments?|bookings?) (?:do )?i (?:have|made)\b|"
                       r"\bwhat do i have (?:scheduled|booked|coming up)\b|"
                       r"\bwhen is my\b")
_SPECIALIST_NOUNS = (r"cardiologists?|neurologists?|orthop(?:a)?edic(?:ians|s)?|pediatricians?|paediatricians?|"
                     r"gyn(?:a)?ecologists?|dermatologists?|ophthalmologists?|ent specialists?|physicians?|"
                     r"specialists?")
_DOCTOR_LIST = re.compile(r"\b(?:which|what|list|who)\b.*\b(?:doctors?|departments?|specialit(?:y|ies)|"
                          r"specialisations?|specializations?|services|" + _SPECIALIST_NOUNS + r")\b|"
                          r"\bdoctors? (?:available|do you have)\b|\bour (?:doctors?|departments?|specialists?)\b|"
                          r"\bdo you have (?:any |a )?(?:doctors?|departments?|" + _SPECIALIST_NOUNS + r")\b")
_GREETING = re.compile(r"^\s*(?:hi+|hello+|hey+|hiya|namaste|hola|good\s+(?:morning|afternoon|evening))\b")
_GOODBYE = re.compile(r"\b(?:bye|goodbye|good\s+bye|see you|that'?s all|that is all|nothing else|that'?s it|"
                      r"i'?m done|hang up)\b")
_THANKS = re.compile(r"\b(?:thanks|thank you|thankyou|thx)\b")
_NO_RECORD = re.compile(
    r"\b(?:(?:don'?t|do not|stop|please stop)\s+(?:record\w*|transcrib\w*|sav\w*\s+(?:this|the|my)\s+"
    r"(?:call|conversation|chat))|no\s+recording|opt(?:ing)? out of (?:record\w*|transcri\w*)|"
    r"not (?:be )?(?:record\w*|transcribed|saved)|"
    r"(?:don'?t|do not) (?:want|consent|agree)[^.]{0,30}(?:record\w*|transcri\w*|sav\w*))\b")
_IDENTITY = re.compile(r"\b(?:are you|r u|you a|is this)\s+(?:a |an |the )?(?:human|robot|real|bot|ai|machine|person|"
                       r"automated|recorded)\b|\bwho are you\b|\bwhat are you\b|"
                       r"\bam i (?:talking|speaking|chatting) (?:to|with) (?:a |an |the )?(?:real |actual )?"
                       r"(?:person|human|robot|bot|ai|machine|doctor|computer)\b")
_HUMAN = re.compile(r"\b(?:(?:speak|talk)\s+(?:to|with)\s+(?:a|an|the|some)\s*(?:human|person|someone|operator|"
                    r"receptionist|agent|nurse|representative)|real person|human being|operator|receptionist|"
                    r"representative|customer care)\b")
_OUT_OF_SCOPE = re.compile(r"\b(?:weather|jokes?|pizza|movies?|cricket|football|songs?|recipes?|stocks?|bitcoin|"
                           r"news|politics|election|homework|poem|lottery|horoscope|capital of|president|"
                           r"prime minister|translate)\b")

_INQUIRIES: list[tuple[str, re.Pattern]] = [  # first match wins, so order matters
    ("emergency_dept", re.compile(r"\bemergenc(?:y|ies)\s+(?:department|dept|ward|room|wing|unit|services?|care|"
                                  r"hours|number|numbers|contact|helpline|line)\b")),
    ("parking", re.compile(r"\bpark(?:ing)?\b")),
    ("insurance", re.compile(r"\b(?:insurance|cashless|mediclaim|tpa|cghs|ayushman)\b")),
    ("fees", re.compile(r"\b(?:fees?|cost|price|prices|charges?|how much|rates?)\b")),
    ("phone", re.compile(r"\b(?:phone|number|contact|call you|email)\b")),
    ("location", re.compile(r"\b(?:where|address|location|located|directions?|how (?:do i|can i|to) (?:get|reach)|"
                            r"landmark|nearest)\b")),
    ("hours", re.compile(r"\b(?:hours|opening|closing|timings?|open|close|closed|until when|working days|"
                         r"what time (?:do|does) you)\b")),
]

# -------------------------------------------------------------------------------------- time and slots
_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
             "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
             "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + r")\b")
_NUMBER_WORDS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_HOUR_WORDS = "|".join(w for w, n in _NUMBER_WORDS.items() if 1 <= n <= 12)
_MERIDIEM = r"(a\.?m\.?|p\.?m\.?|o'?clock)"
_TIME_NUM_MERIDIEM = re.compile(rf"\b(\d{{1,2}})(?::\d{{2}})?\s*{_MERIDIEM}")
_TIME_WORD_MERIDIEM = re.compile(rf"\b({_HOUR_WORDS})\s*{_MERIDIEM}")
_TIME_COLON = re.compile(r"\b(\d{1,2}):\d{2}\b")
_TIME_AT_NUM = re.compile(r"\b(?:at|around|by)\s+(\d{1,2})\b(?!\s*(?:st|nd|rd|th)\b)")
_TIME_AT_WORD = re.compile(rf"\b(?:at|around|by)\s+({_HOUR_WORDS})\b")
_DAYPARTS = {"morning": "morning", "afternoon": "afternoon", "evening": "evening", "tonight": "evening"}

_ORDINALS = [(0, r"first|1st|earliest|soonest|top"), (1, r"second|2nd"), (2, r"third|3rd"),
             (3, r"fourth|4th")]
_AFFIRM = re.compile(r"\b(?:yes|yeah|yep|yup|sure|ok|okay|confirm\w*|correct|right|please do|go ahead|"
                     r"sounds good|that works|fine|absolutely|definitely|book it|do it|perfect)\b")
_DENY = re.compile(r"\b(?:no|nope|nah|don'?t|do not|not really|never ?mind|stop|wrong|incorrect|negative)\b")
_NEGATION = re.compile(r"\b(?:not|never|isn'?t|wasn'?t|aren'?t|can'?t|won'?t)\b")
_DOCTOR_NAME = re.compile(r"\bdr\.?\s+([a-z][a-z'-]+)")
_NAME = re.compile(r"\b(?:my name is|my name'?s|this is|call me|i am called|i'?m called|name is)\s+"
                   r"([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+)?)")
_NAME_STOP = frozenset("""a an the about regarding for to not just calling looking my in urgent emergency please really
very so still too also your i we you can need want would am have like is was and but here speaking from at on
with trying wanting having feeling sick calling asking hoping booking cancelling
back again later soon now today tomorrow tonight morning afternoon evening noon ridiculous silly crazy useless
terrible wrong ready sorry fine good great late early new old confused worried""".split()) | _WEEKDAYS.keys()

# Words that are never a person's name when the whole reply is one ("friday", "cardiology").
_NOT_NAMES = frozenset("""yes yeah yep yup no nope nah ok okay sure skip pass nothing none hi hello hey thanks
thank bye goodbye cancel book appointment doctor cardiology neurology orthopedics pediatrics gynecology dermatology
ophthalmology ent general medicine cardiologist neurologist""".split())
MAX_NAME_CHARS = 40


@dataclass
class Parsed:
    text: str
    norm: str
    emergency: bool = False
    self_harm: bool = False
    caution: bool = False           # worrying-but-not-emergency symptom, e.g. chest pain
    medical_advice: bool = False
    no_record: bool = False         # caller asked not to be recorded/transcribed
    distress: bool = False          # sounds unwell even though no rule fired (adds a safety line to a miss)
    intent: str | None = None
    inquiry: str | None = None      # parking | insurance | fees | phone | location | hours | emergency_dept
    specialty: str | None = None
    doctor_name: str | None = None  # "Dr. Mehta" -> "mehta"
    day: str | None = None          # today | tomorrow | day_after | mon..sun
    daypart: str | None = None      # morning | afternoon | evening
    hour: int | None = None         # 24h clock
    name: str | None = None


def normalize(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").replace("‘", "'").split())


def parse(text: str) -> Parsed:
    t = normalize(text)
    p = Parsed(text=text, norm=t)
    p.self_harm = bool(_SELF_HARM.search(t)) or (bool(_SELF_HARM_SOFT.search(t)) and not _ACCIDENT_CONTEXT.search(t))
    p.emergency = (p.self_harm or bool(_ACUTE.search(t)) or _event_active(t) or _bare_emergency(t)
                   or (bool(_CHEST.search(t)) and bool(_CHEST_SEVERE.search(t))))
    p.caution = bool(_CHEST.search(t)) and not p.emergency
    p.medical_advice = bool(_ADVICE.search(t))
    p.no_record = bool(_NO_RECORD.search(t))
    p.distress = bool(_DISTRESS.search(t))
    p.specialty = detect_specialty(t)
    p.doctor_name = (m.group(1) if (m := _DOCTOR_NAME.search(t)) else None)
    p.day = parse_day(t)
    p.daypart = parse_daypart(t)
    p.hour = parse_time(t)
    p.name = parse_name(t)
    p.intent, p.inquiry = _classify(t, p)
    return p


def _event_active(t: str) -> bool:
    """Acute events escalate unless the caller is clearly talking about history ("stroke last year")."""
    return bool(_EVENT.search(t)) and (bool(_NOW.search(t)) or not _PAST.search(t))


def _bare_emergency(t: str) -> bool:
    """The word "emergency" escalates, except negated ("not an emergency") or asking about the department/services."""
    for m in _EMERGENCY_WORD.finditer(t):
        tail = t[m.end():]
        if _NEGATED_BEFORE.search(t[:m.start()]) or _FACILITY_TAIL.match(tail):
            continue
        if _CARE_TAIL.match(tail) and _ASKING.search(t):
            continue
        return True
    return False


def _classify(t: str, p: Parsed) -> tuple[str | None, str | None]:
    if p.emergency:
        return "emergency", None
    if p.no_record:
        return "no_recording", None
    if _IDENTITY.search(t):
        return "identity", None
    if _HUMAN.search(t):
        return "human", None
    if _GOODBYE.search(t):
        return "goodbye", None
    if _CANCEL.search(t):
        return "cancel", None
    if _RESCHEDULE.search(t):
        return "reschedule", None
    if _MY_APPTS.search(t):
        return "my_appointments", None
    if _DOCTOR_LIST.search(t):
        return "doctor_list", None
    strong_booking = bool(_BOOK.search(t))
    if not strong_booking and not p.medical_advice:  # "what is the consultation fee" is a question, not a booking
        for kind, pattern in _INQUIRIES:
            if pattern.search(t):
                return "inquiry", kind
    if strong_booking or _BOOK_WEAK.search(t) or p.specialty or p.doctor_name:
        return "book", None
    if _GREETING.search(t):
        return "greeting", None
    if _THANKS.search(t):
        return "thanks", None
    if _OUT_OF_SCOPE.search(t):
        return "out_of_scope", None
    return None, None


def detect_specialty(t: str) -> str | None:
    """Pick the specialty with the strongest, earliest evidence (explicit department words beat symptoms)."""
    best: tuple[int, int, str] | None = None
    for name, (named, symptom) in _SPECIALTY_RE.items():
        for weight, pattern in ((2, named), (1, symptom)):
            if m := pattern.search(t):
                candidate = (-weight, m.start(), name)
                if best is None or candidate < best:
                    best = candidate
    return best[2] if best else None


def parse_day(t: str) -> str | None:
    if "day after tomorrow" in t:
        return "day_after"
    if "tomorrow" in t:
        return "tomorrow"
    if re.search(r"\b(?:today|tonight|this (?:morning|afternoon|evening))\b", t):
        return "today"
    if m := _WEEKDAY_RE.search(t):
        return ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[_WEEKDAYS[m.group(1)]]
    return None


def parse_daypart(t: str) -> str | None:
    for word, part in _DAYPARTS.items():
        if re.search(rf"\b{word}\b", t):
            return part
    return None


def _to_24h(hour: int, meridiem: str | None) -> int | None:
    if meridiem and meridiem.startswith("a"):
        return 0 if hour == 12 else hour if 1 <= hour <= 11 else None
    if meridiem and meridiem.startswith("p"):
        return 12 if hour == 12 else hour + 12 if 1 <= hour <= 11 else None
    if not 1 <= hour <= 12:
        return None
    return hour + 12 if hour <= 6 else hour  # clinic runs ~8-20h: "at 3" means 3 PM, "at 10" means 10 AM


def parse_time(t: str) -> int | None:
    """Spoken/typed clock time -> hour on the 24h clock, or None. Minutes are ignored (slots are hourly)."""
    if re.search(r"\b(?:noon|midday)\b", t):  # not a substring test: "afternoon" is a daypart, not 12 o'clock
        return 12
    for pattern, words in ((_TIME_NUM_MERIDIEM, False), (_TIME_WORD_MERIDIEM, True)):
        if m := pattern.search(t):
            hour = _NUMBER_WORDS[m.group(1)] if words else int(m.group(1))
            meridiem = m.group(2)
            return _to_24h(hour, None if meridiem.startswith("o") else meridiem)
    if m := _TIME_COLON.search(t):
        hour = int(m.group(1))
        if 1 <= hour <= 12:
            return _to_24h(hour, None)  # "2:30" means 2:30 PM, same as a bare "at 2"
        return hour if 13 <= hour <= 23 else None
    if m := _TIME_AT_NUM.search(t):
        return _to_24h(int(m.group(1)), None)
    if m := _TIME_AT_WORD.search(t):
        return _to_24h(_NUMBER_WORDS[m.group(1)], None)
    return None


def parse_name(t: str) -> str | None:
    m = _NAME.search(t)
    if not m:
        return None
    words = []
    for word in m.group(1).split():
        if word in _NAME_STOP or word in _NOT_NAMES:
            break
        words.append(word)
    name = " ".join(w.capitalize() for w in words)
    return name[:MAX_NAME_CHARS] or None


def bare_name(text: str) -> str | None:
    """A reply that is only a name ("Anita", "Anita Rao"). Call it only right after asking for a name."""
    t = normalize(text).strip(" .!")
    if not re.fullmatch(r"[a-z][a-z'-]*(?: [a-z][a-z'-]*){0,2}", t):
        return None
    words = t.split()
    if any(w in _NAME_STOP or w in _NOT_NAMES for w in words):
        return None
    return " ".join(w.capitalize() for w in words)[:MAX_NAME_CHARS]


def parse_digits(text: str) -> str:
    """Longest run of spoken or typed digits ("nine four four one", "double five", "9441-321") as a string."""
    tokens = re.findall(r"\d+|[a-z']+", text.lower())
    best, run, repeat, previous = "", "", 1, ""
    for token in tokens:
        was, previous = previous, token
        if token.isdigit():
            run += token
        elif token in ("double", "triple"):
            repeat = 2 if token == "double" else 3
            continue
        elif token in _NUMBER_WORDS and was in ("last", "final"):
            continue  # "the last four is 1662": "four" counts digits, it is not one
        elif token in _NUMBER_WORDS and _NUMBER_WORDS[token] <= 9:
            run += str(_NUMBER_WORDS[token]) * repeat
        elif token in ("and", "um", "uh", "its", "it's", "is"):
            continue  # filler words between digits do not end the run
        else:
            run = ""
        repeat = 1
        if len(run) > len(best):
            best = run
    return best


def pick_ordinal(text: str, count: int) -> int | None:
    """'the second one', 'earliest', 'last' -> zero-based index into an offered list."""
    t = normalize(text)
    for index, pattern in _ORDINALS:
        if index < count and re.search(rf"\b(?:{pattern})\b", t):
            return index
    if re.search(r"\b(?:last|latest)\b", t):
        return count - 1
    return None


def bare_number(text: str) -> int | None:
    """A lone small number ("2", "two") used to pick from a numbered list."""
    t = re.sub(r"^(?:the\s+)?(?:option|number|choice|slot|no\.?)\s*", "", normalize(text).strip(" .!?"))
    if t.isdigit():
        return int(t)
    return _NUMBER_WORDS.get(t)


def yes_no(text: str) -> str | None:
    """'yes' / 'no' / None. If both appear ("yes... no wait"), whichever comes first decides."""
    t = normalize(text)
    yes, no = _AFFIRM.search(t), _DENY.search(t)
    if not no:  # "I'm not sure" / "that's not right" must never confirm a write
        no = _NEGATION.search(t)
    if yes and no:
        return "yes" if yes.start() < no.start() else "no"
    return "yes" if yes else "no" if no else None
