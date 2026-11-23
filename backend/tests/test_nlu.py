import pytest

from app.services import nlu


@pytest.mark.parametrize("text", [
    "this is an emergency", "i am having a heart attack", "severe chest pain", "he is not breathing",
    "my father collapsed", "she is unconscious", "there is a lot of bleeding",
])
def test_emergencies_always_escalate(text):
    assert nlu.parse(text).emergency


@pytest.mark.parametrize("text", [
    "is there an emergency department", "what are the emergency ward hours", "i need an appointment next week",
    "what is your emergency number",
])
def test_emergency_words_in_questions_do_not_escalate(text):
    assert not nlu.parse(text).emergency


def test_chest_pain_is_a_cardiology_booking_with_a_caution_not_an_emergency():
    parsed = nlu.parse("i have had chest pain for two days")
    assert not parsed.emergency
    assert parsed.caution
    assert parsed.intent == "book"
    assert parsed.specialty == "Cardiology"


@pytest.mark.parametrize("text", ["i want to end my life", "i feel like killing myself"])
def test_self_harm_is_detected(text):
    assert nlu.parse(text).self_harm


@pytest.mark.parametrize("text, intent", [
    ("hello there", "greeting"), ("hi", "greeting"), ("good morning", "greeting"),
    ("i want to book an appointment", "book"), ("cancel my appointment", "cancel"),
    ("i need to reschedule", "reschedule"), ("what are my appointments", "my_appointments"),
    ("which doctors do you have", "doctor_list"), ("do you have any doctors", "doctor_list"),
    ("bye", "goodbye"), ("thank you", "thanks"),
    ("what is the weather like", "out_of_scope"), ("are you a robot", "identity"),
    ("can i speak to a human", "human"), ("please don't record this call", "no_recording"),
    ("am i talking to a real person", "identity"), ("connect me to a real person", "human"),
    ("i do not want to be recorded", "no_recording"),
    ("how much is the consultation fee", "inquiry"), ("how do i reach you", "inquiry"),
])
def test_intent_classification(text, intent):
    assert nlu.parse(text).intent == intent


@pytest.mark.parametrize("text, kind", [
    ("how much is the consultation fee", "fees"),
    ("what is the fee for a cardiology consultation", "fees"),
    ("how do i reach you", "location"),
    ("what is your phone number", "phone"),
])
def test_questions_about_the_hospital_are_not_booking_requests(text, kind):
    parsed = nlu.parse(text)
    assert (parsed.intent, parsed.inquiry) == ("inquiry", kind)


@pytest.mark.parametrize("text", ["book a consultation", "i want to consult a cardiologist tomorrow"])
def test_consultation_with_a_booking_intent_still_books(text):
    assert nlu.parse(text).intent == "book"


def test_booking_verb_beats_out_of_scope_words():
    assert nlu.parse("book me a cardiologist before the cricket match").intent == "book"


def test_looking_is_not_a_name():
    parsed = nlu.parse("i am looking for a doctor")
    assert parsed.name is None
    assert parsed.intent is None


@pytest.mark.parametrize("text, name", [
    ("my name is Anita Rao", "Anita Rao"), ("this is Ravi", "Ravi"), ("call me Sam", "Sam"),
])
def test_explicit_names(text, name):
    assert nlu.parse(text).name == name


def test_bare_name_accepts_a_name_and_rejects_filler():
    assert nlu.bare_name("Praneeth") == "Praneeth"
    assert nlu.bare_name("Anita Rao") == "Anita Rao"
    for filler in ("yes", "skip", "no thanks", "hello"):
        assert nlu.bare_name(filler) is None


@pytest.mark.parametrize("text, hour", [
    ("at 10 am", 10), ("3 pm", 15), ("at 3", 15), ("10:30 am", 10), ("five pm", 17), ("at 9", 9),
])
def test_time_parsing(text, hour):
    assert nlu.parse_time(text) == hour


@pytest.mark.parametrize("text, day", [
    ("today", "today"), ("tomorrow", "tomorrow"), ("day after tomorrow", "day_after"),
    ("on friday", "fri"), ("next monday", "mon"), ("nothing", None),
])
def test_day_parsing(text, day):
    assert nlu.parse_day(text) == day


def test_daypart_parsing():
    assert nlu.parse_daypart("tomorrow morning") == "morning"
    assert nlu.parse_daypart("in the evening") == "evening"
    assert nlu.parse_daypart("whenever") is None


@pytest.mark.parametrize("text, count, picked", [
    ("the first one", 3, 0), ("second", 3, 1), ("the third", 3, 2), ("the earliest", 3, 0), ("the last one", 3, 2),
    ("fourth", 3, None), ("whatever", 3, None),
])
def test_pick_ordinal(text, count, picked):
    assert nlu.pick_ordinal(text, count) == picked


@pytest.mark.parametrize("text, number", [("2", 2), ("two", 2), ("option 3", 3), ("number one", 1), ("hello", None)])
def test_bare_number(text, number):
    assert nlu.bare_number(text) == number


def test_digit_extraction_handles_spoken_numbers():
    assert nlu.parse_digits("one six six two") == "1662"
    assert nlu.parse_digits("it is 1662") == "1662"
    assert nlu.parse_digits("double six two") == "662"
    assert nlu.parse_digits("no numbers here") == ""


@pytest.mark.parametrize("text, answer", [
    ("yes please", "yes"), ("yeah sure", "yes"), ("no thanks", "no"), ("nope", "no"), ("maybe later", None),
])
def test_yes_no(text, answer):
    assert nlu.yes_no(text) == answer


def test_specialty_from_symptoms_and_names():
    assert nlu.parse("my child has a fever").specialty == "Pediatrics"
    assert nlu.parse("skin rash").specialty == "Dermatology"
    assert nlu.parse("a heart doctor").specialty == "Cardiology"
    assert nlu.parse("i want a neurologist").specialty == "Neurology"


def test_substring_traps_do_not_match():
    # "which" contains "hi", "different" contains "ent" - word boundaries must prevent false matches
    assert nlu.parse("which one is different").intent != "greeting"
    assert nlu.parse("which one is different").specialty is None


def test_doctor_name_is_extracted():
    assert nlu.parse("book dr mehta on monday at 10:30 am").doctor_name == "mehta"


def test_medical_advice_requests_are_flagged():
    assert nlu.parse("what medicine should i take for headache").medical_advice
    assert not nlu.parse("book a neurologist").medical_advice


# ---------------------------------------------------------------------------------------------- clinical-safety recall
@pytest.mark.parametrize("text", [
    "my baby isn't breathing", "my daughter swallowed a bottle of pills", "my son drank kerosene",
    "my child is having a fit", "my son had a seizure", "my chest hurts and my left arm is numb",
    "sudden numbness on one side and my face is drooping", "her speech is slurred", "I took 30 sleeping pills",
    "my father passed out and is not responding", "she fainted", "he blacked out", "unconcious", "I can't breath",
    "allergic reaction, my throat is closing", "road accident", "bad burns", "head injury", "snake bite",
    "electric shock", "drowning", "her water broke", "labour pains", "my father needs emergency care now",
    "send emergency services",
    "papa behosh ho gaye", "saans nahi aa rahi", "khoon bahut beh raha hai", "zeher kha liya", "dil ka daura",
])
def test_family_and_hinglish_emergencies_escalate(text):
    assert nlu.parse(text).emergency, text


@pytest.mark.parametrize("text", [
    "my son tried to kill himself", "i'm going to end it all tonight", "I want to hang myself",
    "I keep cutting myself", "I wish I was dead", "life is not worth living", "no reason to live", "better off dead",
    "main marna chahta hoon", "aatmahatya", "don't record this, I want to kill myself",
])
def test_self_harm_is_recognised_in_many_phrasings(text):
    parsed = nlu.parse(text)
    assert parsed.emergency and parsed.self_harm, text


@pytest.mark.parametrize("text", [
    "I hurt myself playing cricket, need an ortho", "it's not an emergency, I just want a cardiologist",
    "I'm dying to get my knee checked", "neurology follow-up for my father's stroke last year",
    "collapsed lung follow-up",
])
def test_near_misses_do_not_escalate(text):
    assert not nlu.parse(text).emergency, text


@pytest.mark.parametrize("text", [
    "pain in my chest", "chest me dard", "i have had chest pain for two days",
])
def test_milder_chest_complaints_get_a_caution_not_an_escalation(text):
    parsed = nlu.parse(text)
    assert parsed.caution and not parsed.emergency, text


@pytest.mark.parametrize("text", [
    "how much paracetamol can I give my son", "can I give my baby ibuprofen",
    "what are the side effects of metformin",
])
def test_dosing_and_drug_questions_are_medical_advice_requests(text):
    assert nlu.parse(text).medical_advice, text


@pytest.mark.parametrize("text", ["I don't consent to recording", "don't save this call", "please don't record me"])
def test_recording_opt_out_phrasings(text):
    assert nlu.parse(text).no_record, text


def test_i_have_no_records_is_not_an_opt_out():
    assert not nlu.parse("I have no records of this").no_record


# ---------------------------------------------------------------------------------------------- reviewer regressions
@pytest.mark.parametrize("text", ["I'm not sure", "that's not right", "no don't", "not really", "I don't think so"])
def test_negated_answers_are_never_a_yes(text):
    assert nlu.yes_no(text) != "yes", text


def test_last_four_digits_phrase_is_not_parsed_as_a_code_digit():
    assert nlu.parse_digits("the last four is 1662") == "1662"


def test_afternoon_is_not_noon():
    assert nlu.parse("book cardiology tomorrow afternoon").hour != 12


@pytest.mark.parametrize("text, hour", [("book at 2:30", 14), ("book at 9:00", 9), ("book at 15:00", 15)])
def test_colon_times_without_meridiem(text, hour):
    assert nlu.parse(text).hour == hour


@pytest.mark.parametrize("text", ["call me tomorrow", "call me back", "this is cardiology"])
def test_garbage_is_not_stored_as_a_name(text):
    assert nlu.parse_name(text) is None


@pytest.mark.parametrize("text", ["what do I have scheduled?", "what appointments do I have booked"])
def test_asking_about_own_appointments_is_not_medical_advice(text):
    parsed = nlu.parse(text)
    assert parsed.intent == "my_appointments" and not parsed.medical_advice


def test_cancellation_policy_question_does_not_start_a_cancel_flow():
    assert nlu.parse("what is your cancellation policy").intent != "cancel"


def test_our_doctors_menu_chip_lists_doctors():
    assert nlu.parse("Our doctors").intent == "doctor_list"
