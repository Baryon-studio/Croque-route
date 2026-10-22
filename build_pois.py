#!/usr/bin/env python3
"""
Croque-Route : construit la base locale pois.json (enseignes + aires d'autoroute de France)
depuis OpenStreetMap, puis lance l'app sur ton ordinateur.

Usage (dans le dossier de l'app) :
    python3 build_pois.py              construit la base si elle n'existe pas, puis lance l'app
    python3 build_pois.py --refresh    reconstruit la base
    python3 build_pois.py --all        ajoute tous les fast-foods nommés (base plus grosse)
    python3 build_pois.py --no-serve   construit la base sans lancer l'app

Aucune dépendance : Python 3 seulement. La construction prend quelques minutes, une seule fois.
"""
import json, os, re, socket, sys, time, datetime, math
import urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pois.json")
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
# (libellé affiché dans l'app, motif de reconnaissance) : mêmes enseignes que l'app
BRANDS = [
    ("McDonald's", "McDonald"), ("Burger King", "Burger King"), ("KFC", "KFC"),
    ("Quick", "^Quick"), ("Courte Paille", "Courte ?Paille"), ("Buffalo Grill", "Buffalo Grill"),
    ("Flunch", "Flunch"), ("Subway", "Subway"), ("Paul", "^Paul$"),
    ("Brioche Dorée", "Brioche D"), ("Starbucks", "Starbucks"), ("O'Tacos", "O'?Tacos"),
]
RX = "|".join(p for _, p in BRANDS)
BRAND_RE = [re.compile(p, re.I) for _, p in BRANDS]
TYPES = ["fast_food", "restaurant", "cafe"]

# France métropolitaine + Corse, découpée en tuiles pour ne pas surcharger le serveur
LAT_EDGES = [41.2, 43.2, 44.8, 46.4, 48.0, 49.3, 51.3]
LON_EDGES = [-5.3, -1.0, 2.2, 5.5, 9.8]


def tiles():
    for a, b in zip(LAT_EDGES, LAT_EDGES[1:]):
        for c, d in zip(LON_EDGES, LON_EDGES[1:]):
            yield (a, c, b, d)


def build_query(bb, all_fastfood):
    s, w, n, e = bb
    box = f"({s},{w},{n},{e})"
    extra = f'nwr["amenity"="fast_food"]["name"]{box};' if all_fastfood else ""
    # Filtres exacts (clé=valeur) : Overpass les traite avec ses index, bien plus vite qu'une expression régulière.
    near = "".join(f'nwr["amenity"="{a}"](around.sa:120);' for a in TYPES)
    brands = "".join(
        f'nwr["amenity"="{a}"]["brand"~"{RX}",i]{box};nwr["amenity"="{a}"]["name"~"{RX}",i]{box};'
        for a in TYPES
    )
    return f"""[out:json][timeout:90];
(nwr["highway"="services"]{box};nwr["highway"="rest_area"]{box};)->.sa;
({near})->.ap;
(
  {brands}
  .ap;
  {extra}
)->.u;
.u out center tags;
.ap out ids;
"""


def fetch_tile(query, label):
    """Interroge Overpass avec nouvelles tentatives et serveurs de secours."""
    data = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(3):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(ep, data=data, headers={"User-Agent": "croque-route/1.0"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as ex:
                print(f"    {label}: {ep.split('/')[2]} a échoué ({type(ex).__name__}), je réessaie…", flush=True)
                time.sleep(3)
        time.sleep(10 * (attempt + 1))
    return None


def center(el):
    if "lat" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    return (c["lat"], c["lon"]) if c else (None, None)


def detect_brand(tags):
    for i, rx in enumerate(BRAND_RE):
        if (tags.get("brand") and rx.search(tags["brand"])) or (tags.get("name") and rx.search(tags["name"])):
            return i
    return -1


def process(tile_results, all_fastfood):
    """Transforme les réponses Overpass en liste compacte de lieux."""
    pois = {}
    for res in tile_results:
        if not res:
            continue
        aire_ids = set()
        for el in res.get("elements", []):
            if "tags" not in el and "lat" not in el and "center" not in el:
                aire_ids.add((el["type"], el["id"]))  # entrée « ids » : lieu situé dans une aire
        for el in res.get("elements", []):
            tags = el.get("tags")
            if not tags or tags.get("amenity") not in TYPES:
                continue
            lat, lon = center(el)
            if lat is None:
                continue
            key = (el["type"], el["id"])
            name = tags.get("name") or tags.get("brand")
            if not name:
                continue
            b = detect_brand(tags)
            in_aire = key in aire_ids
            if b < 0 and not in_aire and not (all_fastfood and tags.get("amenity") == "fast_food"):
                continue
            addr = ", ".join(x for x in [
                " ".join(y for y in [tags.get("addr:housenumber"), tags.get("addr:street")] if y),
                tags.get("addr:city"),
            ] if x)
            rec = [round(lat, 5), round(lon, 5), name, b, TYPES.index(tags["amenity"]),
                   1 if in_aire else 0, tags.get("opening_hours", ""), addr]
            if key in pois:  # un même lieu vu dans deux tuiles : on garde le marqueur « aire »
                pois[key][5] = max(pois[key][5], rec[5])
            else:
                pois[key] = rec
    return list(pois.values())


def flat_coords(g):
    """Liste à plat des points [lon, lat] d'une géométrie GeoJSON."""
    out = []
    def walk(c):
        if c and isinstance(c[0], (int, float)):
            out.append(c)
        else:
            for x in c or []:
                walk(x)
    if g:
        walk(g.get("coordinates"))
    return out


def process_geojson(path, all_fastfood):
    """Version « fichier » : lit l'export osmium (geojsonseq) de la France entière filtrée sur les restaurants."""
    aires, cand = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                f = json.loads(line)
            except ValueError:
                continue
            tags = f.get("properties") or {}
            pts = flat_coords(f.get("geometry"))
            if not pts:
                continue
            lons = [p[0] for p in pts]
            lats = [p[1] for p in pts]
            box = (min(lats), min(lons), max(lats), max(lons))
            if tags.get("highway") in ("services", "rest_area"):
                aires.append(box)
            elif tags.get("amenity") in TYPES:
                cand.append((f.get("id"), tags, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2))
    # Index des aires : emprise élargie d'environ 120 m, rangée par cases de 0,02 degré
    CELL = 0.02
    grid = {}
    for (s, w, n, e) in aires:
        s, n, w, e = s - 0.0011, n + 0.0011, w - 0.0016, e + 0.0016
        for cx in range(int(w // CELL), int(e // CELL) + 1):
            for cy in range(int(s // CELL), int(n // CELL) + 1):
                grid.setdefault((cx, cy), []).append((s, w, n, e))

    def in_aire(lat, lon):
        for (s, w, n, e) in grid.get((int(lon // CELL), int(lat // CELL)), []):
            if s <= lat <= n and w <= lon <= e:
                return True
        return False

    pois = {}
    for fid, tags, lat, lon in cand:
        name = tags.get("name") or tags.get("brand")
        if not name:
            continue
        b = detect_brand(tags)
        aire = in_aire(lat, lon)
        if b < 0 and not aire and not (all_fastfood and tags.get("amenity") == "fast_food"):
            continue
        addr = ", ".join(x for x in [
            " ".join(y for y in [tags.get("addr:housenumber"), tags.get("addr:street")] if y),
            tags.get("addr:city"),
        ] if x)
        key = fid or (round(lat, 5), round(lon, 5), name)
        pois[key] = [round(lat, 5), round(lon, 5), name, b, TYPES.index(tags["amenity"]),
                     1 if aire else 0, tags.get("opening_hours", ""), addr]
    print(f"{len(aires)} aires et {len(cand)} restaurants lus, {len(pois)} lieux retenus", flush=True)
    return list(pois.values())


def write_db(p, complete=True):
    p.sort(key=lambda r: (r[0], r[1]))
    data = {
        "v": 1,
        "date": datetime.date.today().strftime("%d/%m/%Y"),
        "complete": complete,
        "brands": [b for b, _ in BRANDS],
        "p": p,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Base écrite : {len(p)} lieux ({os.path.getsize(OUT) / 1024:.0f} Ko) dans pois.json", flush=True)


def build(all_fastfood):
    results, failed = [], 0
    all_tiles = list(tiles())
    start = time.time()
    for i, bb in enumerate(all_tiles, 1):
        label = f"zone {i}/{len(all_tiles)}"
        if time.time() - start > 70 * 60:  # garde-fou : on s'arrête avant la limite de GitHub (90 min)
            failed += 1
            results.append(None)
            print(f"  {label} : ignorée (temps écoulé)", flush=True)
            continue
        print(f"Téléchargement {label}…", flush=True)
        res = fetch_tile(build_query(bb, all_fastfood), label)
        if res is None:
            failed += 1
            print(f"  {label} : échec définitif", flush=True)
        else:
            print(f"  {label} : {len(res.get('elements', []))} éléments ({int(time.time() - start)} s)", flush=True)
        results.append(res)
        time.sleep(2)
    p = process(results, all_fastfood)
    p.sort(key=lambda r: (r[0], r[1]))
    data = {
        "v": 1,
        "date": datetime.date.today().strftime("%d/%m/%Y"),
        "complete": failed == 0,
        "brands": [b for b, _ in BRANDS],
        "p": p,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUT) / 1024
    print(f"\nBase écrite : {len(p)} lieux ({size:.0f} Ko) dans pois.json")
    if failed:
        print(f"ATTENTION : {failed} zone(s) sans réponse. Relance avec --refresh pour compléter.")
    return failed == 0


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def serve():
    import http.server, socketserver, webbrowser, functools
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=HERE)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    for port in range(8000, 8011):
        try:
            httpd = socketserver.ThreadingTCPServer(("0.0.0.0", port), handler)
            break
        except OSError:
            continue
    else:
        print("Aucun port libre entre 8000 et 8010.")
        return
    ip = lan_ip()
    print(f"\nApp lancée : http://localhost:{port}")
    if ip:
        print(f"Sur ton téléphone (même wifi) : http://{ip}:{port}")
    print("Ctrl+C pour arrêter.\n")
    try:
        webbrowser.open(f"http://localhost:{port}")
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêté.")


def main():
    args = set(sys.argv[1:])
    all_ff = "--all" in args
    if "--from-geojson" in sys.argv:
        path = sys.argv[sys.argv.index("--from-geojson") + 1]
        write_db(process_geojson(path, all_ff))
        return
    need = "--refresh" in args or not os.path.exists(OUT)
    if not need:
        try:
            need = not json.load(open(OUT, encoding="utf-8")).get("complete", False)
        except Exception:
            need = True
    if need:
        build(all_ff)
    else:
        print("pois.json existe déjà (utilise --refresh pour le reconstruire).")
    if "--no-serve" not in args:
        serve()


if __name__ == "__main__":
    main()
