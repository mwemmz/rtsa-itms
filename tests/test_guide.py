from app.services.assistant import answer_question


def test_how_to_questions_get_a_reviewed_answer():
    for q, topic in [("How do I apply for a licence?", "apply_licence"), ("how do i pay a fine", "pay_fine"),
                     ("What should I do if a tyre bursts?", "tyre_burst"), ("what is a challan", "what_is_challan")]:
        r = answer_question(q)
        assert r["mode"] == "guide" and r["topic"] == topic and r["answer"]


def test_highway_code_questions_still_use_the_code():
    assert answer_question("Who has right of way at a roundabout?")["mode"] == "extract"
    assert answer_question("What is the capital of France?")["mode"] == "no_match"
