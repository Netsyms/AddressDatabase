# Created on : Aug 29, 2024, 12:57:40 AM
# Author     : Skylar Ittner

import re

import pandas as pd
from pythonnet import load
from scourgify import NormalizeAddress, normalize_address_record
from src.zipfunctions import checkZIPCode, getZIP, getZIP4, getCityStateForZIP
from src.constants import STATES, LONGHAND_STREET_TYPES, UNITS, ValidationException
import src.config
import json
import sys

load("mono")
import clr

clr.AddReference("KellermanSoftware.USPSStandardization")
from KellermanSoftware.USPSStandardization import StandardizationLogic

standardization = False
try:
    standardization = StandardizationLogic("Netsyms Technologies LLC 203206", "2;770AE30D7A5F217E77C857B29618A6E8DD")
except:
    print("Kellerman USPSStandardization failed to initialize, skipping.")


zipcodes = pd.read_csv("zip_code_database.csv", keep_default_na=False, dtype="str")

PRE_STANDARDIZATION_STREET_REGEXES = {
    " POINTADDRESS$": "",
    "S U S HWY ": "S US HIGHWAY ",
    "^U S ([0-9]+) HWY": r"US HIGHWAY \1",
    "^U S HWY ": "US HIGHWAY ",
    "[–—−]": "-",
    " PW$": " PKWY",
    " VIS ": " VISTA ",
    " VLY ": " VALLEY ",
    " MTN ": " MOUNTAIN ",
    " CTR ": " CENTER ",
    " CLB ": " CLUB ",
    "HBR ": "HARBOR ",
    "^PNE ": "PINE ",
    "^SPG ": "SPRING ",
    "^M L KING JR ": "MARTIN LUTHER KING JR ",
    "^NONE$": "",
    " VLY ": " VALLEY ",
    "^VLY ": "VALLEY ",
    "BEN-DIER": "BEN DIER",
    " ROCK RIV$": "" # Albany county WY misspelled their own city name and put it in the street field
}

POST_STANDARDIZATION_STREET_REGEXES = {
    ", BASE$": "",
    ", BASE CP$": "",
    "UNITED STATES HWY ([0-9]+)": r"US HIGHWAY \1",
    "^U.S. HWY ": "US HIGHWAY ",
    "^U.S. HIGHWAY ": "US HIGHWAY ",
    "^U S ([0-9]+) HWY": r"US HIGHWAY \1",
    "^US ([0-9]+)": r"US HIGHWAY \1",
    " US HWY ([0-9]+)": r" US HIGHWAY \1",
    "UNITED STATES FOREST SERVICE ROAD ([0-9]+) RD$": r"FOREST SERVICE ROAD \1",
    "^IH?([0-9]{1,3})(\s|$)": r"INTERSTATE \1\2",
    "^INTERSTATE HWY ([0-9]{1,3})(\s|$)": r"INTERSTATE \1\2",
    "^I ?([0-9]{1,3})(\s|$)": r"INTERSTATE \1\2",
    "^I-([0-9]{1,3})$": r"INTERSTATE \1",
    "^([EW]) I-([0-9]{1,3})$": r"\1 INTERSTATE \2",
    "^HWY FM ([0-9]+)": r"FM \1",
    "^FARM TO MARKET ([0-9]+)": r"FM \1",
    " (HIWAY) ([0-9]+)$": r" HWY \2",
    " (RTE|RT) ([0-9]+)$": r" ROUTE \2",
    " RD ([0-9]+)$": r" ROAD \1",
    "^ST (HIGHWAY|ROUTE|ROAD|HWY) ([0-9]+)$": r"STATE \1 \2",
    "^CNTY (HIGHWAY|ROUTE|ROAD|HWY) ([0-9]+)$": r"COUNTY \1 \2",
    "^CR ([0-9]+)": r"COUNTY ROAD \1",
    "^COUNTY RD ([0-9]+) ([NSEW]{1,2})": r"COUNTY ROAD \1 \2",
    "^(SR|ST RD) ([0-9]+)": r"STATE ROAD \2",
    "^(ST RT|ST RTE) ([0-9]+)": r"STATE ROUTE \2",
    "^(HWY|HIWAY) ([0-9]+)": r"HIGHWAY \2",
    "^(RTE|RT) ([0-9]+)": r"ROUTE \2",
    "^RD ([0-9]+)": r"ROAD \1",
    "^TSR ([0-9]+)": r"TOWNSHIP ROAD \1",
    "([0-9]+) BYP RD": r"\1 BYPASS RD",
    "([0-9]+) BYPASS": r"\1 BYP",
    " HIGHWAY ([0-9]+)": r" HWY \1",
    "^(STATE|COUNTY) HWY ": r"\1 HIGHWAY ",
    "UNITED STATES HWY ([0-9]+)": r"US HIGHWAY \1",
    "UNITED STATES HWY ": "US HIGHWAY ",
    "^US ([0-9]+)": r"US HIGHWAY \1",
    " US HWY ([0-9]+)": r" US HIGHWAY \1",
    "^US HWY ": r"US HIGHWAY ",
    "^FIRST ": "1ST ",
    "^SECOND ": "2ND ",
    "^THIRD ": "3RD ",
    "^FOURTH ": "4TH ",
    "^FIFTH ": "5TH ",
    "^SIXTH ": "6TH ",
    "^SEVENTH ": "7TH ",
    "^EIGHTH ": "8TH ",
    "^NINTH ": "9TH ",
    "^TENTH ": "10TH ",
    " STREET ST$": " ST",
    " AVENUE AVE$": " AVE",
    " DRIVE DR$": " DR",
    " DR DRIVE$": " DR",
    " PARKS PARK$": " PARK",
    " ROAD RD$": " RD",
    " LK ": " LAKE ",
    " ST ST$": " ST",
    "^(N|S|E|W) COUNTY (ROAD|RD) ([0-9]{3,}) (N|S|E|W)$": r"\1 \3 \4", # Indiana has "County Road" in NAD as the street name for some reason
    "^COUNTY RD COUNTY ROAD ": "COUNTY ROAD ",
    " CI$": " CIR",
    " CM$": " CMN",
    " BL$": " BLVD",
    " TE$": " TER",
    " LP$": " LOOP",
    "^CRK ([0-9]+)$": r"COUNTY ROAD \1", # Athens TX does this for some reason
    "^PR ([0-9]+)$": r"PRIVATE ROAD \1", # Athens TX does this for some reason
    "^SH ([0-9]+) ([NSEW])$": r"STATE HIGHWAY \1 \2" # Athens TX does this too for some reason
}

STANDARDIZATION_NUMBER_REGEXES = {
    "^([0-9]+) \1$": r"\1", # Fix address numbers that repeat with a space between (doesn't always work for some reason)
    "^([0-9]+) ([A-Z])$": r"\1\2", # "1234 A ROAD ST" to "1234A ROAD ST"
    "^0$": "", # Blank out 0 as a house number
    "^\.$": "", # Blank out .
}

ABBREV_PATTERN = ""
ABBREV_PATTERN_LIST = []
for (a, b) in LONGHAND_STREET_TYPES.items():
    ABBREV_PATTERN_LIST.append(a)
ABBREV_PATTERN = "|".join(ABBREV_PATTERN_LIST)
STREET_INNER_ABBREV_FIND_REGEX = re.compile("(^|\s)("+ABBREV_PATTERN+") ("+ABBREV_PATTERN+")( [NSEW]{0,2})?$")

def postStandardizeStreet(street):
    # Catch edge cases with USPS formatting
    for (find, replace) in POST_STANDARDIZATION_STREET_REGEXES.items():
        street = re.sub(find, replace, street)

    # Unshorten things like "S CRK RD", correcting to "S CREEK RD"
    matches = STREET_INNER_ABBREV_FIND_REGEX.search(street)
    if matches:
        street = street.replace(matches.group(2), LONGHAND_STREET_TYPES[matches.group(2)], 1)

    # "KY 1234" to "KY HIGHWAY 1234" per Pub 28
    if re.match(r"^[A-Z]{2} [0-9]+$", street):
        for (full, abbr) in STATES.items():
            if street.startswith(abbr + " "):
                street = street.replace(abbr, abbr + " HIGHWAY", 1)
                break
    # "KENTUCKY STATE HIGHWAY 625" to "KY STATE HIGHWAY 625" per Pub 28
    if re.match(r"^[A-Z]{2,} STATE HIGHWAY [0-9]+", street):
        for (full, abbr) in STATES.items():
            if street.startswith(full + " "):
                street = street.replace(full, abbr, 1)
                break

    return street.strip()

def preStandardizeStreet(street):
    for (find, replace) in PRE_STANDARDIZATION_STREET_REGEXES.items():
        street = re.sub(find, replace, street)
    # Remove unit from end of street
    hashtag = street.find("#")
    if hashtag > 0:
        street = street[:hashtag].strip()
    return street

def standardizeNumber(number):
    for (find, replace) in STANDARDIZATION_NUMBER_REGEXES.items():
        number = re.sub(find, replace, number)
    # Detect "1234 1234" which some sources have sometimes (like Kentucky NAD v20)
    if (parts := number.split(" ")) and len(parts) == 2 and parts[0] == parts[1]:
        number = parts[0]
    return number.strip()

def splitNumberAndUnit(number):
    # Some places have unit numbers in the primary address, remove them
    num = number
    unit = ""
    for label in UNITS:
        pos = number.find(label)
        if pos > 0:
            num = number[:pos]
            unit = number[pos:]
            break
    num = standardizeNumber(num)
    unit = removeUnitText(unit)
    return num, unit

def removeUnitText(subaddr):
    if subaddr == None:
        return subaddr
    subaddr = subaddr.upper()
    for label in UNITS:
        subaddr = subaddr.replace(label, "")
    subaddr = subaddr.replace("  ", " ")
    return subaddr.strip()

def normalizeAddress(number, street, unit, city, state, zipcode, lat, lon, zipprefix=False, plus4="", county=False):
    cfg = src.config.get_config()
    if not number:
        #print("W: No address number for address, skipping "+street)
        raise ValidationException("No address number")
    if not street:
        #print("W: No street for address, skipping")
        raise ValidationException("No address number")
    # Detect flipped coordinates
    if lat < -90 or lat > 90:
        lon, lat = lat, lon
    number = standardizeNumber(str(number).upper().strip())
    street = preStandardizeStreet(street.strip().upper())
    unit = unit.strip().upper()
    city = city.strip().upper()
    state = state.strip().upper()
    zipcode = (zipcode or "").strip()
    plus4 = plus4.strip()

    if (not city or city == "") and (not zipcode or zipcode == "") and cfg.citySuggestion:
        # Use the city specified on the CLI, hopefully it'll help
        city = cfg.citySuggestion

    if unit == "":
        number, unit = splitNumberAndUnit(number)        

    city = city.replace("CITY OF ", "").replace("TOWN OF ", "").replace("VILLAGE OF ", "")
    city = city.replace("UNINCORPORATED", "")

    #
    # Standardize address
    #
    try:
        # Python library
        addr = normalize_address_record(
            {
                "address_line_1": "".join(["999999999", " ", street]),
                "address_line_2": unit,
                "city": city,
                "state": state,
                "postal_code": zipcode
            }
        )
    except Exception as e:
        try:
            # Proprietary Mono library
            if standardization == False:
                raise e
            addr = {
                "address_line_1": "999999999 " + standardization.StandardizeStreetAddress(street),
                "address_line_2": unit,
                "city": city,
                "state": state,
                "postal_code": zipcode
            }
        except Exception as ex:
            # This basically never happens
            print("W: Couldn't parse address:")
            print(ex)
            raise ex

    #
    # Remove number from street address field
    #
    addr['address_line_1'] = addr['address_line_1'].replace("999999999", number)
    streetonly = addr['address_line_1']
    if streetonly.startswith(str(number) + " "):
        streetonly = streetonly[len(str(number) + " "):]
    #
    # Run extra regexes on street to fix standardization problems
    #
    streetonly = postStandardizeStreet(streetonly)        

    #
    # Special conditional rules
    #
    if addr["state"] == "PR":
        # Puerto Rico special rules
        if re.match("[A-Z]", number):
            number = number.replace("-", "")

    #
    # Clean second line
    #
    addr['address_line_2'] = removeUnitText(addr['address_line_2'])

    #
    # Standardize and validate and/or append ZIP Code
    #
    zipcode = addr["postal_code"]
    unitprefix = ""
    unit = addr['address_line_2']

    if zipcode is not None:
        zipcode = addr["postal_code"][0:5]

    # Skip these if we already have a ZIP+4 code, assume it's accurate
    if zipcode is not None and len(zipcode) == 5 and not plus4:
        zipinfo = getCityStateForZIP(zipcode)
        if cfg.appendPlus4 or zipinfo == False or addr["state"] != zipinfo["state"] or (cfg.appendUnitLabel and addr["address_line_2"] != ""):
            zipcode, plus4, streetonly, unitprefix, unit = getZIP4(number, streetonly, addr['address_line_2'], addr["state"], lat, lon, addr["city"], zipcode, county)
            zipinfo = getCityStateForZIP(zipcode)
            if zipinfo != False:
                addr["city"] = zipinfo["city"]
                addr["state"] = zipinfo["state"]
        else:
            addr["city"] = zipinfo["city"]
            addr["state"] = zipinfo["state"]
    elif not plus4:
        zipcode, plus4, streetonly, unitprefix, unit = getZIP4(number, streetonly, addr['address_line_2'], addr["state"], lat, lon, addr["city"], False, county)
        zipinfo = getCityStateForZIP(zipcode)
        if zipinfo != False:
            addr["city"] = zipinfo["city"]
            addr["state"] = zipinfo["state"]
    
    if not plus4 and streetonly == "UNITED STATES HWY" and re.match(r"^\d+$", unit):
        streetonly = f"US HIGHWAY {unit}"
        unit = ""

    #if not src.config.appendPlus4:
    #    plus4 = ""        

    return {
        "number": number,
        "street": streetonly,
        "unit": ' '.join(filter(None, (unitprefix, unit))),
        "city": addr["city"],
        "state": addr["state"],
        "zip": zipcode,
        "plus4": plus4,
        "latitude": lat,
        "longitude": lon
    }
