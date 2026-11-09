"""Simulated mobile money and card payments (app.services.sim_pay)."""
import pytest

from app.services import sim_pay


def test_mobile_money_outcomes():
    assert sim_pay.run("mobile_money", {"phone": "0961234567", "pin": "1234"})["ok"]
    r = sim_pay.run("mobile_money", {"phone": "260971234567", "pin": "0000"})
    assert not r["ok"] and "wrong PIN" in r["reason"] and r["meta"]["network"] == "Airtel"
    assert "insufficient" in sim_pay.run("mobile_money", {"phone": "0951234500", "pin": "1234"})["reason"]
    with pytest.raises(sim_pay.SimError):
        sim_pay.run("mobile_money", {"phone": "0911234567", "pin": "1234"})  # not a known network


def test_card_outcomes_and_validation():
    ok = {"expiry": "12/39", "cvv": "123", "name": "A B"}
    assert sim_pay.run("card", {**ok, "number": "4242 4242 4242 4242"})["ok"]
    assert "declined" in sim_pay.run("card", {**ok, "number": "4000000000000002"})["reason"]
    with pytest.raises(sim_pay.SimError):
        sim_pay.run("card", {**ok, "number": "4242424242424241"})  # fails Luhn
    with pytest.raises(sim_pay.SimError):
        sim_pay.run("card", {**ok, "expiry": "01/20", "number": "4242424242424242"})  # expired
    with pytest.raises(sim_pay.SimError) as e:
        sim_pay.run("card", {**ok, "number": "4000000000003220"})
    assert str(e.value).startswith(sim_pay.OTP_PREFIX)
    assert sim_pay.run("card", {**ok, "number": "4000000000003220", "otp": "123456"})["ok"]
    assert not sim_pay.run("card", {**ok, "number": "4000000000003220", "otp": "000000"})["ok"]
