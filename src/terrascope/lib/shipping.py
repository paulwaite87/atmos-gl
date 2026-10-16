import logging

logger = logging.getLogger(__name__)

SPECIAL_VESSEL_TYPES = {
    0: "Other",
    30: "Fishing Vessel",
    31: "Towing Vessel",
    32: "Towing (Large/Towed)",
    33: "Dredging/Underwater Ops",
    34: "Diving Ops",
    35: "Military Ops",
    36: "Sailing Vessel",
    37: "Pleasure Craft",
    50: "Pilot Vessel",
    51: "Search and Rescue (SAR)",
    52: "Tug",
    53: "Port Tender",
    54: "Anti-Pollution Equipment",
    55: "Law Enforcement",
    58: "Medical Transport",
    59: "Non-Combatant (Neutral State)",
}

VESSEL_CLASSES = {
    1: "WIG (Wing In Ground)",
    2: "WIG (Wing In Ground)",
    4: "High Speed Craft",
    6: "Passenger",
    7: "Cargo",
    8: "Tanker",
    9: "Other",
}

VESSEL_SUBCLASSES = {1: " HazA", 2: " HazB", 3: " HazC", 4: " HazD"}


def get_vessel_class_from_type(vessel_type):
    if vessel_type in SPECIAL_VESSEL_TYPES:
        vessel_class = SPECIAL_VESSEL_TYPES.get(vessel_type)
    else:
        class_digit = int(vessel_type // 10)
        vessel_class = VESSEL_CLASSES.get(class_digit, "Other")
    return vessel_class


def get_vessel_classes_list():
    all_classes = list(SPECIAL_VESSEL_TYPES.values()) + list(VESSEL_CLASSES.values())
    return list(dict.fromkeys(all_classes))
