#!/usr/bin/python
"""
Connects all roads with either endpoints or the next closest road
if within reasonable distance.
"""
import sys
import argparse
import psycopg2

EPS = 0.005

def make_valid(table, cursor, merge, line_id):
    """Removes the smallest segments until a single line remains. Update."""
    multi_line = True
    while multi_line:
        sql_array = "'" + "'::geometry, '".join(merge) + "'::geometry"
        cursor.execute(f"""
            SELECT geo FROM (
              SELECT (ST_Dump(ST_LineMerge(ST_Union(ARRAY[{sql_array}])))).geom)
            AS lines (geo) ORDER BY ST_Length(geo) DESC""")
        merge = cursor.fetchall()
        if len(merge) == 1:
            break
        merge = [m[0] for m in merge[:-1]]

    cursor.execute(f"""
        UPDATE {table}
        SET wkb_geometry = '{merge[0][0]}'::geometry
        WHERE id = {line_id}""")

def connect(verbose, cursor, table, point, pt_idx):
    """Connect a road endpoint to the nearest other road."""
    cursor.execute(f"""
        SELECT id, wkb_geometry, ST_Distance('{point[pt_idx]}'::geometry, wkb_geometry)
        FROM {table}
        WHERE type LIKE '%ROADS%' AND ST_Distance('{point[pt_idx]}'::geometry, wkb_geometry) < {EPS}
          AND id <> {point[0]}
        ORDER BY ST_Distance('{point[pt_idx]}'::geometry, wkb_geometry)
        ASC LIMIT 1""")
    lines = cursor.fetchall()
    if len(lines) > 0 and lines[0][2] > 0:
        line = lines[0]
        if verbose:
            print(f"- connect to {line[0]} at {'start' if pt_idx == 1 else 'end'}")
        cursor.execute(f"""
            UPDATE {table}
            SET wkb_geometry = ST_SetPoint(wkb_geometry, {0 if pt_idx == 1 else -1}, tr.p)
            FROM (
              SELECT (tl.p).geom FROM (SELECT ST_DumpPoints('{line[1]}'::geometry))
              AS tl (p)
              ORDER BY ST_Distance((tl.p).geom, '{point[pt_idx]}'::geometry)
              ASC LIMIT 1)
            AS tr (p)
            WHERE id = {point[0]}""")

def main():
    """Main method."""
    parser = argparse.ArgumentParser(
        prog=sys.argv[0],
        description='Load GEOJSON elevation lines and points with elevation labels. ' +
        'Tries to assign the correct labels to the elevation lines')
    parser.add_argument(
        '-d', '--database', dest='db', required=True,
        help='db to connect to user:password@dbname')
    parser.add_argument(
        '-t', '--table', dest='table', required=True,
        help='table prefix; _pts and _lines will be added')
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='verbose', required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(
        database=f"{args.db.split('@')[1]}",
        user=f"{args.db.split('@')[0].split(':')[0]}",
        password=f"{args.db.split('@')[0].split(':')[1]}",)
    cursor = conn.cursor()

    # Initialize
    cursor.execute(f"""
        SELECT count(*) FROM {args.table}_lines WHERE type LIKE '%ROADS%'""")
    print(f"Identifying lines: {cursor.fetchall()[0][0]}")

    # Remove pathological lines
    print("Remove lines of length 3")
    cursor.execute(f"""
        DELETE FROM {args.table}_lines
        WHERE type LIKE '%ROADS%' AND ST_NumPoints(wkb_geometry) < 4
        OR ST_Length(wkb_geometry) < {EPS}""")

    print("Validate lines")
    cursor.execute(f"""
        SELECT id, wkb_geometry FROM {args.table}_lines WHERE type LIKE '%ROADS%'""")
    lines = cursor.fetchall()
    for line in lines:
        make_valid(f"{args.table}_lines", cursor, [line[1]], line[0])

    # Find nearby towns
    print("Find towns")
    cursor.execute(f"""
        SELECT id, wkb_geometry FROM {args.table}_pts
        WHERE type LIKE '%TOWNS%' OR type LIKE '%MINES%' OR type LIKE '%CITIES%'""")
    points = cursor.fetchall()

    # Match towns and roads
    print("Connect {len(points)} locations to roads")
    for point in points:
        if args.verbose:
            print(f"- connect {point[0]}")
        cursor.execute(f"""
            SELECT id, wkb_geometry, ST_Distance(wkb_geometry, '{point[1]}'::geometry) FROM {args.table}_lines
            WHERE type LIKE '%ROADS%' AND ST_Distance('{point[1]}'::geometry, wkb_geometry) < {EPS}
            ORDER BY ST_Distance(wkb_geometry, '{point[1]}'::geometry) ASC""")
        lines = cursor.fetchall()
        while len(lines) > 0 and lines[0][2] > 0:
            line = lines[0]
            if args.verbose:
                print(f"- - shift line {line[0]}")
            cursor.execute(f"""
                UPDATE {args.table}_lines
                SET wkb_geometry = ST_SetPoint('{line[1]}'::geometry, tr.idx - 1, '{point[1]}'::geometry)
                FROM (
                  SELECT ((tl.p).path)[1] FROM (SELECT ST_DumpPoints('{line[1]}'::geometry))
                  AS tl (p)
                  ORDER BY ST_Distance((tl.p).geom, '{point[1]}'::geometry)
                  ASC LIMIT 1)
                AS tr (idx)
                WHERE id = {line[0]}""")
            cursor.execute(f"""
                SELECT id, wkb_geometry, ST_Distance(wkb_geometry, '{point[1]}'::geometry) FROM {args.table}_lines
                WHERE type LIKE '%ROADS%' AND ST_Distance('{point[1]}'::geometry, wkb_geometry) < {EPS}
                ORDER BY ST_Distance(wkb_geometry, '{point[1]}'::geometry) ASC""")
            lines = cursor.fetchall()

    cursor.execute(f"""
        SELECT id, ST_StartPoint(wkb_geometry), ST_EndPoint(wkb_geometry) FROM {args.table}_lines
        WHERE type LIKE '%ROADS%'
        ORDER BY id""")
    points = cursor.fetchall()
    print("Connect {len(points)} roads to roads")
    for point in points:
        if args.verbose:
            print(f"- connect line {point[0]}")
        connect(args.verbose, cursor, f"{args.table}_lines", point, 1)
        connect(args.verbose, cursor, f"{args.table}_lines", point, 2)

    conn.commit()

if __name__ == '__main__':
    main()
