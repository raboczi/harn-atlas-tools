#!/usr/bin/python
"""
Evaluates the coast lines on the map. Does not work well with partial
coast lines that are not closed.
"""
import sys
import argparse
import psycopg2

EPST = 0.1
EPSL = 0.007
EPSB = 0.01

def shortest_connect(table, cursor, line_id):
    """
    Returns the id of the closest line, the type, the geometry of it,
    of the original line, and of the connecting line.
    """
    cursor.execute(f"""
        SELECT wkb_geometry FROM {table} WHERE id = {line_id}""")
    line_geo = cursor.fetchall()[0][0]
    p_11 = f"(1, ST_StartPoint('{line_geo}'::geometry))"
    p_12 = f"(2, ST_EndPoint('{line_geo}'::geometry))"
    p_21 = f"(1, ST_StartPoint(main.wkb_geometry))"
    p_22 = f"(2, ST_EndPoint(main.wkb_geometry))"
    cursor.execute(f"""
        SELECT add_id, add_type, add_geo, line_geo, connect_geo FROM (
          SELECT main.id, main.type, main.wkb_geometry, '{line_geo}', (
            WITH pts1 (i, p) AS (VALUES {p_11}, {p_12}), 
              pts2 (i, p) AS (VALUES {p_21}, {p_22})
            SELECT ST_MakeLine(pt1.p, pt2.p) FROM pts1 AS pt1 CROSS JOIN pts2 AS pt2
            WHERE (main.id <> {line_id} OR pt1.i <> pt2.i)
            ORDER BY ST_Distance(pt1.p, pt2.p) ASC LIMIT 1) AS connect
          FROM {table} AS main)
          AS connects (add_id, add_type, add_geo, line_geo, connect_geo)
        WHERE ST_Length(connects.connect_geo) < {EPSL} AND
          (connects.add_type LIKE '%COASTLINE%' OR
            connects.add_type = '0')
        ORDER BY ST_Length(connects.connect_geo) ASC LIMIT 1""")
    ret = cursor.fetchall()
    return ret

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
        SELECT count(*) FROM {args.table}_lines WHERE type LIKE '%COASTLINE%'""")
    print(f"Identifying lines: {cursor.fetchall()[0][0]}")

    # Remove pathological lines
    print("Remove lines of length 3")
    cursor.execute(f"""
        DELETE FROM {args.table}_lines
        WHERE type LIKE '%COASTLINE%' AND ST_NumPoints(wkb_geometry) < 4
        OR ST_Length(wkb_geometry) < {EPSL}""")

    print("Validate lines")
    cursor.execute(f"""
        SELECT id, wkb_geometry FROM {args.table}_lines WHERE type LIKE '%COASTLINE%'""")
    lines = cursor.fetchall()
    for line in lines:
        make_valid(f"{args.table}_lines", cursor, [line[1]], line[0])

    # Connect
    print("Connect unlabeled and like-labelled lines")
    cursor.execute(f"""
        SELECT id FROM {args.table}_lines WHERE type LIKE '%COASTLINE%' AND NOT ST_IsClosed(wkb_geometry)
        ORDER BY id""")
    lines = cursor.fetchall()
    deleted = []
    for line in lines:
        if line[0] in deleted:
            continue
        if args.verbose:
            print(f"- connect {line[0]}")
        connect = shortest_connect(f"{args.table}_lines", cursor, line[0])
        while len(connect) > 0:
            if args.verbose:
                print(f"- - with {connect[0][0]}")
            make_valid(f"{args.table}_lines", cursor, connect[0][2:], line[0])
            if line[0] == connect[0][0]:
                break
            if args.verbose:
                print(f"- - remove {connect[0][0]}")
            cursor.execute(f"""
                DELETE FROM {args.table}_lines WHERE id = {connect[0][0]}""")
            deleted.append(connect[0][0])
            connect = shortest_connect(f"{args.table}_lines", cursor, line[0])

    cursor.execute(f"""
        UPDATE {args.table}_lines
        SET type = 'RIVER'
        WHERE ST_IsClosed(wkb_geometry) AND type LIKE '%COASTLINE%' AND
          ST_IsEmpty(ST_Buffer(ST_MakePolygon(wkb_geometry),
            -(ST_MinimumBoundingRadius(ST_MakePolygon(wkb_geometry))).radius * {EPST}))""")

    # Melderyn
    print(f"Special: Melderyn Isle")
    # Make smaller by 2*EPSB and then create union
    cursor.execute(f"""
        SELECT id, geo FROM (
          SELECT id, (ST_Dump(ST_Boundary(ST_Union(ST_Buffer(ST_Buffer(ST_MakePolygon(wkb_geometry), {EPSB}), -2 * {EPSB}),
            ST_MakePolygon(wkb_geometry))))).geom
          FROM {args.table}_lines
          WHERE ST_IsClosed(wkb_geometry) AND type LIKE '%COASTLINE%' AND
            ST_Covers(ST_MakePolygon(wkb_geometry), ST_GeomFromText('POINT(-16 41)', 4326)))
        AS lines (id, geo)""")
    poly = cursor.fetchall()
    if args.verbose:
        print(f"- {poly[0][0]}")
    make_valid(f"{args.table}_lines", cursor, [p[1] for p in poly], poly[0][0])

    # All (non-distorted) closed coast
    cursor.execute(f"""
        UPDATE {args.table}_lines
        SET type = '0'
        WHERE type LIKE '%COASTLINE%' AND ST_IsClosed(wkb_geometry)""")

    cursor.execute(f"""
        SELECT count(*) FROM {args.table}_lines WHERE type LIKE '%COASTLINE%'""")
    print(f"Remaining lines: {cursor.fetchall()[0][0]}")
    conn.commit()

if __name__ == '__main__':
    main()
