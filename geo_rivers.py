#!/usr/bin/python
"""
Detect all rivers.
"""
import sys
import argparse
import psycopg2

EPS = 0.0045 # must be a bit bigger than EPSB from geo_coast

def make_axis(verbose, table, cursor, merge, bound):
    """Removes the smallest segments until a single line remains. Update."""
    sql_array = "'" + "'::geometry, '".join(merge) + "'::geometry"
    cursor.execute(f"""
        WITH lines (geo) AS (
          SELECT (ST_Dump(ST_Union(ARRAY[{sql_array}]))).geom)
        SELECT (ST_Dump(ST_LineMerge(ST_Union(geo)))).geom FROM lines
        WHERE ST_Covers(ST_Buffer('{bound[1]}'::geometry, -{EPS/50}), geo)""")
    merge = cursor.fetchall()
    if verbose:
        print(f"- Create axis for {bound[0]} with {len(merge)} medial(s)")
    for m in merge:
        sql_array = "'" + "'::geometry, '".join(m) + "'::geometry"
        cursor.execute(f"""
            INSERT INTO {table} (id, name, type, wkb_geometry)
            VALUES (nextval('serial'), 'candidate', 'STREAMS', ST_Union(ARRAY[{sql_array}]))""")

def handle_lakes(args, cursor, vertex, level, lakes):
    """Add lakes to river network."""
    other_vertex = 'end' if vertex == 'start' else 'start'
    print(f"Handle lakes level {level} for {vertex}")
    for lake in lakes:
        cursor.execute(f"""
            SELECT id FROM {args.table}_lines
            WHERE type LIKE 'River/{level}/Mouth:{other_vertex}' AND
              ST_Distance(ST_MakePolygon('{lake[1]}'::geometry),
                ST_{vertex.capitalize()}Point(wkb_geometry)) < {EPS} AND
              ST_Distance(ST_MakePolygon('{lake[1]}'::geometry),
                ST_{vertex.capitalize()}Point(wkb_geometry)) > 0""")
        lines = cursor.fetchall() # lines with v in lake and ov connected
        for pts in lines:
            if args.verbose:
                print(f"- line {pts} in lake {lake[0]}")
            cursor.execute(f"""
                WITH lines(geo) AS (
                  SELECT (ST_Dump(
                    ST_Difference(ST_MakeValid(wkb_geometry),
                      ST_MakeValid(ST_MakePolygon('{lake[1]}'::geometry))))).geom
                  FROM {args.table}_lines
                  WHERE id = {pts[0]})
                SELECT lines.geo FROM lines ORDER BY ST_Length(lines.geo) DESC
                LIMIT 1""")
            line = cursor.fetchall()[0]
            cursor.execute(f"""
                UPDATE {args.table}_lines SET wkb_geometry = '{line[0]}'::geometry
                WHERE id = {pts[0]}""")
        if len(lines) > 0:
            handle_river(args, cursor, vertex, level + 1, lake[1])
            handle_river(args, cursor, other_vertex, level + 1, lake[1])

def handle_river(args, cursor, vertex, level, old):
    """Creates rivers for all lines. Update."""
    idx = 0 if vertex == 'start' else -1
    print(f"Handle outflows level {level} for {vertex}")
    cursor.execute(f"""
        SELECT id, name, type FROM {args.table}_lines
        WHERE name = 'candidate' AND type NOT LIKE 'River/%' AND
          ST_Distance('{old}'::geometry,
            ST_{vertex.capitalize()}Point(wkb_geometry)) < {EPS}""")
    lines = cursor.fetchall()
    print(f"Shift {len(lines)} river {vertex}s")
    for pts in lines:
        if args.verbose:
            print(f"- line {pts} with {vertex}")
        while True:
            cursor.execute(f"""
                SELECT ST_Distance('{old}'::geometry,
                  ST_{vertex.capitalize()}Point(wkb_geometry)),
                  ST_NPoints(wkb_geometry)
                FROM {args.table}_lines
                WHERE id = {pts[0]}""")
            line = cursor.fetchall()
            if args.verbose:
                print(f"- - shorten {pts[0]}: {line[0][1]}")
            cursor.execute(f"""
                SELECT ST_Intersects('{old}'::geometry, wkb_geometry), ST_AsText(wkb_geometry)
                FROM {args.table}_lines WHERE id = {pts[0]}""")
            intersects = cursor.fetchall()[0]
            if not intersects[0]:
                break
            if line[0][1] < 3:
                print(f"ERROR: duplicate at {pts[0]}")
                cursor.execute(f"""
                    DELETE FROM {args.table}_lines WHERE id = {pts[0]}""")
                break
            cursor.execute(f"""
                UPDATE {args.table}_lines SET wkb_geometry =
                  ST_RemovePoint(wkb_geometry, {idx} * (1 - ST_NPoints(wkb_geometry)))
                WHERE id = {pts[0]}""")
        cursor.execute(f"""
            SELECT wkb_geometry,
              ST_ClosestPoint('{old}'::geometry, ST_{vertex.capitalize()}Point(wkb_geometry))
            FROM {args.table}_lines
            WHERE id = {pts[0]}""")
        pts2 = cursor.fetchall()
        if len(pts2) > 0:
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                VALUES (nextval('serial'), '-', 'River/{level}/Mouth:{vertex}',
                  ST_SetPoint(ST_RemoveRepeatedPoints('{pts2[0][0]}'::geometry), {idx},
                    '{pts2[0][1]}'::geometry))""")
            cursor.execute(f"""
                DELETE FROM {args.table}_lines WHERE id = {pts[0]}""")
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
    parser.add_argument(
        '-T', '--test', action='store_true', help='run tests instead',
        required=False)
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
        CREATE TEMP SEQUENCE IF NOT EXISTS serial START 500000""")

    if args.test:
        # Priming test DB
        cursor.execute(f"""
            DELETE FROM {args.table}_lines""")
        cursor.execute(f"""
            INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
              VALUES (nextval('serial'), '-', '0',
                'LINESTRING(10 10, 30 10, 30 20, 10 20, 10 10)'::geometry)""")

        for typ in ["Lake/test", "COASTLINE/tmp-lake"]:
            offset = 10 if typ == "Lake/test" else 20
            if args.verbose:
                print(f"Prime with {typ}")
                print("1. 0/Mouth:start")                                   # <-
                print("2. 0/Mouth:end")                                     # ->
                print("3. 1/Mouth:start at 0/Mouth:start")                  # <-O<-
                print("4. 1/Mouth:end at 0/Mouth:start")                    # <-O->
                print("5. 1/Mouth:start at 0/Mouth:end")                    # ->O<-
                print("6. 1/Mouth:end at 0/Mouth:end")                      # ->O->
                print("7. 2/Mouth:start at 1/Mouth:start at 0/Mouth:start") # <-O<-O<-
                print("8. 2/Mouth:end at 1/Mouth:start at 0/Mouth:start")   # <-O<-O->
                print("9. 2/Mouth:end at 1/Mouth:end at 0/Mouth:start")     # <-O->O->
                print("10. 2 x 1/Mouth:end at 0/Mouth:end")                 # ->O->->
                print("11. 1/Mouth:start and 1/Mouth:end at 0/Mouth:end")   # ->O-><-
            # Relies on EPS = 0.0045
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '1', 'STREAMS',
                    'LINESTRING({offset}.100 10.001, {offset}.100 10.099)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '2', 'STREAMS',
                    'LINESTRING({offset}.200 10.099, {offset}.200 10.001)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '3a', 'STREAMS',
                    'LINESTRING({offset}.300 10.001, {offset}.300 10.099)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '3b', '{typ}',
                    'LINESTRING({offset}.300 10.100, {offset}.310 10.110,
                      {offset}.300 10.120, {offset}.290 10.110, {offset}.300 10.100)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '3c', 'STREAMS',
                    'LINESTRING({offset}.300 10.121, {offset}.300 10.199)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '4a', 'STREAMS',
                    'LINESTRING({offset}.400 10.001, {offset}.400 10.099)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '4b', '{typ}',
                    'LINESTRING({offset}.400 10.100, {offset}.410 10.110,
                      {offset}.400 10.120, {offset}.390 10.110, {offset}.400 10.100)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '4c', 'STREAMS',
                    'LINESTRING({offset}.400 10.199, {offset}.400 10.121)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '5a', 'STREAMS',
                    'LINESTRING({offset}.500 10.099, {offset}.500 10.001)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '5b', '{typ}',
                    'LINESTRING({offset}.500 10.100, {offset}.510 10.110,
                      {offset}.500 10.120, {offset}.490 10.110, {offset}.500 10.100)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '5c', 'STREAMS',
                    'LINESTRING({offset}.500 10.121, {offset}.500 10.199)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '6a', 'STREAMS',
                    'LINESTRING({offset}.600 10.099, {offset}.600 10.001)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '6b', '{typ}',
                    'LINESTRING({offset}.600 10.100, {offset}.610 10.110,
                      {offset}.600 10.120, {offset}.590 10.110, {offset}.600 10.100)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '6c', 'STREAMS',
                    'LINESTRING({offset}.600 10.199, {offset}.600 10.121)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '7a', 'STREAMS',
                    'LINESTRING({offset}.700 10.001, {offset}.700 10.099)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '7b', '{typ}',
                    'LINESTRING({offset}.700 10.100, {offset}.710 10.110,
                      {offset}.700 10.120, {offset}.690 10.110, {offset}.700 10.100)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '7c', 'STREAMS',
                    'LINESTRING({offset}.700 10.121, {offset}.700 10.199)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '7d', '{typ}',
                    'LINESTRING({offset}.700 10.200, {offset}.710 10.210,
                      {offset}.700 10.220, {offset}.690 10.210, {offset}.700 10.200)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '7e', 'STREAMS',
                    'LINESTRING({offset}.700 10.221, {offset}.700 10.299)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '8a', 'STREAMS',
                    'LINESTRING({offset}.800 10.001, {offset}.800 10.099)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '8b', '{typ}',
                    'LINESTRING({offset}.800 10.100, {offset}.810 10.110,
                      {offset}.800 10.120, {offset}.790 10.110, {offset}.800 10.100)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '8c', 'STREAMS',
                    'LINESTRING({offset}.800 10.121, {offset}.800 10.199)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '8d', '{typ}',
                    'LINESTRING({offset}.800 10.200, {offset}.810 10.210,
                      {offset}.800 10.220, {offset}.790 10.210, {offset}.800 10.200)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '8e', 'STREAMS',
                    'LINESTRING({offset}.800 10.299, {offset}.800 10.221)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '9a', 'STREAMS',
                    'LINESTRING({offset}.900 10.001, {offset}.900 10.099)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '9b', '{typ}',
                    'LINESTRING({offset}.900 10.100, {offset}.910 10.110,
                      {offset}.900 10.120, {offset}.890 10.110, {offset}.900 10.100)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '9c', 'STREAMS',
                    'LINESTRING({offset}.900 10.199, {offset}.900 10.121)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '9d', '{typ}',
                    'LINESTRING({offset}.900 10.200, {offset}.910 10.210,
                      {offset}.900 10.220, {offset}.890 10.210, {offset}.900 10.200)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '9e', 'STREAMS',
                    'LINESTRING({offset}.900 10.299, {offset}.900 10.221)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '10a', 'STREAMS',
                    'LINESTRING({offset+1}.000 10.099, {offset+1}.000 10.001)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '10b', '{typ}',
                    'LINESTRING({offset+1}.000 10.100, {offset+1}.010 10.110,
                      {offset+1}.000 10.120, {offset}.990 10.110, {offset+1}.000 10.100)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '10c', 'STREAMS',
                    'LINESTRING({offset}.980 10.199, {offset+1}.000 10.121)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '10d', 'STREAMS',
                    'LINESTRING({offset+1}.020 10.199, {offset+1}.000 10.121)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '11a', 'STREAMS',
                    'LINESTRING({offset+1}.000 10.099, {offset+1}.000 10.001)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '11b', '{typ}',
                    'LINESTRING({offset+1}.000 10.100, {offset+1}.010 10.110,
                      {offset+1}.000 10.120, {offset}.990 10.110, {offset+1}.000 10.100)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '11c', 'STREAMS',
                    'LINESTRING({offset}.980 10.199, {offset+1}.000 10.121)'::geometry)""")
            cursor.execute(f"""
                INSERT INTO {args.table}_lines (id, name, type, wkb_geometry)
                  VALUES (nextval('serial'), '11d', 'STREAMS',
                    'LINESTRING({offset+1}.000 10.121, {offset+1}.020 10.199)'::geometry)""")

    cursor.execute(f"""
        SELECT count(*) FROM {args.table}_lines
        WHERE type LIKE '%STREAMS%' AND (NOT ST_IsClosed(wkb_geometry) OR
          ST_IsClosed(wkb_geometry) AND style LIKE '%fill: #36868d%')""")
    print(f"Found {cursor.fetchall()[0][0]} rivers")

    # These are all extended rivers
    # (Buffer because there are strange duplicates)
    cursor.execute(f"""
        SELECT id, ST_Buffer(ST_MakePolygon(wkb_geometry), {EPS}/100)
        FROM {args.table}_lines
        WHERE type LIKE '%STREAMS%' AND ST_IsClosed(wkb_geometry) AND
          style LIKE '%fill: #36868d%'""")
    rows = cursor.fetchall()
    print(f"Thinning area rivers: {len(rows)}")
    for row in rows:
        cursor.execute(f"""
            SELECT CG_ApproximateMedialAxis('{row[1]}'::geometry)""")
        axis = cursor.fetchall()
        make_axis(args.verbose, f"{args.table}_lines", cursor, [l[0] for l in axis], row)
    cursor.execute(f"""
        UPDATE {args.table}_lines SET name = 'candidate'
        WHERE type LIKE '%STREAMS%' AND NOT ST_IsClosed(wkb_geometry)""")

    # Shores
    cursor.execute(f"""
        SELECT ST_Union(wkb_geometry) FROM {args.table}_lines WHERE type = '0'""")
    old_term = cursor.fetchall()[0][0]
    cursor.execute(f"""
        SELECT id, wkb_geometry FROM {args.table}_lines
        WHERE type = 'COASTLINE/tmp-lake' OR type LIKE 'Lake/%'""")
    lakes = cursor.fetchall()
    length = handle_river(args, cursor, "start", 0, old_term)
    length += handle_river(args, cursor, "end", 0, old_term)
    handle_lakes(args, cursor, "start", 0, lakes)
    handle_lakes(args, cursor, "end", 0, lakes)

    # Recurse rivers into rivers
    level = 0
    while length > 0:
        level = level + 1
        cursor.execute(f"""
            SELECT ST_Union(wkb_geometry) FROM {args.table}_lines
            WHERE type LIKE 'River/{level-1}/%'""")
        old_term = cursor.fetchall()[0][0]
        length = handle_river(args, cursor, "start", level, old_term)
        length += handle_river(args, cursor, "end", level, old_term)
        handle_lakes(args, cursor, "start", level, lakes)
        handle_lakes(args, cursor, "end", level, lakes)

#    cursor.execute(f"""
#        DELETE FROM {args.table}_lines WHERE name = 'candidate' AND NOT type LIKE 'River/%'""")

    cursor.execute(f"""
        SELECT count(*) FROM {args.table}_lines
        WHERE type LIKE '%STREAMS%' AND NOT ST_IsClosed(wkb_geometry) AND NOT name = '-'""")

    print(f"Leave {cursor.fetchall()[0][0]} rivers")
    if args.test:
        # Test count only, because no column is preserved
        cursor.execute(f"""
            SELECT count(*) FROM {args.table}_lines WHERE type LIKE 'River/%'""")
        assert cursor.fetchall()[0][0] == 50
        cursor.execute(f"""
            SELECT count(*) FROM {args.table}_lines WHERE type = 'River/0/Mouth:start'""")
        assert cursor.fetchall()[0][0] == 12
        cursor.execute(f"""
            SELECT count(*) FROM {args.table}_lines WHERE type = 'River/0/Mouth:end'""")
        assert cursor.fetchall()[0][0] == 10
        cursor.execute(f"""
            SELECT count(*) FROM {args.table}_lines WHERE type = 'River/1/Mouth:start'""")
        assert cursor.fetchall()[0][0] == 10
        cursor.execute(f"""
            SELECT count(*) FROM {args.table}_lines WHERE type = 'River/1/Mouth:end'""")
        assert cursor.fetchall()[0][0] == 12
        cursor.execute(f"""
            SELECT count(*) FROM {args.table}_lines WHERE type = 'River/2/Mouth:start'""")
        assert cursor.fetchall()[0][0] == 2
        cursor.execute(f"""
            SELECT count(*) FROM {args.table}_lines WHERE type = 'River/2/Mouth:end'""")
        assert cursor.fetchall()[0][0] == 4

        # Don't commit this!
    else:
        conn.commit()

if __name__ == '__main__':
    main()
