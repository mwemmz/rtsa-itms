"""The kinds of motor policy an insurer can issue, and what each is for."""

POLICY_TYPES: dict[str, dict] = {
    "third_party": {
        "label": "Third-party only",
        "covers": "Injury or damage you cause to other people and their property. Nothing for your own vehicle.",
        "required_for": "The legal minimum for every vehicle on a Zambian road.",
    },
    "third_party_fire_theft": {
        "label": "Third-party, fire and theft",
        "covers": "Everything in third-party, plus your own vehicle if it is stolen or burns.",
        "required_for": "Optional. Common for financed vehicles.",
    },
    "comprehensive": {
        "label": "Comprehensive",
        "covers": "Third-party, fire and theft, plus accident damage to your own vehicle.",
        "required_for": "Optional. Lenders often require it.",
    },
    "psv_passenger": {
        "label": "PSV passenger liability",
        "covers": "Death or injury to fare-paying passengers on a bus, minibus or taxi.",
        "required_for": "Required before a PSV permit is issued or renewed.",
    },
    "goods_in_transit": {
        "label": "Goods-in-transit (haulage)",
        "covers": "Loss or damage to cargo on a truck, plus third-party liability.",
        "required_for": "Required for commercial haulage operators.",
    },
    "comesa_yellow_card": {
        "label": "COMESA Yellow Card",
        "covers": "Third-party cover when driving into other COMESA member states.",
        "required_for": "Required for cross-border trips, in addition to a local policy.",
    },
}
