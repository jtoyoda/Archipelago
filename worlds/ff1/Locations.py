import json
from typing import Dict, NamedTuple, List, Optional

from BaseClasses import Region, RegionType, MultiWorld, Location

EventId: Optional[int] = None


class LocationData(NamedTuple):
    name: str
    address: int


class FF1Locations:
    _location_table: List[LocationData] = []
    _location_table_lookup: Dict[str, LocationData] = {}

    def _populate_item_table_from_data(self):
        with open('worlds/ff1/data/locations.json') as file:
            locations = json.load(file)
            # Hardcode progression and categories for now
            self._location_table = [LocationData(name, code) for name, code in locations.items()]
            self._location_table_lookup = {item.name: item for item in self._location_table}

    def _get_location_table(self) -> List[LocationData]:
        if not self._location_table or not self._location_table_lookup:
            self._populate_item_table_from_data()
        return self._location_table

    def _get_location_table_lookup(self) -> Dict[str, LocationData]:
        if not self._location_table or not self._location_table_lookup:
            self._populate_item_table_from_data()
        return self._location_table_lookup

    def get_location_name_to_address_dict(self) -> Dict[str, int]:
        return {name: location.address for name, location in self._get_location_table_lookup().items()}

    @staticmethod
    def create_menu_region(player: int, locations: Dict[str, int],
                           rules: Dict[str, List[List[str]]]) -> Region:
        menu_region = Region("Menu", RegionType.Generic, "Menu", player)
        for name, address in locations.items():
            location = Location(player, name, address, menu_region)
            if name in rules:
                rules_list = rules[name]
                location.access_rule = generate_rule(rules_list, player)
            menu_region.locations.append(location)

        return menu_region


def generate_rule(rules_list, player):
    def x(state):
        for rule in rules_list:
            current_state = True
            for item in rule:
                if not state.has(item, player):
                    current_state = False
                    break
            if current_state:
                return True
        return False
    return x
