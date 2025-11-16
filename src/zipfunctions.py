# Created on : Aug 29, 2024, 12:57:40 AM
# Author     : Skylar Ittner

import re, sys
#import pandas as pd
from uszipcode import SearchEngine, ZipcodeTypeEnum
from shapely.geometry import Point
from shapely.geometry.polygon import Polygon
import sqlite3
from src.constants import LONGHAND_STREET_TYPES
import src.config
import sys
import urllib.request
from urllib.parse import quote
import json

#zipcodes = pd.read_csv("zip_code_database.csv", keep_default_na=False, dtype="str")
fastsearch = SearchEngine(db_file_path="zipcode_db_simple.sqlite", simple_or_comprehensive=SearchEngine.SimpleOrComprehensiveArgEnum.simple)
search = SearchEngine(db_file_path="zipcode_db.sqlite", simple_or_comprehensive=SearchEngine.SimpleOrComprehensiveArgEnum.comprehensive)

zip4 = sqlite3.connect("file:/home/skylar/AddressDatabase/zip4.sqlite?mode=ro&immutable=1", uri=True, check_same_thread=False)
zip4.executescript("""
PRAGMA query_only=ON;            -- belt-and-suspenders (can’t write)
PRAGMA temp_store=MEMORY;        -- sorts/temps in RAM
PRAGMA cache_size=-800000;       -- ~800 MB page cache (negative = KB units)
PRAGMA mmap_size=4294967296;     -- 1 GiB memory-mapped I/O (bump if you have RAM)
PRAGMA automatic_index=ON;       -- leave enabled (default), can help odd joins
PRAGMA threads=4;                -- allow parallel ops for sorts/expr eval (if available)
""")
zip4.row_factory = sqlite3.Row
cur = zip4.cursor()

# SQL query cache when finding ZIP+4, prevents running duplicate queries for nearby addresses on the same road
querycache = {}
querycachelimit = 3000

def checkZIPCode(lat, lon, state, zip):
    zipok = False
    if not zip or zip == None or zip != zip:
        zipok = False
    elif isinstance(zip, str):
        if len(zip) == 5:
            zipok = True
        else:
            zip = zip.rjust(5, '0')
    elif isinstance(zip, int):
        if zip >= 10000:
            zipok = True
        else:
            zip = str(int(zip)).rjust(5, '0')
            zipok = True
    elif isinstance(zip, float):
        if zip >= 10000:
            zip = int(zip)
            zipok = True
        else:
            zip = str(int(zip)).rjust(5, '0')
            zipok = True
    else:
        zip = str(int(zip)).rjust(5, '0')
        zipok = True

    zipInfo = False
    if zipok:
        zipInfo = getCityStateForZIP(zip)
        if not zipInfo:
            zipok = False
        elif zipInfo.state != state:
            zipok = False

    if not zipok:
        result = search.by_coordinates(lat = lat, lng = lon, returns = 1)
        if len(result) == 1:
            return getCityStateForZIP(result[0].zipcode)
        elif len(result) > 1:
            print(result[0])
            print(result[1])
            return getCityStateForZIP(result[0].zipcode)
        else:
            return {"zip": "", "city": "", "state": ""}
    else:
        return zipInfo

def getCityStateForZIP(zipcode):
    if not zipcode or zipcode == False or len(zipcode) != 5:
        return False
    cur.execute("SELECT ZipCode,City,State FROM ZIPCodes WHERE ZipCode='"+zipcode+"' AND CityStateKey=PreferredLastLineKey LIMIT 1")
    results = cur.fetchall()
    if len(results) == 0:
        return False
    return {
        "zip": results[0]["ZipCode"],
        "city": results[0]["City"],
        "state": results[0]["State"]
    }

def getZIPFromGeo(lat, lon, prefix=False, state=False):
    if prefix and state:
        result = search.query(lat = lat, lng = lon, returns = 1, radius=20, prefix=str(prefix), state=state)
    elif state:
        result = search.query(lat = lat, lng = lon, returns = 1, radius=20, state=state)
    elif prefix:
        result = search.query(lat = lat, lng = lon, returns = 1, radius=20, prefix=str(prefix))
    else:
        result = search.by_coordinates(lat = lat, lng = lon, returns = 1)
    if len(result) == 1:
        return result[0].zipcode
    elif len(result) > 1:
        #print("W: Multiple ZIP matches for "+lat+", "+lon+": "+result[0] + " or " + result[1])
        return result[0].zipcode
    else:
        return None

def subaddrMatchRows(rows, unit):
    if re.match(r"^[0-9]+$", unit):
        unit = unit.zfill(8)
    unitrows = []
    nounitrows = []
    for row in rows:
        if row["AddressSecLowNumber"] == "":
            nounitrows.append(row)
            continue
        if row["AddressSecLowNumber"] <= unit and row["AddressSecHighNumber"] >= unit:
            unitrows.append(row)
    if len(unitrows) == 0:
        return nounitrows
    return unitrows

def getZIPsForCityState(city, state):
    city = city.upper().strip().replace("'","''")
    cur.execute("SELECT * FROM ZIPCodes WHERE State = '" + state + "' AND (CityAliasName = '" + city + "' OR City = '" + city + "' OR CityAliasAbbreviation = '" + city + "')")
    citylist = cur.fetchall()
    resultlist = []
    ziplist = []
    # Remove entries that aren't the preferred city name for the ZIP Code
    for cityrow in citylist:
        if cityrow["CityStateKey"] == cityrow["PreferredLastLineKey"]:
            resultlist.append(cityrow)
            ziplist.append(cityrow["ZipCode"])
    return resultlist, ziplist

def getZIPsForCounty(county, state):
    county = county.upper().strip().replace("'","''")
    cur.execute("SELECT ZipCode FROM ZIPCodes WHERE State = '" + state + "' AND County = '" + county + "'")
    countylist = cur.fetchall()
    # Also get records where the ZIP isn't mainly in the county but some of it is
    cur.execute("SELECT ZipCode FROM ZIPCodesMultiCounty WHERE State = '" + state + "' AND County = '" + county + "'")
    multicountylist = cur.fetchall()
    ziplist = []
    
    for row in countylist:
        if row["ZipCode"] not in ziplist:
            ziplist.append(row["ZipCode"])
    for row in multicountylist:
        if row["ZipCode"] not in ziplist:
            ziplist.append(row["ZipCode"])
    return ziplist

def addressRangeContainsNumber(low, high, evenodd, number):
    numberevenodd = "B"
    if re.match(r"^[0-9]+$", number):
        if int(number) % 2 == 0:
            numberevenodd = "E"
        else:
            numberevenodd = "O"
        number = number.zfill(10)
    elif low == number or high == number:
        return True
    
    if evenodd == "B" or evenodd == numberevenodd:
        if low <= number and high >= number: # This logic is bad and should be rewritten
            return True
    
    return False

# Check if the address number range is actually just a single address that matches the number provided.
def addressRangeIsExactNumber(low, high, number):
    if low != high:
        return False
    if low == number:
        return True
    if low == number.zfill(10):
        return True
    return False

def getZIP4(number, street, unit, state, lat, lon, city=False, zip=False, county=False):
    number = number.strip()
    street = street.strip()
    if not unit:
        unit = ""

    # Get list of 5-digit ZIP Codes matching the city and state
    citystateresults = False
    zipfilter = False
    if city:
        citystateresults, zipfilter = getZIPsForCityState(city, state)
        if len(zipfilter) == 0:
            zipfilter = False
    elif county:
        zipfilter = getZIPsForCounty(county, state)
        if len(zipfilter) == 0:
            zipfilter = False
        

    queries = []
    basenamequeries = [] # Queries that only match on street basename, try after "main" queries don't return a match

    # Get street base name for broader matching in case suffix or directional differs
    typelessStreet = street
    for (short, long) in LONGHAND_STREET_TYPES.items():
        typelessStreet = re.sub(r"\s" + re.escape(short) + r"\b", "", typelessStreet)
    streetBasename = re.sub("^[NSEW]{1,2} ", "", typelessStreet)
    streetBasename = re.sub(" [NSEW]{1,2}$", "", streetBasename)
    
    #print(street, typelessStreet, streetBasename)

    # Build a list of queries to run, starting with the most specific and getting more desperate until a match is found
    if zip:
        queries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StreetFull = '" + street + "' AND State = '" + state + "'" + " AND ZipCode='"+zip+"'")
        basenamequeries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StName = '" + streetBasename + "' AND State = '" + state + "'" + " AND ZipCode='"+zip+"'")
    if zipfilter:
        queries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StreetFull = '" + street + "' AND State = '" + state + "'" + " AND ZipCode IN ('" + ("','".join(zipfilter)) + "')")
        basenamequeries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StName = '" + streetBasename + "' AND State = '" + state + "'" + " AND ZipCode IN ('" + ("','".join(zipfilter)) + "')")

    if not unit and re.match(".* ([0-9]{1,5}|[A-Z]{1})$", street):
        # Maybe the street has the apartment number in it for some reason
        newStreet = re.sub(" ([0-9]{1,5}|[A-Z]{1})$", "", street)
        newUnit = street[len(newStreet):].strip()
        typelessStreet = newStreet
        for (short, long) in LONGHAND_STREET_TYPES.items():
            typelessStreet = re.sub(r"\s" + re.escape(short) + r"\b", "", typelessStreet)
        newstreetBasename = re.sub("^[NSEW]{1,2} ", "", typelessStreet)
        newstreetBasename = re.sub(" [NSEW]{1,2}$", "", newstreetBasename)
        if zip:
            queries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StreetFull = '" + newStreet + "' AND State = '" + state + "'" + " AND ZipCode='"+zip+"'")
            basenamequeries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StName = '" + newstreetBasename + "' AND State = '" + state + "'" + " AND ZipCode='"+zip+"'")
        if zipfilter:
            queries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StreetFull = '" + newStreet + "' AND State = '" + state + "'" + " AND ZipCode IN ('" + ("','".join(zipfilter)) + "')")
            basenamequeries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StName = '" + newstreetBasename + "' AND State = '" + state + "'" + " AND ZipCode IN ('" + ("','".join(zipfilter)) + "')")

    if not zip and not zipfilter:
        # Who needs ZIP Codes and city names anyways
        queries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StreetFull = '" + street + "' AND State = '" + state + "'")
        queries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StName = '" + street + "' AND State = '" + state + "'")
        basenamequeries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StName = '" + streetBasename + "' AND State = '" + state + "'")
        queries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StreetFull LIKE '" + street + "%' AND State = '" + state + "'")
        #queries.append("SELECT ZipCode, Plus4Low, StreetFull, AddressSecAbbr, AddressSecLowNumber, AddressSecHighNumber, AddressPrimaryLowNumber, AddressPrimaryHighNumber, AddressPrimaryEvenOdd FROM ZIP4 WHERE StName LIKE '" + streetBasename + "%' AND State = '" + state + "'")

    resultrows = []
    
    suggestZip = ""
    suggestStreet = ""
    
    queries = queries + basenamequeries

    for query in queries:
        #print(query)
        if query in querycache:
            rows = querycache[query]
            #print("CACHED:  " + query)
        else:
            cur.execute(query)
            #print("NOCACHE: " +query)
            rows = cur.fetchall()

        # Add to query cache
        querycache[query] = rows
        if len(querycache) > querycachelimit:
            querycache.pop(next(iter(querycache)))

        unitfilterrows = rows
        if unit:
            # Filter to rows that match the unit number
            unitfilterrows = subaddrMatchRows(rows, unit)

        # Try matching range against unit-filtered rows, if that doesn't work, try the non-filtered ones (address might be more specific than ZIP4 file)
        for row in unitfilterrows:
            if addressRangeContainsNumber(row["AddressPrimaryLowNumber"], row["AddressPrimaryHighNumber"], row["AddressPrimaryEvenOdd"], number):
                resultrows.append(row)
        if len(resultrows) == 0 and len(unitfilterrows) < len(rows):
            for row in rows:
                if addressRangeContainsNumber(row["AddressPrimaryLowNumber"], row["AddressPrimaryHighNumber"], row["AddressPrimaryEvenOdd"], number):
                    resultrows.append(row)
        
        if len(resultrows) == 1:
            # One match found, it's probably the right one!
            return resultrows[0]["ZipCode"], resultrows[0]["Plus4Low"], resultrows[0]["StreetFull"], resultrows[0]["AddressSecAbbr"], unit

        if len(resultrows) > 1:
            # First check if our source address has a unit, and if not, remove all match rows that DO have a unit.
            if not unit:
                base_rows = [
                    r for r in resultrows
                    if not (
                        r["AddressSecAbbr"] or
                        r["AddressSecLowNumber"] or
                        r["AddressSecHighNumber"]
                    )
                ]

                # If we found at least one base-address row, narrow resultrows to those
                if base_rows:
                    # Narrow further by looking for exact number matches (low and high are the same and what we're looking for)
                    exact_rows = []
                    for row in base_rows:
                        if addressRangeIsExactNumber(row["AddressPrimaryLowNumber"], row["AddressPrimaryHighNumber"], number):
                            exact_rows.append(row)
                    
                    if len(exact_rows) > 0:
                        resultrows = exact_rows
                    else:
                        resultrows = base_rows

                    # If that left us with exactly one, we can return it immediately
                    if len(resultrows) == 1:
                        row = resultrows[0]
                        return (
                            row["ZipCode"],
                            row["Plus4Low"],
                            row["StreetFull"],
                            "",     # no unit designator when no unit was given
                            unit,   # still the original (empty) unit
                        )
            suggestZip = resultrows[0]["ZipCode"]
            suggestStreet = resultrows[0]["StreetFull"]
            for row in resultrows:
                # Check if the 5-digit ZIP and/or street are the same for all results, clear it if not
                if suggestZip != row["ZipCode"]:
                    suggestZip = ""
                if suggestStreet != row["StreetFull"]:
                    suggestStreet = ""
                # Return an address-specific row if it exists
                if row["AddressPrimaryLowNumber"] == number and row["AddressPrimaryHighNumber"] == number:
                    return row["ZipCode"], row["Plus4Low"], row["StreetFull"], row["AddressSecAbbr"], unit
            #print("Multiple possible ZIP+4 matches for", number, street, "#"+unit, city, state, zip)
            #for row in resultrows:
            #    print(row["ZipCode"],row["AddressPrimaryLowNumber"],row["AddressPrimaryHighNumber"], row["StreetFull"], row["AddressPrimaryEvenOdd"], row["Plus4Low"], row["AddressSecAbbr"])

    # No match found
    cfg = src.config.get_config()
    if cfg.useCensusToFillEmptyZIPs and number != "" and street != "" and city != "" and city != False and state != "":
        # Query the Census Geocoder, because this address probably exists
        print("US Census Geo:" + number + " " + street + ", " + city + " " + state + "                         ", end="\r", flush=True)
        try:
            result = urllib.request.urlopen("https://geocoding.geo.census.gov/geocoder/locations/address?street="+quote(number + " " + street)+"&city="+quote(city)+"&state="+state+"&zip=&benchmark=4&format=json").read()
            jsonresult = json.loads(result)
            if len(jsonresult["result"]["addressMatches"]) == 1:
                comps = jsonresult["result"]["addressMatches"][0]["addressComponents"]
                streetparts = [comps["preDirection"], comps["preType"], comps["streetName"], comps["suffixType"], comps["suffixDirection"]]
                street = " ".join(x for x in streetparts if x)
                return jsonresult["result"]["addressMatches"][0]["addressComponents"]["zip"], "", street, "", unit
        except:
            pass
    
    if suggestZip == "":
        suggestZip = getZIP(lat, lon, False, state, city)
    if suggestStreet == "":
        suggestStreet = street
    return suggestZip, "", suggestStreet, "", unit


def getZIP(lat, lon, prefix=False, state=False, city=False):
    if city == "":
        city = False
    else:
        city = city.upper()
    if state == "":
        state = False
    lat = float(lat)
    lon = float(lon)

    citystateresult = False
    if city and state: # Check if city and state combo only has one standard ZIP Code
        try:
            citystateresult = fastsearch.by_city_and_state(city, state, returns=30, zipcode_type=ZipcodeTypeEnum.Standard) # Use simple database because it's like 2x faster
            if len(citystateresult) == 1:
                #print("Exact city match found: "+city+ " "+state+" "+citystateresult[0].zipcode)
                return citystateresult[0].zipcode
        except ValueError:
            # Sometimes it objects to a city name and says it isn't valid
            pass
    if prefix and state: # Get ZIPs by lat/lon that start with prefix and are in state for faster queries
        result = search.query(lat = lat, lng = lon, returns = 20, radius=10, prefix=str(prefix), state=state, zipcode_type=ZipcodeTypeEnum.Standard)
    elif state: # Get ZIPs filtered by state for faster queries
        result = search.query(lat = lat, lng = lon, returns = 20, radius=10, state=state, zipcode_type=ZipcodeTypeEnum.Standard)
    elif prefix: # Get ZIPs by lat/lon that start with prefix for faster queries
        result = search.query(lat = lat, lng = lon, returns = 20, radius=10, prefix=str(prefix), zipcode_type=ZipcodeTypeEnum.Standard)
    else: # Get ZIPs by lat/lon
        result = search.by_coordinates(lat = lat, lng = lon, returns = 20, zipcode_type=ZipcodeTypeEnum.Standard)

    if len(result) == 1:
        return result[0].zipcode
    elif len(result) > 1:
        matchzips = []
        if citystateresult:
            # Find zip codes that both queries have in common, maybe there's only one that overlaps with both!
            for val in citystateresult:
                for res in result:
                    if res.zipcode == val.zipcode:
                        matchzips.append(res)
            if len(matchzips) == 1:
                #print("Exact match found between lat/lon and city/state queries: "+matchzips[0])
                return matchzips[0].zipcode
        else:
            matchzips = result
        #print("W: Multiple equally-valid ZIP matches for "+str(lat)+", "+str(lon)+" "+str(city)+", "+str(state)+": ")
        addrpoint = Point(lon, lat)
        for zip in matchzips:
            #print("  "+zip.zipcode)
            zippolys = zip.polygon
            if zippolys == None:
                continue
            if dimensionality(zippolys) == 2:
                zippolys = [zippolys]
            for poly in zippolys:
                zipborder = Polygon(poly)
                if zipborder.contains(addrpoint):
                    #print("Found probable ZIP based on border: " + zip.zipcode)
                    return zip.zipcode
        return None
    else:
        return None

def dimensionality(matrix):
    dims = []
    while isinstance(matrix, list) and matrix is not None:
        dims.append(len(matrix))
        matrix = matrix[0]
    return len(dims)
