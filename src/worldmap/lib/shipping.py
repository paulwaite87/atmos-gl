import logging

logger = logging.getLogger(__name__)

SPECIAL_VESSEL_TYPES = {
    0: "Vessel",
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

VESSEL_SUBCLASSES = {
    1: " HazA",
    2: " HazB",
    3: " HazC",
    4: " HazD"
}


class Ship:
    def __init__(self, vessel):
        self.vessel = vessel

        # mmsi
        self.mmsi = vessel.get("mmsi")

        # name
        name = vessel.get("name") or "Unknown"
        self.vessel_name = name.replace('"', "").strip()

        # type
        self.vessel_type = vessel.get("vessel_type") or 0

        # class
        if self.vessel_type in SPECIAL_VESSEL_TYPES:
            self.vessel_class =  SPECIAL_VESSEL_TYPES[self.vessel_type]
        else:
            class_digit = int(self.vessel_type // 10)
            self.vessel_class = VESSEL_CLASSES.get(class_digit, f"Vessel (Type {self.vessel_type})")

        # subclass
        sub_digit = self.vessel_type % 10
        self.vessel_subclass = VESSEL_SUBCLASSES.get(sub_digit, "")

    def get_expanded_vessel_class(self):
        """
        Expands the standard vessel classes to a more descriptive string, but
        only for Tankers and Passenger ships.
        Args:
            vessel: The vessel to find the expanded class for

        Returns: String expanded class, or the default standard class
        """
        length, beam = self.get_vessel_dimensions()

        # Deal with bogus beam data
        if beam > 70:
            beam = 0

        # Ships like the BOREAS (WTIV) have a length-to-beam ratio < 3.
        # Normal tankers/cargo are usually 5:1 or 6:1.
        if self.vessel_class == "Tanker":
            if length > 0 and beam > 0 and (length / beam) < 3.5:
                return "SPEC"
            elif length > 350 and beam > 58:
                return "ULTRA"
            elif length > 300:
                return "VLCC"
            elif length >= 220:
                return "STD"
            else:
                return self.vessel_class

        elif self.vessel_class == "Passenger":
            if length > 250:
                return "Mega Cruise"  # Floating shopping mall/theme park
            elif length > 190:
                return "Cruise"  # Standard Cruise Ship
            elif length > 50:
                return "Ferry"  # Small / Large Ferry
            else:
                return self. vessel_class

        return self.vessel_class

    def get_vessel_dimensions(self):
        length = int(self.vessel.get("length") or 0)
        beam = int(self.vessel.get("beam") or 0)
        return length, beam

    def get_vessel_navigational_status(self):
        cog = float(self.vessel.get("cog") or 0.0)
        sog = float(self.vessel.get("sog") or 0.0)
        nav_status = int(self.vessel.get("nav_status") or 1)
        return cog, sog, nav_status

    def get_vessel_position(self):
        lat = self.vessel.get("lat")
        lon = self.vessel.get("lon")
        return (float(lat) if lat is not None else None,
                float(lon) if lon is not None else None)

    def get_vessel_heading_str(self):
        """
        Converts Course Over Ground (COG) into a cardinal direction string.
        """
        cog, _, _ = self.get_vessel_navigational_status()

        # Normalize COG to be within [0, 360)
        cog = cog % 360

        # Define the 8 cardinal directions in order
        directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']

        # Each sector is 45 degrees. We add 22.5 to 'center' the
        # first sector (North) around 0 degrees.
        index = int((cog + 22.5) // 45)

        # Use modulo 8 to wrap 337.5 - 360 back to index 0 (North)
        return directions[index % 8]

    def get_vessel_directional_icon(self):
        """Returns what is essentially the filename of the icon eg. 'red_ship_NW.png'"""
        vessel_heading = self.get_vessel_heading_str()
        if self.vessel_class == "Tanker":
            icon_colour = "red"
        elif self.vessel_class == "Cargo":
            icon_colour = "green"
        else:
            icon_colour = "purple"
        return f"{icon_colour}_ship_{vessel_heading}.png"

    def get_vessel_disc_icon(self):
        suffix = "_empty.png" if self.is_unloaded() else ".png"
        if self.vessel_class == "Tanker":
            return f"ship_tanker{suffix}"
        elif self.vessel_class == "Cargo":
            return f"ship_cargo{suffix}"
        else:
            return f"ship{suffix}"

    def get_vessel_description(self):
        """This function returns the vessel description which starts with the
        name of the ship and then the class and subclass information"""
        return f"{self.vessel_name} {self.vessel_class}{self.vessel_subclass}"

    def get_vessel_colour(self):
        ship_expanded_class = self.get_expanded_vessel_class()
        if ship_expanded_class == "ULTRA":
            return "DeepPink"
        elif ship_expanded_class == "VLCC":
            return "Red"
        else:
            if self.vessel_class == "Cargo":
                return "Green"
            elif self.vessel_class == "Passenger":
                return "Purple"
            else:
                return "Yellow"

    def is_unloaded(self):
        """Determine whether a ship has been unloaded partially or fully"""
        current_draught = float(self.vessel.get("draught") or 0.0)
        draught_threshold = float(self.vessel.get("prev_draught") or 0.0) * 0.9
        return  0.0 < current_draught < draught_threshold > 0.0

    def is_underway(self) -> bool:
        """
        Returns True if vessel is moving faster than 1 knot
        and is not anchored (1) or moored (5).
        """
        # 0 = Under way using engine, 8 = Under way sailing
        # 1 = Anchored, 5 = Moored
        cog, sog, nav_status = self.get_vessel_navigational_status()
        return sog > 1.0 and nav_status not in [1, 5]
