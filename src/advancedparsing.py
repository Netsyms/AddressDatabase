# Use "AI" to parse problem addresses and find more matches
# First expand the address to possible forms, then normalize each one, and keep the one that has a ZIP+4
 
from postal.parser import parse_address
from postal.expand import expand_address
from src.addressfunctions import normalizeAddress
import re
 
def advancedNormalize(number, street, unit, city, state, zipcode, lat, lon, zipprefix=False, plus4 = ""):
    if len(plus4 or "") == 4:
        # Return as-is, it's got a +4 match already
        return {
            "number": number,
            "street": street,
            "unit": unit,
            "city": city,
            "state": state,
            "zip": zipcode,
            "plus4": plus4,
            "latitude": lat,
            "longitude": lon
        }
    
    # Merge and re-split the address to catch odd things like the street having the city and zip too
    parsed = parse_address(f"{number} {street}, {city} {state} {zipcode}")
    pNumber = number
    pStreet = street
    pUnit = unit
    pCity = city
    pState = state
    pZip = zipcode
    for part in parsed:
        if part[1] == "house_number" and (pNumber == "" or pNumber == number): # Don't overwrite it with values found later, which might be a zip code or something
            pNumber = part[0].upper()
        elif part[1] == "road":
            pStreet = part[0].upper()
        elif part[1] == "unit":
            pUnit = part[0].upper()
        elif part[1] == "city":
            pCity = part[0].upper()
        elif part[1] == "state":
            pState = part[0].upper()
        elif part[1] == "postcode":
            pZip = part[0].upper()
    
    # Expand the number/street to all possible forms
    expanded = expand_address(f"{pNumber} {pStreet}")
    
    normalizedMatches = []
    # Add the original address as a candidate so if no better matches come up, it'll probably just use it as-is
    normalizedMatches.append({
        "number": number,
        "street": street,
        "unit": unit,
        "city": city,
        "state": state,
        "zip": zipcode,
        "plus4": plus4,
        "latitude": lat,
        "longitude": lon
    })
    # Also add one where we remove any non-numeric data from the number and unit fields
    normalizedMatches.append(normalizeAddress(re.sub("[^0-9]", "", number), street, re.sub("[^0-9]", "", unit), pCity, pState, pZip, lat, lon, zipprefix, plus4))
    if number != pNumber or unit != pUnit or street != pStreet:
        normalizedMatches.append(normalizeAddress(re.sub("[^0-9]", "", pNumber), pStreet, re.sub("[^0-9]", "", pUnit), pCity, pState, pZip, lat, lon, zipprefix, plus4))
    
    for exp in expanded:
        parsed = parse_address(exp)
        pN = ""
        pS = ""
        pU = ""
        for part in parsed:
            if part[1] == "house_number" and pN == "":
                pN = part[0]
            elif part[1] == "road":
                pS = part[0]
            elif part[1] == "unit":
                pU = part[0]
        try:
            normalizedMatches.append(normalizeAddress(pN, pS, pU, pCity, pState, pZip, lat, lon, zipprefix, plus4))
        except Exception as e:
            pass
    
    
    if len(normalizedMatches) > 1:
        weights = {"number": 5, "street": 5, "unit": 1, "city": 1, "state": 1, "zip": 3, "plus4": 8, "latitude": 0, "longitude": 0}
        sortedMatches = sorted(
            normalizedMatches,
            key=lambda item: sum(weights[k] for k, v in item.items() if v),
            reverse=True
        )
        return sortedMatches[0]
    elif len(normalizedMatches) == 1:
        return normalizedMatches[0]
    
    # No matches, give up on the whole thing
    return {
        "number": number,
        "street": street,
        "unit": unit,
        "city": city,
        "state": state,
        "zip": zipcode,
        "plus4": plus4,
        "latitude": lat,
        "longitude": lon
    }
    
    
