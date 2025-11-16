#!/usr/bin/python3

# Generate a ZIP+4 database from the data at https://www.zip-codes.com/zip-plus-4-database.asp

from argparse import ArgumentParser
import sqlite3, zipfile, re
import pandas as pd

def process(infile, outfile):
    print("Reading " + infile)
    zf = zipfile.ZipFile(infile, mode="r")
    zipFiles = zf.namelist()
    ziplist = []
    zip5list = []
    zipcountylist = [] # List of ZIPs in multiple counties
    for fname in zipFiles:
        if re.match("ZIP4-[A-Z]{2}.zip", fname):
            ziplist.append(fname)
        elif fname == "zip-codes-database-STANDARD.csv":
            zip5list.append(fname)
        elif "MULTI-COUNTY" in fname and fname.endswith(".csv"):
            zipcountylist.append(fname)

    filesprocessed = 0
    chunksprocessed = 0
    chunksize = 5000
    
    if len(ziplist) > 0:
        print("Creating ZIP+4 database")
        connection = sqlite3.connect(outfile)
        connection.executescript("PRAGMA foreign_keys=OFF;")
        c = connection.cursor()
        c.execute("PRAGMA journal_mode=OFF;")        # or MEMORY; fastest is OFF (risk if crash)
        c.execute("PRAGMA synchronous=OFF;")         # biggest win: no fsync on each commit
        c.execute("PRAGMA temp_store=MEMORY;")       # keep temp B-trees in RAM
        c.execute("PRAGMA cache_size=-1600000;")     # ~1600MB page cache (negative = KB)
        c.execute("PRAGMA locking_mode=EXCLUSIVE;")  # avoid lock thrash
        c.execute("PRAGMA mmap_size=1073741824;")    # 1GB mmap; helps reads, slight write help
        c.execute("PRAGMA page_size=65536;")
        createZIP4DB(c)

        def mergeStreet(row):
            return ' '.join(filter(None, [row["StPreDirAbbr"], row["StName"], row["StSuffixAbbr"], row["StPostDirAbbr"]]))

        for file in ziplist:
            with zf.open(file, mode="r", force_zip64=True) as innerfile:
                with zipfile.ZipFile(innerfile, mode="r") as innerzip:
                    with innerzip.open(innerzip.namelist()[0], mode="r") as csvfile:
                        print("\nImporting " + file + " ..." + "            ", end="\r", flush=True)
                        for chunk in pd.read_csv(csvfile, chunksize=chunksize, keep_default_na=False, dtype="str"):
                            chunk["StreetFull"] = chunk.apply(mergeStreet, axis=1)
                            chunk.to_sql("ZIP4", connection, if_exists='append', index=False, method='multi')
                            chunksprocessed = chunksprocessed + 1
                            print("Importing " + file + " ... " + str(chunksprocessed * chunksize) +"            ", end="\r", flush=True)
                        #print("\nVacuuming database...")
                        #connection.executescript("VACUUM")
            filesprocessed = filesprocessed + 1
        zf.close()

    if len(zip5list) > 0:
        print("Creating 5-digit ZIP database")
        connection = sqlite3.connect(outfile)
        c = connection.cursor()
        createZIP5DB(c)
        filesprocessed = 1
        with zf.open(zip5list[0], mode="r", force_zip64=True) as csvfile:
            print("\nImporting " + zip5list[0] + " ..." + "            ", end="\r", flush=True)
            for chunk in pd.read_csv(csvfile, chunksize=chunksize, keep_default_na=False, dtype="str"):
                chunk.to_sql("ZIPCodes", connection, if_exists='append', index=False)
                chunksprocessed = chunksprocessed + 1
                print("Importing " + zip5list[0] + " ... " + str(chunksprocessed * chunksize) +"            ", end="\r", flush=True)
    
    if len(zipcountylist) > 0:
        print("Creating Multi-county ZIP database")
        connection = sqlite3.connect(outfile)
        c = connection.cursor()
        createZIPMultiCountyDB(c)
        filesprocessed = 1
        with zf.open(zipcountylist[0], mode="r", force_zip64=True) as csvfile:
            print("\nImporting " + zipcountylist[0] + " ..." + "            ", end="\r", flush=True)
            for chunk in pd.read_csv(csvfile, chunksize=chunksize, keep_default_na=False, dtype="str"):
                chunk.to_sql("ZIPCodesMultiCounty", connection, if_exists='append', index=False)
                chunksprocessed = chunksprocessed + 1
                print("Importing " + zipcountylist[0] + " ... " + str(chunksprocessed * chunksize) +"            ", end="\r", flush=True)

    print("\nFiles processed: " + str(filesprocessed))
    print("Records processed: " + str(chunksprocessed * chunksize))
    print("Done! Saved to " + outfile)
    print("\nOne last thing: optimizing output database (this might take a few minutes)...")
    connection.executescript("VACUUM; ANALYZE; PRAGMA optimize;")


def createZIP5DB(c):
    c.execute("DROP TABLE IF EXISTS ZIPCodes")
    c.execute('''CREATE TABLE ZIPCodes (
	ZipCode char(5) NOT NULL,
	City varchar(35) NULL,
	State char(2),
	County varchar(45) NULL,
	AreaCode varchar(55) NULL,
	CityType char(1) NULL,
	CityAliasAbbreviation varchar(13) NULL,
	CityAliasName varchar(35) NULL,
	Latitude decimal(12, 6),
	Longitude decimal(12, 6),
	TimeZone char(2) NULL,
	Elevation int,
	CountyFIPS char(5) NULL,
	DayLightSaving char(1) NULL,
	PreferredLastLineKey varchar(10) NULL,
	ClassificationCode char(1) NULL,
	MultiCounty char(1) NULL,
	StateFIPS char(2) NULL,
	CityStateKey char(6) NULL,
	CityAliasCode varchar(5) NULL,
	PrimaryRecord char(1),
	CityMixedCase varchar(35) NULL,
	CityAliasMixedCase varchar(35) NULL,
	StateANSI varchar(2) NULL,
	CountyANSI varchar(3) NULL,
	FacilityCode varchar(1) NULL,
	CityDeliveryIndicator varchar(1) NULL,
	CarrierRouteRateSortation varchar(1) NULL,
	FinanceNumber varchar(6) NULL,
	UniqueZIPName varchar(1) NULL,
	CountyMixedCase varchar(45) NULL
);''')
    c.execute("CREATE INDEX Index_ZIPCodes_ZipCode ON ZIPCodes (ZipCode)")
    c.execute("CREATE INDEX Index_ZIPCodes_State ON ZIPCodes (State)")
    c.execute("CREATE INDEX Index_ZIPCodes_County ON ZIPCodes (County)")
    c.execute("CREATE INDEX Index_ZIPCodes_AreaCode ON ZIPCodes (AreaCode)")
    c.execute("CREATE INDEX Index_ZIPCodes_City ON ZIPCodes (City)")
    c.execute("CREATE INDEX Index_ZIPCodes_Latitude ON ZIPCodes (Latitude)")
    c.execute("CREATE INDEX Index_ZIPCodes_Longitude ON ZIPCodes (Longitude)")
    c.execute("CREATE INDEX Index_ZIPCodes_CityAliasName ON ZIPCodes (CityAliasName)")
    c.execute("CREATE INDEX Index_ZIPCodes_CityStateKey ON ZIPCodes (CityStateKey)")

    c.execute("DROP TABLE IF EXISTS States")
    c.execute("CREATE TABLE States (code TEXT, name TEXT)")
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("AE", "Armed Forces Europe, the Middle East, and Canada")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("AP", "Armed Forces Pacific")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("AA", "Armed Forces Americas")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("AL", "Alabama")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("AK", "Alaska")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("AS", "American Samoa")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("AZ", "Arizona")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("AR", "Arkansas")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("CA", "California")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("CO", "Colorado")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("CT", "Connecticut")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("DE", "Delaware")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("DC", "District of Columbia")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("FM", "Federated States of Micronesia")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("FL", "Florida")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("GA", "Georgia")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("GU", "Guam")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("HI", "Hawaii")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("ID", "Idaho")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("IL", "Illinois")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("IN", "Indiana")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("IA", "Iowa")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("KS", "Kansas")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("KY", "Kentucky")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("LA", "Louisiana")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("ME", "Maine")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("MH", "Marshall Islands")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("MD", "Maryland")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("MA", "Massachusetts")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("MI", "Michigan")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("MN", "Minnesota")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("MS", "Mississippi")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("MO", "Missouri")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("MT", "Montana")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("NE", "Nebraska")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("NV", "Nevada")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("NH", "New Hampshire")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("NJ", "New Jersey")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("NM", "New Mexico")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("NY", "New York")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("NC", "North Carolina")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("ND", "North Dakota")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("MP", "Northern Mariana Islands")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("OH", "Ohio")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("OK", "Oklahoma")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("OR", "Oregon")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("PW", "Palau")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("PA", "Pennsylvania")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("PR", "Puerto Rico")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("RI", "Rhode Island")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("SC", "South Carolina")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("SD", "South Dakota")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("TN", "Tennessee")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("TX", "Texas")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("UT", "Utah")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("VT", "Vermont")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("VI", "Virgin Islands")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("VA", "Virginia")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("WA", "Washington")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("WV", "West Virginia")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("WI", "Wisconsin")')
    c.execute('INSERT INTO "States" ("code", "name") VALUES ("WY", "Wyoming")')

def createZIPMultiCountyDB(c):
    c.execute("DROP TABLE IF EXISTS ZIPCodesMultiCounty")
    c.execute("CREATE TABLE ZIPCodesMultiCounty ( ZipCode char(5) NOT NULL, StateFIPS char(2), State char(2), CountyFIPS char(5) NULL, County varchar(45), CountyMixedCase varchar(45) )")
    c.execute("CREATE INDEX Index_ZIPCodesMultiCounty_ZipCode ON ZIPCodesMultiCounty (ZipCode)")
    c.execute("CREATE INDEX Index_ZIPCodesMultiCounty_State ON ZIPCodesMultiCounty (State)")
    c.execute("CREATE INDEX Index_ZIPCodesMultiCounty_County ON ZIPCodesMultiCounty (County)")

def createZIP4DB(c):
    c.execute("DROP TABLE IF EXISTS `ZIP4`")
    c.execute('''
CREATE TABLE "ZIP4" (
"ZipCode"char(5),
"UpdateKey"varchar(10),
"Action"char(1),
"RecordType"varchar(1),
"CarrierRoute"varchar(4),
"StPreDirAbbr"varchar(2),
"StName"varchar(28),
"StSuffixAbbr"varchar(4),
"StPostDirAbbr"varchar(2),
"AddressPrimaryLowNumber"varchar(10),
"AddressPrimaryHighNumber"varchar(10),
"AddressPrimaryEvenOdd"varchar(1),
"BuildingName"varchar(40),
"AddressSecAbbr"varchar(4),
"AddressSecLowNumber"varchar(10),
"AddressSecHighNumber"varchar(10),
"AddressSecOddEven"varchar(1),
"Plus4Low"varchar(4),
"Plus4High"varchar(4),
"BaseAlternateCode"varchar(1),
"LACSStatus"varchar(1),
"GovernmentBuilding"varchar(1),
"FinanceNumber"varchar(6),
"State"varchar(2),
"CountyFIPS"varchar(3),
"CongressionalDistrict"varchar(2),
"MunicipalityKey"varchar(6),
"UrbanizationKey"varchar(6),
"PreferredLastLineKey"varchar(6),
"ToLatitude"decimal(18, 10),
"FromLatitude"decimal(18, 10),
"ToLongitude"decimal(18, 10),
"FromLongitude"decimal(18, 10),
"CensusTract"varchar(15),
"CensusBlock"varchar(15),
"TLID"varchar(15),
"LatLonMultiMatch"varchar(1),
"StreetFull" varchar(36)
)
    ''')
    c.execute('''CREATE INDEX "addressnumber" ON "ZIP4" ("AddressPrimaryLowNumber","AddressPrimaryHighNumber","AddressPrimaryOddEven")''')
    c.execute('''CREATE INDEX "key" ON "ZIP4" ("PreferredLastLineKey")''')
    c.execute('''CREATE INDEX "zipcode_route" ON "ZIP4" ("ZipCode", "CarrierRoute")''')
    c.execute('''CREATE INDEX "state" ON "ZIP4" ("State")''')
    c.execute('''CREATE INDEX "streetfull_state" ON "ZIP4" ("StreetFull", "State")''')
    c.execute('''CREATE INDEX "stname_state" ON "ZIP4" ("StName", "State")''')
    c.execute('''CREATE INDEX "zip" ON "ZIP4" ("ZipCode")''')
    c.execute('''CREATE INDEX "streetfull_state_zip" ON "ZIP4" ("StreetFull", "State", "ZipCode")''')
    c.execute('''CREATE INDEX "stname_state_zip" ON "ZIP4" ("StName", "State", "ZipCode")''')


parser = ArgumentParser(description='Create a SQLite ZIP Code database from CSV data from https://www.zip-codes.com/zip-plus-4-database.asp. Supports both 5-digit ZIP and ZIP+4 products.')

parser.add_argument('src', help='Input .zip archive')
parser.add_argument('dest', help='Output SQLite3 database file')

if __name__ == "__main__":
    args = parser.parse_args()
    process(args.src, args.dest)
