#!/usr/bin/python3

if __name__ == "__main__":
    print("Address Database Builder 2025")
    print("Starting up...")

import argparse, csv, zipfile, gzip, os, re, json, traceback, sys, multiprocessing
import concurrent.futures
from collections import deque
import pandas as pd
import dask.dataframe as dd
import gc
from multiprocessing import get_context

import sqlite3

from src.addressfunctions import normalizeAddress
from src.constants import ValidationException
import src.config

maxthreads = multiprocessing.cpu_count()
MAX_IN_FLIGHT = maxthreads * 2
os.environ["OPENBLAS_MAIN_FREE"] = "1"
writelock = multiprocessing.Lock()
badcount = 0
skippedcount = 0

countrycode = "US"

def init_worker(cfg: src.config.AppConfig):
    src.config.set_config(cfg)

def fixLatLon(filepath):
    cfg = src.config.get_config()
    print("Repairing flipped latitude/longitude pairs in " + filepath)
    fixedcount = 0
    df = pd.read_csv(filepath, keep_default_na=False, dtype="str")
    skipstates = ("VI", "AK", "HI", "PR")
    for index, row in df.iterrows():
        row.latitude = float(row.latitude)
        row.longitude = float(row.longitude)
        if row.latitude < -90 or row.latitude > 90:
            df.at[index, "latitude"], df.at[index, "longitude"] = row.longitude, row.latitude
            fixedcount = fixedcount + 1
        elif cfg.countryCode == "US" and row.state not in skipstates and (row.longitude < -171.791110603 or row.longitude > -66.96466):
            df.at[index, "latitude"], df.at[index, "longitude"] = row.longitude, row.latitude
            fixedcount = fixedcount + 1
        elif cfg.countryCode == "US" and row.state not in skipstates and (row.latitude < 18.91619 or row.latitude > 71.3577635769):
            df.at[index, "latitude"], df.at[index, "longitude"] = row.longitude, row.latitude
            fixedcount = fixedcount + 1
    df.to_csv(filepath + ".coordfix.csv", mode='a', index=False, header=not os.path.exists(filepath + ".coordfix.csv"))
    print("\nDone flipping " + filepath + "! Fixed " + str(fixedcount) + " records.")


def normalize(number, street, street2, city, state, zipcode, latitude, longitude, zipprefix = False, plus4="", county = False):
    cfg = src.config.get_config()
    street1 = street
    if len(city) > 4 and street1.endswith(" " + city):
        # City name leaked into street field (Albany County Wyoming, for one)
        street1 = street1.removesuffix(" " + city)
    addr = normalizeAddress(number, street1, street2, city, state, zipcode, round(float(latitude),7), round(float(longitude), 7), zipprefix, plus4, county)
    if len(addr['zip'] or "") < 5 or len(addr['plus4'] or "") != 4:
        
        # Try removing letters from address numbers, and ignore city field
        addrstrip = normalizeAddress(re.sub("[^0-9]", "", addr['number']), addr['street'], addr['unit'], addr['city'], addr['state'], addr['zip'], addr['latitude'], addr['longitude'], False, addr['plus4'], county)
        
        # If that didn't work, try instead stripping the city name because it might be wrong
        if addr['city'] != "" and (len(addrstrip['zip'] or "") < 5 or len(addrstrip['plus4'] or "") != 4):
            addrstrip = normalizeAddress(addr['number'], addr['street'], addr['unit'], "", addr['state'], addr['zip'], addr['latitude'], addr['longitude'], False, addr['plus4'], county)
        
        # Use libpostal to analyze address deeper
        if cfg.advancedMode and len(addrstrip['zip'] or "") < 5 or len(addrstrip['plus4'] or "") != 4:
            try:
                addr = advancedNormalize(addr['number'], addr['street'], addr['unit'], addr['city'], addr['state'], addr['zip'], addr['latitude'], addr['longitude'], False, addr['plus4'], county)
            except Exception as e:
                pass
            # Do another normalize pass for good luck (maybe the previous one got the ZIP and now we can get the +4)
            if len(addr['zip'] or "") < 5 or len(addr['plus4'] or "") != 4:
                addr = normalizeAddress(addr['number'], addr['street'], addr['unit'], addr['city'], addr['state'], addr['zip'], addr['latitude'], addr['longitude'], False, addr['plus4'], county)
        else:
            addr = addrstrip
    return addr

def processOwnChunk(chunk, chunkcount, outfilename, ignorestates, keeponlystates):
    global badcount, skippedcount, writelock
    cfg = src.config.get_config()
    data = []
    print(" " + str(chunkcount) + "          ", end="\r", flush=True)
    for index, row in chunk.iterrows():
        if row.state in ignorestates:
            skippedcount = skippedcount + 1
            continue
        if keeponlystates != [] and row.state not in keeponlystates:
            skippedcount = skippedcount + 1
            continue
        try:
            if not cfg.noSkip4 and len(row.plus4 or "") == 4:
                addr = {
                    "number": row.number,
                    "street": row.street,
                    "unit": row.street2,
                    "city": row.city,
                    "state": row.state,
                    "zip": row.zip,
                    "plus4": row.plus4,
                    "latitude": round(float(row.latitude),7),
                    "longitude": round(float(row.longitude), 7)
                }
            else:
                addr = normalize(row.number, row.street, row.street2, row.city, row.state, row.zip, round(float(row.latitude),7), round(float(row.longitude), 7), False, row.plus4)
                
            if addr["state"] in ignorestates:
                skippedcount = skippedcount + 1
                continue
            
            data.append([addr['number'], addr['street'], addr['unit'], addr['city'], addr['state'], addr['zip'], addr['plus4'], addr['latitude'], addr['longitude'], row.source])
        except ValidationException as e:
            badcount = badcount + 1
        except Exception as e:
            print("W: Couldn't ingest address:")
            print(row)
            traceback.print_exc()
            badcount = badcount + 1
    out = pd.DataFrame(data=data, columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
    with writelock:
        out.to_csv(outfilename, mode='a', index=False, header=not os.path.exists(outfilename), columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
    gc.collect()
    

def importOwnFile(filename, outfilename, ignorestates, keeponlystates):
    global badcount, skippedcount, writelock
    print("Processing addresses from " + filename)
    columns = ["number","street","street2","city","state","zip","plus4","latitude","longitude","source"]
    file = filename
    chunkcount = 0
    badcount = 0
    skippedcount = 0
    chunksize = 1000
    in_flight = set()
    with concurrent.futures.ProcessPoolExecutor(max_workers=maxthreads, max_tasks_per_child=100, initializer=init_worker, initargs=(cfg,)) as executor:
        for chunk in pd.read_csv(file, chunksize=chunksize, usecols=columns, keep_default_na=False, dtype={
            "number":"string","street":"string",
            "street2":"string","city":"string",
            "state":"category", "zip":"string",
            "plus4": "string",
            "latitude":"float32", "longitude":"float32",
            "source":"category"}, dtype_backend="pyarrow"):
            
            while len(in_flight) >= MAX_IN_FLIGHT:
                done, in_flight = concurrent.futures.wait(in_flight, return_when=concurrent.futures.FIRST_COMPLETED)
                for fut in done:
                    fut.result()
            fut = executor.submit(processOwnChunk, chunk, chunkcount * chunksize, outfilename, ignorestates, keeponlystates)
            in_flight.add(fut)
            chunkcount = chunkcount + 1
        
        for fut in concurrent.futures.as_completed(in_flight):
            fut.result()
            
    print("\nDone processing! Parsed " + str(chunkcount) + " chunks.")
    print("There were " + str(badcount) + " unprocessable addresses.")
    if ignorestates:
        print("There were " + str(skippedcount) + " addresses ignored due to your --ignorestates setting.")
    print("Saved to output file " + outfilename)

def processNadChunk(chunk, chunkcount, outfilename, ignorestates, keeponlystates):
    global badcount, skippedcount, writelock
    print(" " + str(chunkcount) + "          ", end="\r", flush=True)
    data = []
    for index, row in chunk.iterrows():
        if row.State.upper() in ignorestates:
            skippedcount = skippedcount + 1
            continue
        if keeponlystates != [] and row.State.upper() not in keeponlystates:
            skippedcount = skippedcount + 1
            continue
        try:
            town = row.Inc_Muni
            if town == "Unincorporated":
                town = ""
            if not town:
                town = row.Post_City
            if not town:
                town = row.Uninc_Comm
            
            addr = normalize(row.AddNo_Full, row.StNam_Full, row.SubAddress, row.Inc_Muni, row.State, row.Zip_Code, round(float(row.Latitude),7), round(float(row.Longitude), 7))
            
            if addr["state"] in ignorestates: # For example, AR's data claims to have MO addresses but the ZIP says they're in AR, so the first pass of this won't catch those
                skippedcount = skippedcount + 1
                continue
            
            source = row.NAD_Source
            source = source.replace("State of ", "")
            source = "NAD " + source
            data.append([addr['number'], addr['street'], addr['unit'], addr['city'], addr['state'], addr['zip'], addr['plus4'], addr['latitude'], addr['longitude'], source])
        except ValidationException as e:
            badcount = badcount + 1
        except Exception as e:
            print("W: Couldn't ingest address:")
            print(row)
            traceback.print_exc()
            badcount = badcount + 1
    out = pd.DataFrame(data=data, columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
    with writelock:
        out.to_csv(outfilename, mode='a', index=False, header=not os.path.exists(outfilename), columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
    gc.collect()

def importNadFile(filename, outfilename, ignorestates, keeponlystates, startatline):
    global skippedcount, badcount
    print("Importing National Address Database addresses from " + filename)
    if startatline > 0:
        print("Skipping to line number " + str(startatline))
    
    columns = [
        "AddNo_Full",
        "StNam_Full",
        "St_PreMod",
        "St_PreDir",
        "St_Name",
        "SubAddress",
        "Inc_Muni",
        "Post_City",
        "Uninc_Comm",
        "Urbnztn_PR",
        "State",
        "Zip_Code",
        "UUID",
        "Longitude",
        "Latitude",
        "DateUpdate",
        "NAD_Source",
    ]
    file = filename
    if filename.endswith(".zip"):
        zf = zipfile.ZipFile(filename, mode="r")
        zipFiles = zf.namelist()
        for fname in zipFiles:
            if fname.upper().startswith("TXT/NAD") and fname.upper().endswith(".TXT"):
                file = zf.open(fname, mode="r", force_zip64=True)
                break
    chunkcount = 0
    chunksize = 1000
    in_flight = set()
    with concurrent.futures.ProcessPoolExecutor(max_workers=maxthreads, mp_context=get_context("spawn"), max_tasks_per_child=100, initializer=init_worker, initargs=(cfg,)) as executor:
        for chunk in pd.read_csv(file, chunksize=chunksize, header=0, skiprows=lambda i: 1 <= i <= startatline, usecols=columns, keep_default_na=False, dtype={
            "State":"category","NAD_Source":"category",
            "Zip_Code":"string","UUID":"string",
            "AddNo_Full":"string","StNam_Full":"string","St_PreMod":"string",
            "St_PreDir":"string","St_Name":"string","SubAddress":"string",
            "Inc_Muni":"string","Post_City":"string","Uninc_Comm":"string",
            "Urbnztn_PR":"string","Longitude":"float32","Latitude":"float32",
            "DateUpdate":"string"}, dtype_backend="pyarrow"):
            while len(in_flight) >= MAX_IN_FLIGHT:
                done, in_flight = concurrent.futures.wait(in_flight, return_when=concurrent.futures.FIRST_COMPLETED)
                for fut in done:
                    fut.result()
            fut = executor.submit(processNadChunk, chunk, chunkcount * chunksize, outfilename, ignorestates, keeponlystates)
            in_flight.add(fut)
            chunkcount = chunkcount + 1
        for fut in concurrent.futures.as_completed(in_flight):
            fut.result()
    print("\nDone importing NAD! Processed " + str(chunkcount) + " chunks of " + str(chunksize) + " rows.")
    print("There were " + str(badcount) + " unprocessable addresses.")
    if ignorestates:
        print("There were " + str(skippedcount) + " addresses ignored due to your --ignorestates setting.")
    print("Saved to output file " + outfilename)

def processOpenAddressRows(rows, startindex, outfilename, ignorestates, source, stateOverride, zipprefix, citySuggestion, county = False):
    global badcount, skippedcount, writelock
    print(" " + str(startindex) + "                                                ", end="\r", flush=True)
    linecount = 0
    outdata = []
    emptylinecount = 0
    for line in rows:
        linecount = linecount + 1
        try:
            data = json.loads(line)
            if not data["properties"]["number"] and not data["properties"]["street"]:
                emptylinecount = emptylinecount + 1
            if not data["geometry"] or not data["geometry"]["coordinates"][0] or not data["geometry"]["coordinates"][1]:
                emptylinecount = emptylinecount + 1
            state = data["properties"]["region"].upper()
            city = data["properties"]["city"].upper().strip()
            if stateOverride:
                state = stateOverride
            if state in ignorestates:
                skippedcount = skippedcount + 1
                continue
            if data["geometry"] is None:
                badcount = badcount + 1
                continue
            if not data["properties"]["number"] or not data["properties"]["street"] or data["properties"]["number"] == "0":
                badcount = badcount + 1
                continue
            if citySuggestion and not city:
                city = citySuggestion
            if source == "OA/hawaii" and re.match(r"^[1-9][1-9][0-9]{4}", data["properties"]["number"]):
                # Source is broken/missing, and the last good version has the house numbers without dashes
                # Hawaii has a specific and unique address numbering system
                data["properties"]["number"] = data["properties"]["number"][:2] + "-" +  data["properties"]["number"][2:]
            
            addr = normalize(data["properties"]["number"], data["properties"]["street"], data["properties"]["unit"], city, state, data["properties"]["postcode"], data["geometry"]["coordinates"][1], data["geometry"]["coordinates"][0], zipprefix, "", county)
            
            
            if addr["state"] in ignorestates:
                skippedcount = skippedcount + 1
                continue
            if addr["street"] == "":
                badcount = badcount + 1
                continue
            if not source:
                source = "OA/"+addr["state"]
            outdata.append([addr['number'], addr['street'], addr['unit'], addr['city'], addr['state'], addr['zip'], addr['plus4'], addr['latitude'], addr['longitude'], source])
        except ValidationException as e:
            badcount = badcount + 1
        except Exception as e:
            traceback.print_exc()
            print("Error encountered while processing", line)
            badcount = badcount + 1
    if linecount > 0 and emptylinecount / linecount > .95:
        print("\nWarning: Empty chunk! " + str(emptylinecount) + " of " + str(linecount) + " rows had no address.")
    out = pd.DataFrame(data=outdata, columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
    with writelock:
        out.to_csv(outfilename, mode='a', index=False, header=not os.path.exists(outfilename), columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
    gc.collect()

def importOpenAddressFile(filepath, outfilename, ignorestates, source, stateOverride, zipprefix):
    global badcount, skippedcount
    cfg = src.config.get_config()
    print("Importing OpenAddresses data from " + filepath)
    chunksize = 1000
    linecount = 0

    if stateOverride:
        stateOverride = stateOverride.strip().upper()

    file = filepath
    if filepath.endswith(".gz"):
        file = gzip.open(filepath, 'rb')
    else:
        file = open(file, 'r')
        
    county = False

    if not source or source == "":
        source = "OA/"+filepath.split("/")[-1].split("-")[0]
        if source.startswith("OA/statewide"):
            if stateOverride:
                source = source.replace("statewide", stateOverride)
            else:
                source = False
    citySuggestion = False
    if not cfg.citySuggestion and filepath.split("/")[-1].startswith("city_of_"):
        # Set city suggestion using filename
        citySuggestion = re.sub(r'\d+', '', filepath.split("/")[-1].split("-")[0].replace("city_of_", "").replace("_", " ").upper().strip())

    if filepath.split("/")[-1].endswith("-addresses-county.geojson"):
        county = filepath.split("/")[-1].split("-")[0].replace("_", " ").upper().strip()
        print("Detected county from filename: " + county + ", will use for ZIP Code hinting")

    lines = []
    in_flight = set()
    with concurrent.futures.ProcessPoolExecutor(max_workers=maxthreads, mp_context=get_context("spawn"), max_tasks_per_child=1000, initializer=init_worker, initargs=(cfg,)) as executor:
        for line in file:
            lines.append(line)
            linecount = linecount + 1
            if len(lines) >= chunksize:
                while len(in_flight) >= MAX_IN_FLIGHT:
                    done, in_flight = concurrent.futures.wait(in_flight, return_when=concurrent.futures.FIRST_COMPLETED)
                    for fut in done:
                        fut.result()
                fut = executor.submit(processOpenAddressRows, lines, linecount, outfilename, ignorestates, source, stateOverride, zipprefix, citySuggestion, county)
                in_flight.add(fut)
                lines = []
        
        fut = executor.submit(processOpenAddressRows, lines, linecount, outfilename, ignorestates, source, stateOverride, zipprefix, citySuggestion, county)
        in_flight.add(fut)
        
        for fut in concurrent.futures.as_completed(in_flight):
            fut.result()

    file.close()

    print("\nDone importing OpenAddresses! Processed " + str(linecount) + " entries.")
    print("There were " + str(badcount) + " unprocessable addresses.")
    if ignorestates:
        print("There were " + str(skippedcount) + " addresses ignored due to your --ignorestates setting.")
    print("Saved to output file " + outfilename)
    return

def importOSMFile(filename, outfilename):
    """
    Overpass API query for data input (replace name=Montana with the region you want):
    [out:csv(::"lat", ::"lon", "addr:housenumber", "addr:street", "addr:city", "addr:state", "addr:postcode")][timeout:120];
    area["name"="Montana"]->.boundaryarea;
    node["addr:housenumber"]["addr:street"](area.boundaryarea);
    out;
    way["addr:housenumber"]["addr:street"](area.boundaryarea);
    out center;
    relation["addr:housenumber"]["addr:street"](area.boundaryarea);
    out center;
    """
    print("Importing OSM Overpass data from " + filename)
    columns = [
        "@lat",
        "@lon",
        "addr:housenumber",
        "addr:street",
        "addr:city",
        "addr:state",
        "addr:postcode"
    ]
    file = filename
    chunkcount = 0
    badcount = 0
    skippedcount = 0
    source = "OpenStreetMap.org. License: ODbL"
    for chunk in pd.read_csv(file, sep='\t', chunksize=100, usecols=columns, keep_default_na=False, dtype="str"):
        print(" " + str(chunkcount * 100) + "          ", end="\r", flush=True)
        data = []
        for index, row in chunk.iterrows():
            try:
                addr = normalize(row["addr:housenumber"], row["addr:street"], "", row["addr:city"], row["addr:state"], row["addr:postcode"], row["@lat"], row["@lon"])
                data.append([addr['number'], addr['street'], addr['unit'], addr['city'], addr['state'], addr['zip'], addr['plus4'], addr['latitude'], addr['longitude'], source])
            except ValidationException as e:
                badcount = badcount + 1
            except Exception as e:
                print("W: Couldn't ingest address:")
                print(row)
                traceback.print_exc()
                badcount = badcount + 1
        out = pd.DataFrame(data=data, columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
        out.to_csv(outfilename, mode='a', index=False, header=not os.path.exists(outfilename), columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
        chunkcount = chunkcount + 1
    print("\nDone importing OSM! Processed " + str(chunkcount) + " chunks.")
    print("There were " + str(badcount) + " unprocessable addresses.")
    print("Saved to output file " + outfilename)

def importNARFile(filename, outfilename):
    print("Importing Statistics Canada data from " + filename)
    zf = zipfile.ZipFile(filename, mode="r")
    zipFiles = zf.namelist()
    locationFileList = {}
    addressFileList = {}
    provinceCodes = [10,11,12,13,24,35,46,47,48,59,60,61,62]
    for c in provinceCodes:
        addressFileList[str(c)] = []
        locationFileList[str(c)] = []
    #  = zf.open(fname, mode="r", force_zip64=True)
    for fname in zipFiles:
        if fname.startswith("Addresses/Address_") and fname.endswith(".csv"):
            number = fname.replace("Addresses/Address_", "").replace(".csv", "").split("_")[0]
            addressFileList[number].append(fname)
        elif fname.startswith("Locations/Location_") and fname.endswith(".csv"):
            number = fname.replace("Locations/Location_", "").replace(".csv", "").split("_")[0]
            locationFileList[number].append(fname)

    print("\nMerging address and location tables...")
    mergecount = 0
    dataframes = []
    addrcols = ["LOC_GUID","APT_NO_LABEL","CIVIC_NO","CIVIC_NO_SUFFIX","MAIL_STREET_NAME","MAIL_STREET_TYPE","MAIL_STREET_DIR","MAIL_MUN_NAME","MAIL_PROV_ABVN","MAIL_POSTAL_CODE","BU_N_CIVIC_ADD"]
    loccols = ["LOC_GUID","BG_LATITUDE","BG_LONGITUDE"]
    for provinceId in provinceCodes:
        print(" " + str(mergecount+1) + "          ", end="\r", flush=True)
        readaf = map(lambda addrFilename: dd.read_csv("zip://"+addrFilename, storage_options={'fo': filename}, usecols=addrcols, keep_default_na=False, dtype="str"), addressFileList[str(provinceId)])
        readlf = map(lambda locationFilename: dd.read_csv("zip://"+locationFilename, storage_options={'fo': filename}, usecols=loccols, keep_default_na=False, dtype="str"), locationFileList[str(provinceId)])
        addressFrame = dd.concat(list(readaf), ignore_index=False)
        locationFrame = dd.concat(list(readlf), ignore_index=False)
        dataframes.append(dd.merge(addressFrame, locationFrame, on=["LOC_GUID"]))
        mergecount = mergecount + 1

    print("\nProcessing addresses...")
    file = filename
    alladdrcount = 0
    skippedcount = 0
    source = "StatsCan NAR"
    provinceIndex = 0
    for df in dataframes:
        print("\nProcessing province ID " + str(provinceCodes[provinceIndex]))
        data = []
        addrcount = 0
        for index, row in df.iterrows():
            if (addrcount % 100 == 0):
                print(" " + str(addrcount) + "          ", end="\r", flush=True)
            number = ("".join(filter(None, [row["CIVIC_NO"], row["CIVIC_NO_SUFFIX"]]))).strip().upper()
            street = (" ".join(filter(None, [row["MAIL_STREET_NAME"], row["MAIL_STREET_TYPE"], row["MAIL_STREET_DIR"]]))).strip().upper()
            apt = row["APT_NO_LABEL"].strip().upper()
            if street == "":
                # PO BOX probably
                if row["BU_N_CIVIC_ADD"].startswith("PO BOX "):
                    data.append([row["BU_N_CIVIC_ADD"].replace("PO BOX ", "").strip(), "PO BOX", "", row["MAIL_MUN_NAME"], row["MAIL_PROV_ABVN"], row["MAIL_POSTAL_CODE"], "", row["BG_LATITUDE"], row["BG_LONGITUDE"], source])
                else:
                    skippedcount = skippedcount + 1
            else:
                data.append([number, street, apt, row["MAIL_MUN_NAME"], row["MAIL_PROV_ABVN"], row["MAIL_POSTAL_CODE"], "", row["BG_LATITUDE"], row["BG_LONGITUDE"], source])
            addrcount = addrcount + 1
            if len(data) >= 1000: # Dump to file so we don't use tons of RAM
                out = pd.DataFrame(data=data, columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
                out.to_csv(outfilename, mode='a', index=False, header=not os.path.exists(outfilename), columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
                data = []
        out = pd.DataFrame(data=data, columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
        out.to_csv(outfilename, mode='a', index=False, header=not os.path.exists(outfilename), columns=["number","street","street2","city","state","zip","plus4","latitude","longitude","source"])
        alladdrcount = alladdrcount + addrcount
        provinceIndex = provinceIndex + 1
    print("\nDone importing NAR! Processed " + str(alladdrcount) + " addresses.")
    print("Skipped " + str(skippedcount) + " invalid mailing addresses.")
    print("Saved to output file " + outfilename)

def removeDupes(filepath):
    print("Removing duplicate and incomplete addresses from " + filepath)
    chunkcount = 0
    chunksize = 20000000
    for chunk in pd.read_csv(filepath, chunksize=chunksize, keep_default_na=False, dtype="str", usecols=["number", "street", "street2", "city", "state", "zip", "latitude", "longitude", "source"]):
        print(".", end="", flush=True)
        chunk.replace('', None, inplace=True)
        chunk.dropna(subset=['zip','number','street','city','state','latitude','longitude'], inplace=True)
        chunk.sort_values(by="plus4", ascending=False, inplace=True, na_position="last") # Make sure the address duplicate with a +4 is kept
        chunk.drop_duplicates(subset=["number", "street", "street2", "city", "state", "zip"], keep="first", inplace=True)
        chunk.to_csv(filepath + ".dedup.csv", mode='a', index=False,header=not os.path.exists(filepath + ".dedup.csv"), columns=["number","street","street2","city","state","zip","latitude","longitude", "source"])
        chunkcount = chunkcount + 1
    print("\nDone removing duplicates from " + filepath + "! Processed " + str(chunkcount) + " chunks of " + str(chunksize) + " records.")

def tosqlite(addressfile, dbfile):
    global countrycode
    cfg = src.config.get_config()
    print("\nReading addresses from " + addressfile)
    file = addressfile
    if addressfile.endswith(".gz"):
        file = gzip.open(addressfile, 'rb')
    else:
        file = open(addressfile, 'r')

    connection = sqlite3.connect(dbfile)

    cursor = connection.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS `addresses` (
	`zipcode`	VARCHAR ( 6 ) NOT NULL,
	`number`	VARCHAR ( 30 ) NOT NULL,
	`street`	VARCHAR ( 200 ) NOT NULL,
	`street2`	VARCHAR ( 20 ),
	`city`	VARCHAR ( 50 ) NOT NULL,
	`state`	CHAR ( 2 ) NOT NULL,
    `plus4`	CHAR ( 4 ),
    `country` CHAR ( 2 ) NOT NULL DEFAULT "US",
	`latitude`	DECIMAL ( 8 , 6 ) NOT NULL,
	`longitude`	DECIMAL( 9 , 6 ) NOT NULL,
    `source`	VARCHAR( 40 ),
    UNIQUE (zipcode, number, street, street2, country)
)""")
    cursor.execute("DROP TABLE IF EXISTS `addresses_temp`")
    cursor.execute("""CREATE TABLE IF NOT EXISTS `addresses_temp` (
	`zipcode`	CHAR ( 6 ) NOT NULL,
	`number`	VARCHAR ( 30 ) NOT NULL,
	`street`	VARCHAR ( 200 ) NOT NULL,
	`street2`	VARCHAR ( 20 ),
	`city`	VARCHAR ( 50 ) NOT NULL,
	`state`	CHAR ( 2 ) NOT NULL,
    `plus4`	CHAR ( 4 ),
    `country` CHAR ( 2 ) NOT NULL DEFAULT "US",
	`latitude`	DECIMAL ( 8 , 6 ) NOT NULL,
	`longitude`	DECIMAL( 9 , 6 ) NOT NULL,
    `source`	VARCHAR( 40 )
)""")
    cursor.execute("""CREATE INDEX IF NOT EXISTS `latitude_longitude` ON `addresses` (
	`latitude`,
	`longitude`
)""")
    cursor.execute("""CREATE INDEX IF NOT EXISTS `number_street` ON `addresses` (
	`number`,
	`street`
)""")
    cursor.execute("""CREATE INDEX IF NOT EXISTS `state_city` ON `addresses` (
	`state`,
	`city`
)""")
    cursor.execute("""CREATE INDEX IF NOT EXISTS `zipcode_number` ON `addresses` (
	`zipcode`,
	`number`
)""")
    cursor.execute("""CREATE INDEX IF NOT EXISTS `country` ON `addresses` (
	`country`
)""")

    chunksize = 5000
    chunkcount = 0
    rowschanged = 0
    columns = ["number","street","street2","city","state","zip","latitude","longitude","source"]
    if cfg.appendPlus4:
        columns.append("plus4")
    for chunk in pd.read_csv(file, chunksize=chunksize, usecols=columns, keep_default_na=False, dtype="str"):
        chunk = chunk.rename(columns={'zip': 'zipcode'})
        chunk.insert(7, "country", countrycode)
        # Replace empty values with NULL
        chunk.replace('', None, inplace=True)
        # Replace null street2 with empty string so the SQLite UNIQUE clause will work
        chunk.fillna({"street2": ""}, inplace=True)
        # Remove null values that aren't allowed
        chunk.dropna(subset=['zipcode','number','street','city','state','latitude','longitude'], inplace=True)
        print(" " + str(chunkcount * chunksize) + "          ", end="\r", flush=True)
        # Write chunk to SQLite
        cursor.execute("DELETE FROM addresses_temp")
        chunk.to_sql("addresses_temp", connection, if_exists='append', index=False, dtype={
            "zipcode": "CHAR(6)",
            "number": "VARCHAR(30)",
            "street": "VARCHAR(200)",
            "street2": "VARCHAR(20)",
            "city": "VARCHAR(50)",
            "state": "CHAR(2)",
            "plus4": "CHAR(4)",
            "country": "CHAR(2)",
            "latitude": "DECIMAL(8,6)",
            "longitude": "DECIMAL(9,6)",
            "source": "VARCHAR(40)"
            })
        chunkcount = chunkcount + 1
        cursor.execute("INSERT OR IGNORE INTO addresses SELECT * FROM addresses_temp")
        rowschanged = rowschanged + cursor.rowcount
        if chunkcount % 5000 == 0: # VACUUM every 10 million inserts
            print(" Optimizing database...", end="\r", flush=True)
            connection.executescript("VACUUM")
            print("                       ", end="\r", flush=True)
    connection.executescript("DROP TABLE addresses_temp")

    cursor.execute("DELETE FROM addresses WHERE number=\"0\"")
    rowschanged = rowschanged + cursor.rowcount
    if rowschanged > 10000000:
        print("\nOptimizing database...")
        connection.executescript("VACUUM; ANALYZE; PRAGMA optimize;")
    print("Done converting to SQLite! Processed " + str(chunkcount) + " chunks (" + str(chunksize) + " records per chunk).")
    print(str(rowschanged) + " records inserted.")
    connection.close()
    print("Saved to output file " + dbfile)
    return rowschanged

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tools to build a standardized U.S. address database from free source data."
    )
    parser.add_argument("file", help="Address file(s) to process.", nargs='+')
    parser.add_argument("--outputfile", help="Filename to output address data to. If unspecified, set to \"./data/out.csv\" or \"./data/out.sqlite\", depending on options set.")
    parser.add_argument(
        "--filetype",
        help="Type of address file to ingest. nad=National Address Database, oa=OpenAddresses, adb=CSV created by this script, osm=OpenStreetMap Overpass API (see main.py source for query to use), nar=Statistics Canada National Address Register",
        choices=["nad", "oa", "adb", "osm", "nar"],
    )
    parser.add_argument("--state", help="Some OpenAddresses files don't have the state field set. Do it manually here.")
    parser.add_argument("--ignorestates", help="Comma-separated two-letter state names. Addresses in these states will be skipped over.")
    parser.add_argument("--onlystates", help="Comma-separated two-letter state names. Addresses NOT in these states will be skipped over.")
    parser.add_argument("--source", help="Set the data source name (OpenAddresses only). Autodetected based on filename if not set.")
    parser.add_argument("--dedup", help="Remove duplicate records in an already-ingested address file, and saves it to folder/file.dedup.csv. Only catches \"nearby\" duplicates; processes 20,000,000 records at a time.", action='store_true')
    parser.add_argument("--fixlatlon", help="Detect and repair flipped latitude/longitude pairs in an already-ingested address file, and saves it to [filename].coordfix.csv.", action='store_true')
    parser.add_argument("--tosqlite", help="Output to a SQLite3 database. Only works on output CSV data from this script.", action='store_true')
    parser.add_argument("--appendplus4", help="Append ZIP+4 data to all records. Fairly slow.", action='store_true')
    parser.add_argument("--appendunitlabel", help="Append unit label (APT, STE, etc) to unit numbers using ZIP+4 data.", action='store_true')
    parser.add_argument("--zipprefix", help="When searching for a ZIP, assume it starts with the digits provided for faster lookups.")
    parser.add_argument("-a", help="Allow appending to existing output file.", action='store_true')
    parser.add_argument("--cpu", help="Number of CPU cores to use for parallel processing.")
    parser.add_argument("--country", help="Two-letter country code. Default is US.")
    parser.add_argument("--city", help="City name to assume when there's no city or postal code in the source data. Useful for OpenAddresses city_of_ data files.")
    parser.add_argument("--startat", help="Skip to this line number in the input file (NAD)")
    parser.add_argument("--census", help="Enable looking up missing ZIP codes in the U.S. Census Geocoder when we have a full address, city, and state but no ZIP.", action='store_true')
    parser.add_argument("--libpostal", help="Use libpostal address parsing and expansions to match bad addresses to a ZIP+4. Automatically enables --appendplus4.", action='store_true')
    parser.add_argument("--noskip4", help="When processing own file format, don't skip normalizing records that have a ZIP+4 already.", action="store_true")

    args = parser.parse_args()

    startAtLine = 0
    
    appendPlus4 = False
    appendUnitLabel = False
    useCensusToFillEmptyZIPs = False
    countryCode = "US"
    citySuggestion = False
    advancedMode = False
    noSkip4 = False
    
    if args.libpostal:
        advancedMode = True
        appendPlus4 = True
    if advancedMode:
        from src.advancedparsing import advancedNormalize
        print("Using libpostal to work harder on bad addresses.")

    if args.appendplus4:
        appendPlus4 = True
    if appendPlus4:
        print("Trying to match to ZIP+4 codes for every address!")
        
    if args.noskip4:
        noSkip4 = True
    if noSkip4:
        print("Also normalizing records that have a +4 in the input data.")
    
    if args.appendunitlabel:
        appendUnitLabel = True
        
    if args.census:
        useCensusToFillEmptyZIPs = True
    else:
        useCensusToFillEmptyZIPs = False
    if useCensusToFillEmptyZIPs:
        print("Census geocoder enabled! RIP your network maybe")

    statesToIgnore = []
    if args.ignorestates:
        statesToIgnore = re.sub(r"[^a-zA-Z,]+", "", args.ignorestates.upper()).split(",")
    statesToKeep = []
    if args.onlystates:
        statesToKeep = re.sub(r"[^a-zA-Z,]+", "", args.onlystates.upper()).split(",")

    zipprefix = False
    if args.zipprefix:
        zipprefix = args.zipprefix

    if args.cpu:
        maxthreads = int(args.cpu)

    if args.country:
        if len(args.country) != 2:
            print("Invalid country code " + args.country + ", exiting.")
            sys.exit(1)
        countrycode = args.country.upper()
        countryCode = countrycode
    if args.startat and args.startat.isdigit():
        startAtLine = int(args.startat)

    if args.city:
        citySuggestion = args.city.strip().toUpper()
        
    cfg = src.config.AppConfig(appendPlus4=appendPlus4, appendUnitLabel=appendUnitLabel, countryCode=countryCode, citySuggestion=citySuggestion, useCensusToFillEmptyZIPs=useCensusToFillEmptyZIPs, advancedMode=advancedMode, noSkip4=noSkip4)
    
    src.config.set_config(cfg)

    if args.dedup:
        with concurrent.futures.ProcessPoolExecutor(max_workers=maxthreads) as executor:
            for file in args.file:
                executor.submit(removeDupes, file)
    elif args.fixlatlon:
        with concurrent.futures.ProcessPoolExecutor(max_workers=maxthreads) as executor:
            for file in args.file:
                executor.submit(fixLatLon, file)
    elif args.tosqlite:
        outputfile = "./data/out.sqlite"
        if args.outputfile:
            outputfile = args.outputfile
        if args.a != True and os.path.exists(args.outputfile):
            print("Output file already exists, exiting!")
            sys.exit()
        rowschanged = 0
        filesimported = 0
        for file in args.file:
            rowschanged = rowschanged + tosqlite(file, outputfile)
            filesimported = filesimported + 1
        print("\nDone importing " + str(filesimported) + " files. " + str(rowschanged) + " records inserted.")
    elif args.file:
        outputfile = "./data/out.csv"
        if args.outputfile:
            outputfile = args.outputfile
        if args.a != True and os.path.exists(args.outputfile):
            print("Output file already exists, exiting!")
            sys.exit()
        if args.filetype == "nad":
            for file in args.file:
                importNadFile(file, outputfile, statesToIgnore, statesToKeep, startAtLine)
        elif args.filetype == "adb":
            for file in args.file:
                importOwnFile(file, outputfile, statesToIgnore, statesToKeep)
        elif args.filetype == "osm":
            for file in args.file:
                importOSMFile(file, outputfile)
        elif args.filetype == "nar":
            countrycode = "CA"
            for file in args.file:
                importNARFile(file, outputfile)
        elif args.filetype == "oa":
            source = ""
            if args.source:
                source = args.source
            for file in args.file:
                importOpenAddressFile(file, outputfile, statesToIgnore, source, args.state, zipprefix)
