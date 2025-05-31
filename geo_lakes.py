#!/usr/bin/python
"""
Evaluates the lakes on the map. Assumes elevations to have been
done.
"""
import sys
import argparse
import psycopg2

EPS = 0.01

def main():
    """Main method."""
    parser = argparse.ArgumentParser(
        prog=sys.argv[0],
        description='Create Lakes form postgis database.')
    parser.add_argument(
        '-d', '--database', dest='db', required=True,
        help='db to connect to user:password@dbname:host:port')
    parser.add_argument(
        '-t', '--table', dest='table', required=True,
        help='table prefix; _pts and _lines will be added')
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='verbose', required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(
        user=f"{args.db.split('@')[0].split(':')[0]}",
        password=f"{args.db.split('@')[0].split(':')[1]}",
        database=f"{args.db.split('@')[1].split(':')[0]}",
        host=f"{args.db.split('@')[1].split(':')[1]}",
        port=f"{args.db.split('@')[1].split(':')[2]}")
    cursor = conn.cursor()

    # Initialize
    cursor.execute(f"""
        ALTER TABLE {args.table}_lines ALTER id SET NOT NULL;
        SELECT id, wkb_geometry FROM {args.table}_lines WHERE type LIKE '%LAKES%'""")
    print(f"Identifying lines: {cursor.fetchall()[0][0]}")

    # Remove pathological lines
    print("Remove lines of length 3")
    cursor.execute(f"""
        DELETE FROM {args.table}_lines
        WHERE type LIKE '%LAKES%' AND ST_NumPoints(wkb_geometry) < 4
        OR ST_Length(wkb_geometry) < {EPS}""")

    # All colored closed lines are lakes
    print("Elevate all lakes")
    cursor.execute(f"""
        UPDATE {args.table}_lines SET type = 'Lake'
        WHERE ST_IsClosed(wkb_geometry) AND type LIKE '%LAKES%' AND style LIKE '%fill: #d4effc%'""")

    conn.commit()

if __name__ == '__main__':
    main()
