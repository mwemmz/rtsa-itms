"""Reviewed answers for "how do I ..." questions about using the system, and for common road situations
the Highway Code text does not spell out. Checked before the Highway Code search; every answer is short,
plain and points at the screen to use. Nothing here states a fee or a penalty amount."""

from __future__ import annotations

import re

GUIDE_NOTE = "Guide to using this system and common situations. For the legal rules, see the Highway Code answers."

_ENTRIES: list[tuple[str, str, str]] = [
    ("apply_licence",
     r"(apply|applying|application|get|obtain|how.*(?:do|can) i get).{0,25}(driving )?(licen[cs]e|dl)\b|\bnew (driving )?licen[cs]e",
     "To apply for a driving licence: 1) Sign in as a citizen and add your NRC number under My account (it is needed to apply). "
     "2) Open My applications and choose Apply for a licence, then pick the licence type and submit the form. "
     "3) RTSA books your theory and practical tests; you can track the status on the same page and on your dashboard. "
     "4) Once you pass, an officer issues the licence and it appears under My licence. "
     "A medical report signed by a registered medical practitioner is required before a provisional licence (Highway Code Rule 95)."),
    ("renew_licence",
     r"(renew|renewal|expired|expiring|extend).{0,25}licen[cs]e|licen[cs]e.{0,25}(renew|expire)",
     "To renew a driving licence, sign in and open My licence. It shows the expiry date and a Renew button once the licence is within the renewal window or has expired. "
     "Follow the steps there and keep the receipt. It is a good idea to get a fresh medical report at each renewal (Rule 95)."),
    ("pay_fine",
     r"(pay|settle|clear).{0,25}(fine|challan|ticket|penalty)|(fine|challan|ticket).{0,20}(pay|paid)",
     "To pay a fine: sign in and open Fines & payments, find the fine, and press Pay now. Choose Mobile money or Card, follow the prompts "
     "(mobile money asks you to approve with your PIN, and cards may ask for a one-time code). You get a receipt number when it succeeds. "
     "In this demo the payment is simulated; no real money moves."),
    ("dispute_fine",
     r"(dispute|appeal|contest|challenge|wrong|unfair).{0,25}(fine|challan|ticket)|(fine|challan).{0,20}(dispute|appeal)",
     "To dispute a fine, open Fines & payments, choose the fine and use the appeal option. Give the reason and at least 15 characters of detail about what happened. "
     "An officer reviews it and the outcome shows against the fine."),
    ("what_is_challan",
     r"\bchallans?\b|e-?challans?|what is a fine notice",
     "A challan (e-challan) is the electronic fine notice issued for an offence. It records the vehicle, the offence, the amount and the due date, and appears under Fines & payments "
     "for the vehicle's owner, where it can be paid or disputed. Unpaid challans past their due date are marked overdue."),
    ("register_vehicle",
     r"(register|registration|add|link).{0,25}(vehicle|car|truck|bus|motorbike)|vehicle.{0,15}regist",
     "A vehicle is registered by RTSA staff (an officer or admin) under Vehicles, using the owner's NRC number and the vehicle details. Once registered it is linked to the owner's account, "
     "and the owner sees it under My vehicles with its insurance, fitness and permit status."),
    ("fitness",
     r"(fitness|roadworthy|roadworthiness|inspection).{0,30}(certificate|test|book|get|renew)?",
     "A vehicle needs a valid fitness (roadworthiness) certificate, obtained after passing an inspection at an RTSA-approved centre. Inspectors record the result under Inspections and the certificate then shows on the vehicle. "
     "A vehicle without a valid certificate is flagged during roadside and toll checks."),
    ("insurance",
     r"(insurance|insured|third.party|cover note).{0,40}",
     "A vehicle must carry valid insurance. Insurers' policies are recorded under Insurance and show on the vehicle, and the system flags vehicles with no valid policy at checks and toll gates. "
     "If you are uninsured, buy a policy from a licensed insurer and ask for it to be recorded. The Highway Code extract loaded here does not state the penalty, so ask RTSA."),
    ("report_accident",
     r"(report|reporting).{0,20}(accident|crash|collision)|(accident|crash|collision).{0,20}(report|happened)|hit (my|the) (car|vehicle)|someone hit|(rear|behind).{0,12}(hit|collid|ended)|crash(ed)? into",
     "After a crash: stop, switch on your hazard lights and make the scene safe; help anyone injured and call the emergency services. Do not move vehicles if someone is hurt. "
     "Exchange names, addresses, vehicle registrations and insurance details, take photos, and report it to the nearest police station as soon as reasonably practical (Rule 121). "
     "Tell your insurer. Who is at fault is decided by the police, courts and insurers, not by the Highway Code."),
    ("report_incident",
     r"(report|reporting).{0,20}(road )?(incident|hazard|obstruction|breakdown|pothole)",
     "To report a road incident (an obstruction, hazard or breakdown happening now), sign in, open Report road incident, choose what is happening and how serious it is, add details if you like, "
     "tick the declaration that it is true, and send. Your NRC number must be on your account. Nearby road alerts show under Road alerts."),
    ("tyre_burst",
     r"(tyre|tire).{0,15}(burst|blow|blew|puncture|flat)|blowout|burst.{0,6}(tyre|tire)",
     "If a tyre bursts: hold the steering wheel firmly and do not brake hard. Ease off the accelerator and let the car slow down, steer straight, then brake gently. "
     "Switch on hazard lights, move off the road if you can, and warn other traffic with a reflective triangle placed well behind you (Rule 124 requires a spare tyre, jack and triangles)."),
    ("u_turn",
     r"u.?turn",
     "The Highway Code extract loaded here does not give a specific rule on U-turns. As a rule of safety, only turn where it is clearly permitted by signs, with a clear view and no traffic coming; never on a bend, a hill crest or a busy highway. Obey road signs and markings, and ask RTSA if unsure."),
    ("min_age",
     r"(minimum|legal|how old|what age|age).{0,25}(drive|driving|licen[cs]e)|(\b1[0-7]\b|sixteen|seventeen)[ -]year.{0,12}(drive|car|licen)|can a .{0,12}year old drive",
     "The Highway Code extract loaded here does not state the minimum age for a licence. It does say learners must hold a provisional licence and be supervised by someone at least 21 with a valid licence "
     "and 3 years' driving experience (Rule 97). Ask RTSA for the exact minimum age for each licence class."),
    ("windscreen",
     r"(cracked|broken|damaged|chipped).{0,15}(windscreen|windshield|glass)",
     "The Highway Code extract loaded here only says to keep lights, windscreen and mirrors clean (Rule 290) and does not set a rule for cracks. A crack that blocks your view can fail a fitness inspection and is unsafe, so get it repaired."),
    ("toll",
     r"(pay|how).{0,20}toll|toll.{0,15}(pay|fee|gate|charge)",
     "Tolls are recorded when your plate or toll tag is read at a toll gate. At a barrier lane you pay at the gate; on a free-flow lane the charge is recorded against your vehicle "
     "and shows as due until it is paid. Toll history is on the Toll gates screen for staff and against your vehicle."),
    ("psv",
     r"\bpsv\b|public service vehicle|bus permit|taxi permit|minibus permit",
     "A PSV is a Public Service Vehicle: a bus, minibus or taxi that carries paying passengers. It needs a PSV permit tied to a licensed operator and an approved route. "
     "Officers manage operators and permits under PSV & routes, where each route also has a geo-fence that raises an alert if the bus strays too far from it."),
    ("contact",
     r"(contact|phone|email|call|office|where is).{0,15}(rtsa|you)|rtsa.{0,10}(office|contact)",
     "Use the contact details on the official RTSA website or visit your nearest RTSA office. For a specific case, such as a particular fine or licence, an officer can help once you sign in."),
]

_COMPILED = [(name, re.compile(p, re.IGNORECASE), text) for name, p, text in _ENTRIES]


def lookup(question: str) -> dict | None:
    """Return a ready answer in the assistant's response shape, or None when no reviewed answer applies."""
    for name, rx, text in _COMPILED:
        if rx.search(question):
            return {"mode": "guide", "topic": name, "answer": text, "sources": [], "disclaimer": GUIDE_NOTE}
    return None
