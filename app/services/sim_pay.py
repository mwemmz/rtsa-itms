"""Simulated mobile money and card payments (no real money moves, no real network is contacted).

It behaves like a gateway would, so the whole flow can be demonstrated and tested:

Mobile money (MTN, Airtel, Zamtel): the phone number picks the network, then the
"phone prompt" asks for a PIN.
    PIN 1234            approved
    any other PIN       "Wrong PIN"
    number ending 00    "Insufficient funds" (whatever the PIN)

Card (Visa / Mastercard test numbers):
    4242 4242 4242 4242   approved
    5555 5555 5555 4444   approved
    4000 0000 0000 3220   asks for a bank code (3-D Secure style); the code is 123456
    4000 0000 0000 0002   declined by the bank
    4000 0000 0000 9995   insufficient funds
    any other number      "Card not recognised"; a number failing the Luhn check is rejected outright.
Nothing typed here (PIN, card number, CVV) is stored: only the network, the last four digits and the result.
"""

import re
from datetime import datetime

NETWORKS = {"MTN": ("096", "076"), "Airtel": ("097", "077"), "Zamtel": ("095", "075")}
TEST_PIN = "1234"
TEST_OTP = "123456"
OTP_PREFIX = "OTP_REQUIRED:"


class SimError(ValueError):
    """A problem with what was typed (shown to the user, nothing is charged)."""


def normalise_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("260") and len(digits) == 12:
        digits = "0" + digits[3:]
    if len(digits) == 9:
        digits = "0" + digits
    return digits


def network_for(phone: str) -> str | None:
    for name, prefixes in NETWORKS.items():
        if phone[:3] in prefixes:
            return name
    return None


def luhn_ok(number: str) -> bool:
    total, alt = 0, False
    for ch in reversed(number):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def card_brand(number: str) -> str:
    if number.startswith("4"):
        return "Visa"
    if number[:2] in {"51", "52", "53", "54", "55"} or 2221 <= int(number[:4] or 0) <= 2720:
        return "Mastercard"
    return "Card"


def run(method: str, d: dict) -> dict:
    """Return {ok, reason, label, meta}. Raises SimError for bad input; SimError(OTP_PREFIX...) when a code is needed."""
    d = d or {}
    if method == "mobile_money":
        phone = normalise_phone(d.get("phone", ""))
        if len(phone) != 10 or not phone.startswith("0"):
            raise SimError("Enter a Zambian mobile number, like 0961234567.")
        net = network_for(phone)
        if not net:
            raise SimError("That number isn't on MTN (096/076), Airtel (097/077) or Zamtel (095/075).")
        meta = {"network": net, "phone_last4": phone[-4:]}
        label = f"{net} Mobile Money ****{phone[-4:]}"
        pin = str(d.get("pin", ""))
        if not re.fullmatch(r"\d{4}", pin):
            raise SimError("Enter your 4-digit mobile money PIN.")
        if phone.endswith("00"):
            return {"ok": False, "reason": f"{net}: insufficient funds in the wallet.", "label": label, "meta": meta}
        if pin != TEST_PIN:
            return {"ok": False, "reason": f"{net}: wrong PIN.", "label": label, "meta": meta}
        return {"ok": True, "reason": None, "label": label, "meta": meta}

    if method == "card":
        number = re.sub(r"\D", "", d.get("number", ""))
        if not 13 <= len(number) <= 19 or not luhn_ok(number):
            raise SimError("That card number isn't valid. Check the digits.")
        m = re.fullmatch(r"(\d{2})\s*/\s*(\d{2})", str(d.get("expiry", "")).strip())
        if not m or not 1 <= int(m.group(1)) <= 12:
            raise SimError("Enter the expiry as MM/YY.")
        now = datetime.utcnow()
        if (2000 + int(m.group(2)), int(m.group(1))) < (now.year, now.month):
            raise SimError("That card has expired.")
        if not re.fullmatch(r"\d{3,4}", str(d.get("cvv", ""))):
            raise SimError("Enter the 3-digit security code on the back.")
        if not str(d.get("name", "")).strip():
            raise SimError("Enter the name on the card.")
        brand = card_brand(number)
        meta = {"brand": brand, "card_last4": number[-4:]}
        label = f"{brand} ****{number[-4:]}"
        if number == "4000000000003220":
            if str(d.get("otp", "")).strip() != TEST_OTP:
                if not d.get("otp"):
                    raise SimError(OTP_PREFIX + "Your bank sent a code to confirm this payment. Demo code: " + TEST_OTP)
                return {"ok": False, "reason": "Bank verification failed: wrong code.", "label": label, "meta": meta}
            return {"ok": True, "reason": None, "label": label, "meta": meta}
        if number in ("4242424242424242", "5555555555554444"):
            return {"ok": True, "reason": None, "label": label, "meta": meta}
        if number == "4000000000000002":
            return {"ok": False, "reason": "Card declined by the issuing bank.", "label": label, "meta": meta}
        if number == "4000000000009995":
            return {"ok": False, "reason": "Card declined: insufficient funds.", "label": label, "meta": meta}
        return {"ok": False, "reason": "Card not recognised by the demo gateway. Use a test card.", "label": label, "meta": meta}

    raise SimError("Choose mobile money or card.")
