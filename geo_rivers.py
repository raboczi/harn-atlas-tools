#!/usr/bin/python
"""
Detect all rivers.
"""
import sys
import argparse
import psycopg2

EPS = 0.006

def make_valid(table, cursor, merge, line_id):
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
        UPDATE {table}
        SET wkb_geometry = '{merge[0][0]}'::geometry
        WHERE id = {line_id}""")

def main():
    """Main method."""
    parser = argparse.ArgumentParser(
        prog=sys.argv[0],
        description='Create vegetation areas from postgis database.')
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

    cursor.execute(f"""
        SELECT id, ST_Buffer(ST_MakePolygon(wkb_geometry), 0.001)
        FROM {args.table}_lines
        WHERE type LIKE '%STREAM%' AND ST_IsClosed(wkb_geometry) AND style = 'fill: #36868d;'""")
    rows = cursor.fetchall()
    print(f"Thinning area rivers: {len(rows)}")
    for row in rows:
        cursor.execute(f"""
            SELECT (ST_Dump(CG_ApproximateMedialAxis('{row[1]}'::geometry))).geom""")
        axis = cursor.fetchall()
        if args.verbose:
            print(f"- axe {row[0]}")
        make_valid(f"{args.table}_lines", cursor, [l[0] for l in axis], row[0])

    print(f"Connect start points of rivers to rivers")
    cursor.execute(f"""
        SELECT tl.id, tr.id, ST_ClosestPoint(tl.wkb_geometry, ST_StartPoint(tr.geo))
        FROM {args.table}_lines AS tl INNER JOIN LATERAL (
          SELECT ts.id, ts.wkb_geometry FROM {args.table}_lines AS ts
          WHERE ts.id <> tl.id AND ts.type LIKE '%STREAM%' AND
            ST_Distance(tl.wkb_geometry, ST_StartPoint(ts.wkb_geometry)) < {EPS} AND
            ST_Distance(tl.wkb_geometry, ST_StartPoint(ts.wkb_geometry)) > 0)
        AS tr (id, geo) ON TRUE
        WHERE tl.type LIKE '%STREAM%'""")
    pt_lines = cursor.fetchall()
    print(f"Shift {len(pt_lines)} river-starts onto rivers")
    for pt_line in pt_lines:
        if args.verbose:
            print(f"- start {pt_line[1]} on {pt_line[0]}")
        cursor.execute(f"""
            UPDATE {args.table}_lines
            SET wkb_geometry = ST_Snap(wkb_geometry, '{pt_line[2]}'::geometry, 0)
            WHERE id = {pt_line[0]}""")
        cursor.execute(f"""
            UPDATE {args.table}_lines
            SET wkb_geometry = ST_SetPoint(wkb_geometry, 1, '{pt_line[2]}'::geometry)
            WHERE id = {pt_line[1]}""")

    print(f"Connect end points of rivers to rivers")
    cursor.execute(f"""
        SELECT tl.id, tr.id, ST_ClosestPoint(tl.wkb_geometry, ST_EndPoint(tr.geo))
        FROM {args.table}_lines AS tl INNER JOIN LATERAL (
          SELECT ts.id, ts.wkb_geometry FROM {args.table}_lines AS ts
          WHERE ts.id <> tl.id AND ts.type LIKE '%STREAM%' AND
            ST_Distance(tl.wkb_geometry, ST_EndPoint(ts.wkb_geometry)) < {EPS} AND
            ST_Distance(tl.wkb_geometry, ST_EndPoint(ts.wkb_geometry)) > 0)
        AS tr (id, geo) ON TRUE
        WHERE tl.type LIKE '%STREAM%'""")
    pt_lines = cursor.fetchall()
    print(f"Shift {len(pt_lines)} river-end onto rivers")
    for pt_line in pt_lines:
        if args.verbose:
            print(f"- end {pt_line[1]} on {pt_line[0]}")
        cursor.execute(f"""
            UPDATE {args.table}_lines
            SET wkb_geometry = ST_Snap(wkb_geometry, '{pt_line[2]}'::geometry, 0)
            WHERE id = {pt_line[0]}""")
        cursor.execute(f"""
            UPDATE {args.table}_lines
            SET wkb_geometry = ST_SetPoint(wkb_geometry, -1, '{pt_line[2]}'::geometry)
            WHERE id = {pt_line[1]}""")

    conn.commit()

if __name__ == '__main__':
    main()
