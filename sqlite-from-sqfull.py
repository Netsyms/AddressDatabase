#!/usr/bin/python3

from argparse import ArgumentParser
import sqlite3

def process(filename, outfile, only4, noplus4, dropcitystate):
    print("Connecting to databases")
    connection = sqlite3.connect(filename)
    connection.executescript("""
PRAGMA temp_store=MEMORY;        -- sorts/temps in RAM
PRAGMA cache_size=-800000;       -- ~800 MB page cache (negative = KB units)
PRAGMA mmap_size=4294967296;     -- 1 GiB memory-mapped I/O (bump if you have RAM)
""")
    c = connection.cursor()

    connection2 = sqlite3.connect(outfile)
    connection2.executescript("""
PRAGMA temp_store=MEMORY;        -- sorts/temps in RAM
PRAGMA cache_size=-800000;       -- ~800 MB page cache (negative = KB units)
PRAGMA mmap_size=4294967296;     -- 1 GiB memory-mapped I/O (bump if you have RAM)
PRAGMA automatic_index=ON;       -- leave enabled (default), can help odd joins
""")
    c2 = connection2.cursor()

    print("Creating lite database")
    plus4create = "`plus4`	CHAR ( 4 )," if not noplus4 else ""
    c2.execute("DROP TABLE IF EXISTS `addresses`")
    c2.execute("""CREATE TABLE `addresses` (
	`zipcode`	VARCHAR ( 6 ) NOT NULL,
	`number`	VARCHAR ( 30 ) NOT NULL,
	`street`	VARCHAR ( 200 ) NOT NULL,
	`street2`	VARCHAR ( 20 ),
	`city`	VARCHAR ( 50 ) NOT NULL,
	`state`	CHAR ( 3 ) NOT NULL, """
    + plus4create
    + """`country` CHAR ( 2 ) NOT NULL DEFAULT "US",
    UNIQUE (zipcode, number, street, street2, country)
)""")
    connection2.commit()

    print("Copying records")
    c.execute("ATTACH DATABASE ? AS dest", (outfile,))
    c.execute("PRAGMA dest.journal_mode = OFF;")
    c.execute("PRAGMA dest.synchronous = OFF;")
    c.execute("PRAGMA dest.cache_size = -800000;")

    columns = "zipcode, number, street, street2, city, state, plus4, country" if not noplus4 else "zipcode, number, street, street2, city, state, country"
    sql = "INSERT OR IGNORE INTO dest.addresses " + "(" + columns + ")" + " SELECT " + columns + """ FROM main.addresses
WHERE
    -- replicate: if only4 and not plus4 and country == "US": skip
    -- So: keep rows where that condition is FALSE
    NOT (? = 1 AND (plus4 IS NULL OR plus4 = '') AND country = 'US');
"""
    c.execute(sql, (1 if only4 else 0,))
    
    if dropcitystate:
        print("Emptying city and state columns for US addresses")
        c.execute("UPDATE addresses SET city='', state='' WHERE country='US'")
    
    connection.commit()
    
    connection.close()

    print("\nVacuuming...")
    connection2.executescript("VACUUM")
    print("Adding indexes...")
    print("  Index 1/3")
    c2.execute("CREATE INDEX `zipcode_number` ON `addresses` (`zipcode`,`number`)")
    print("  Index 2/3")
    c2.execute("CREATE INDEX `state_city` ON `addresses` (`state`, `city`)")
    print("  Index 3/3")
    c2.execute("CREATE INDEX `number_street` ON `addresses` (`number`,`street`)")
    print("Analyzing and optimizing...")
    connection2.executescript("ANALYZE; PRAGMA optimize;")
    connection2.commit()
    connection2.close()
    print("Done!")

parser = ArgumentParser(description="Create the 'lite' version of the address SQLite database: no coordinates or source columns.")

parser.add_argument('src_db', help='"Full" SQLite database')
parser.add_argument('dest_db', help='Output database with some columns and indexes removed')
parser.add_argument("--only4", help="Skip US records missing the ZIP+4.", action="store_true")
parser.add_argument("--noplus4", help="Remove the 'plus4' column from the output database.", action="store_true")
parser.add_argument("--dropcitystate", help="Don't include city and state for US addresses.", action="store_true")

if __name__ == "__main__":
    args = parser.parse_args()
    process(args.src_db, args.dest_db, args.only4, args.noplus4, args.dropcitystate)
