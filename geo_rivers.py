#!/usr/bin/python
"""
Detect all rivers.
"""
import sys
import argparse
import psycopg2

EPS = 0.006

def make_valid(table, cursor, merge):
    """Removes the smallest segments until a single line remains. Update."""
    multi_line = True
    while multi_line:
        sql_array = "'" + "'::geometry, '".join(merge) + "'::geometry"
        cursor.execute(f"""
            SELECT ST_AsText(geo) FROM (
              SELECT (ST_Dump(ST_LineMerge(ST_Union(ARRAY[{sql_array}])))).geom)
            AS lines (geo) ORDER BY ST_Length(geo) DESC""")
        merge = cursor.fetchall()
        if len(merge) == 1:
            break
        merge = [m[0] for m in merge[:-1]]

    cursor.execute(f"""
        INSERT INTO {table} (id, name, type, wkb_geometry)
        VALUES (nextval('serial'), 'temporary new', 'River',
          '{merge[0][0]}'::geometry)""")

def handle(with_clause, r_table, w_table, cursor, vertex, level, old):
    """Creates rivers for all lines. Update."""
    cursor.execute(f"""
        {with_clause}
        SELECT id, wkb_geometry,
          ST_ClosestPoint('{old}'::geometry, ST_{vertex.capitalize()}Point(wkb_geometry))
        FROM {r_table}
        WHERE name LIKE 'temporary%' AND type NOT LIKE 'River/%' AND
          ST_Distance('{old}'::geometry,
            ST_{vertex.capitalize()}Point(wkb_geometry)) < {EPS}""")
    lines = cursor.fetchall()
    print(f"Shift {len(lines)} river {vertex}s")
    idx = 0 if vertex == 'start' else -1
    for pts in lines:
        cursor.execute(f"""
            INSERT INTO {w_table} (id, name, type, wkb_geometry)
            VALUES (nextval('serial'), '-', 'River/{level}/Mouth:{vertex}',
              ST_SetPoint('{pts[1]}'::geometry, {idx}, '{pts[2]}'::geometry))""")
        cursor.execute(f"""
            UPDATE {w_table} SET name = '-' WHERE id = {pts[0]}""")
    return len(lines)

def main():
    """Main method."""
    parser = argparse.ArgumentParser(
        prog=sys.argv[0],
        description='Create rivers from postgis database.')
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
    table = f"{args.table}_lines"

    conn = psycopg2.connect(
        user=f"{args.db.split('@')[0].split(':')[0]}",
        password=f"{args.db.split('@')[0].split(':')[1]}",
        database=f"{args.db.split('@')[1].split(':')[0]}",
        host=f"{args.db.split('@')[1].split(':')[1]}",
        port=f"{args.db.split('@')[1].split(':')[2]}")
    cursor = conn.cursor()

    # Initialize
    cursor.execute(f"""
        CREATE TEMP SEQUENCE IF NOT EXISTS serial START 500000""")

    cursor.execute(f"""
        SELECT count(*) FROM {table}
        WHERE type LIKE '%STREAM%' AND (NOT ST_IsClosed(wkb_geometry) OR
          ST_IsClosed(wkb_geometry) AND style LIKE '%fill: #36868d%')""")
    print(f"Found {cursor.fetchall()[0][0]} rivers")

    # These are all extended rivers
    # (Buffer because there are strange duplicates)
    cursor.execute(f"""
        SELECT id, ST_Buffer(ST_MakePolygon(wkb_geometry),0.001)
        FROM {table}
        WHERE type LIKE '%STREAM%' AND ST_IsClosed(wkb_geometry) AND
          style LIKE '%fill: #36868d%'""")
    rows = cursor.fetchall()
    print(f"Thinning area rivers: {len(rows)}")
    for row in rows:
        # I don't know why CG_ApproximateMedialAxis is only a proxy
        # for CG_StraightSkeleton.  With the versions I run, it should
        # give a line.
        cursor.execute(f"""
            SELECT (ST_Dump(CG_ApproximateMedialAxis('{row[1]}'::geometry))).geom""")
        axis = cursor.fetchall()
        if args.verbose:
            print(f"- Create axis for {row[0]}")
        make_valid(table, cursor, [l[0] for l in axis])
    cursor.execute(f"""
        UPDATE {table} SET name = 'temporary'
        WHERE type LIKE '%STREAM%' AND NOT ST_IsClosed(wkb_geometry)""")

    # Shores
    print(f"Handle outflows to shores")
    cursor.execute(f"""
        SELECT ST_Union(wkb_geometry) FROM {table} WHERE type = '0' OR type LIKE '%Lake'""")
    old = cursor.fetchall()[0][0]
    cursor.execute(f"""
        SELECT ST_Union(ST_MakePolygon(wkb_geometry)) FROM {table}
        WHERE type = '0'""") # not Lakes!
    zero = cursor.fetchall()
    with_clause = f"""WITH lines (id, wkb_geometry, name, type) AS (
          SELECT id, (ST_Dump(ST_CollectionExtract(ST_Intersection(
            wkb_geometry, '{zero[0][0]}'::geometry), 2))).geom AS wkb_geometry,
            'temporary', 'unused'
          FROM {table}
          WHERE name LIKE 'temporary%')"""
    length = handle(with_clause, "lines", table, cursor, "start", 0, old)
    length += handle(with_clause, "lines", table, cursor, "end", 0, old)

    # Recurse rivers into rivers
    level = 0
    while length > 0:
        level = level + 1
        cursor.execute(f"""
            SELECT ST_Union(wkb_geometry) FROM {table}
            WHERE type LIKE 'River/{level-1}/%'""")
        old = cursor.fetchall()[0][0]
        print(f"Handle outflows level {level}")
        length = handle("", table, table, cursor, "start", level, old)
        length += handle("", table, table, cursor, "end", level, old)

    # Clean up
    cursor.execute(f"""
        DELETE FROM {table} WHERE name = 'temporary new'""")

    cursor.execute(f"""
        SELECT count(*) FROM {table}
        WHERE type LIKE '%STREAM%' AND NOT ST_IsClosed(wkb_geometry) AND NOT name = '-'""")

    print(f"Leave {cursor.fetchall()[0][0]} rivers")
    conn.commit()

if __name__ == '__main__':
    main()
