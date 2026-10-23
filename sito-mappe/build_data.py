#!/usr/bin/env python3
"""Genera il sito della Rete UdR 7 dal GTFS.

Uso:
    python3 sito-mappe/build_data.py [percorso_gtfs.zip]

Senza argomento usa lo zip GTFS nella radice del repo; se ce n'è più di uno,
quello aggiunto/modificato per ultimo (secondo git, altrimenti per data file).

Riempie sito-mappe/template.html con i dati e scrive due copie identiche del sito,
un unico file HTML autonomo: sito-mappe/index.html (pubblicato online) e index.html
nella radice del repo (da aprire con un doppio clic).
"""
import base64
import csv
import datetime
import glob
import io
import json
import math
import os
import re
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
TITOLO = "Rete bus UdR 7"

# calendari imposti (al posto di calendar.txt; le date di calendar_dates.txt sono ignorate)
# service_id -> etichetta, giorni della settimana (0 = lunedì), validità; la domenica non ci sono corse
CALENDARI = {
    "udr7_10": ("Scolastico feriale", {0, 1, 2, 3, 4}, "20260921", "20270608"),
    "udr7_20": ("Scolastico sabato", {5}, "20260921", "20270608"),
}

# raggruppamento delle linee per comune (dal prefisso del codice linea); le linee con codice
# numerico (03_1, 29_1, 66_3, …) sono intercomunali
COMUNI = {
    "AMA": "Amatrice", "CAN": "Cantalice", "CITT": "Cittaducale", "CON": "Contigliano",
    "CSA": "Castel Sant'Angelo", "LEO": "Leonessa",
}

# località scritte in testa al nome fermata ("CASTELNUOVO F. / Via Roma") con la grafia estesa;
# le altre diventano "Iniziali Maiuscole" (CITTADUCALE -> Cittaducale)
LOCALITA = {
    "CASTELNUOVO F.": "Castelnuovo di Farfa", "CARSOLI (AQ)": "Carsoli", "ACCUMULI": "Accumoli",
    "TORRI": "Torri in Sabina", "COLLALTO": "Collalto Sabino", "FRASSO": "Frasso Sabino",
    "PETRELLA": "Petrella Salto", "POZZAGLIA": "Pozzaglia Sabina", "POSTA LAZIO": "Posta",
}
# comuni (anche scritti da soli come nome fermata: "Leonessa", "Turania", "MONTEBUONO centro")
NOMI_COMUNI = set(LOCALITA.values()) | {
    "Amatrice", "Antrodoco", "Borbona", "Cantalice", "Castel Sant'Angelo", "Cittaducale", "Cittareale",
    "Collegiove", "Configni", "Contigliano", "Cottanello", "Forano", "Greccio", "Leonessa", "Micigliano",
    "Montasola", "Montebuono", "Nespolo", "Poggio Moiano", "Poggio Nativo", "Pozzaglia Sabina", "Rieti",
    "Selci", "Stimigliano", "Tarano", "Turania",
}
# fermate senza comune nel nome (nome -> comune); le altre prendono quello della fermata
# con comune più vicina, se entro NEAR_MAX_KM
COMUNE_FERMATA = {
    "Gavignano (St.ne FS)": ("Stazione FS Gavignano", "Forano"),
    "Stimigliano (St.ne FS)": ("Stazione FS", "Stimigliano"),
    "S.tne FS Carsoli": ("Stazione FS", "Carsoli"),
    "Rieti FS": ("Stazione FS", "Rieti"),
    "Configni (Lugnola)": ("Lugnola", "Configni"),
    "Posta Lazio": ("Posta", "Posta"),
}
# refusi nei nomi fermata
REFUSI = {"Scentifico": "Scientifico", "Metteotti": "Matteotti", "Colllalto": "Collalto", "fRATI": "Frati",
          "STazione": "Stazione", "C.O.C": "C.O.C."}
NEAR_MAX_KM = 3.0

PRINC_OGNI_MIN = 5      # almeno una fermata principale ogni 5 minuti di viaggio
PRINC_NODO_LINEE = 4    # nodo di interscambio: fermata servita da almeno 4 altre linee

GIORNI = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]


def find_gtfs():
    zips = glob.glob(os.path.join(ROOT, "*.zip"))
    if not zips:
        raise SystemExit("Nessun file .zip GTFS trovato nella radice del repo.")

    def committed(path):
        try:
            out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", os.path.basename(path)],
                                 cwd=ROOT, capture_output=True, text=True).stdout.strip()
            return int(out) if out else float("inf")  # non ancora in git: è il più recente
        except OSError:
            return 0

    def calendar_end(path):
        # a parità di commit (zip caricati insieme) vince il GTFS con il calendario che finisce più tardi
        try:
            zf = zipfile.ZipFile(path)
            ends = [c["end_date"] for c in read(zf, "calendar.txt")] + [c["date"] for c in read(zf, "calendar_dates.txt")]
            return max(ends) if ends else ""
        except (zipfile.BadZipFile, KeyError):
            return ""

    return max(zips, key=lambda z: (committed(z), calendar_end(z), os.path.getmtime(z)))


def read(zf, name):
    if name not in zf.namelist():
        return []
    with zf.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))


def natural(code):
    """'LA10' -> ('LA', 10): ordina LA5, LA6, … LA10 invece di LA10, LA5."""
    m = re.match(r"(\D*)(\d*)(.*)", code)
    return (m.group(1), int(m.group(2)) if m.group(2) else 10**6, m.group(3))


def verso(short, trip_name):
    """'A' o 'R' dal codice corsa ('CV1 A', 'Angn 2-B-R', 'LA9A', 'LA21-varA', 'LA22 VAR-R'), altrimenti ''."""
    key = r"\s*".join(map(re.escape, re.sub(r"\s+", "", short).lower()))
    name = re.sub(r"\s+", " ", trip_name).strip().lower()
    rest = re.sub("^" + key, "", name)  # "zgrl4 scol r" -> " scol r", "angn 2-b-r" -> "-b-r"
    tokens = [re.sub(r"^var", "", x) for x in re.split(r"[\s\-]+", rest) if x]
    tokens = [x for x in tokens if x in ("a", "r")]
    return tokens[-1].upper() if tokens else ""


def directions(rtrips, short, st_by_trip, stops):
    """Direzione di ogni corsa. Il GTFS mette quasi tutto in direction_id 0, anche andata e ritorno:
    se la linea ha corse marcate A e R nel codice si usa quello; le corse senza marca vanno con la
    direzione il cui capolinea d'arrivo è più vicino al loro."""
    marks = {t["trip_id"]: verso(short, t.get("trip_short_name") or "") for t in rtrips}
    if not {"A", "R"} <= set(marks.values()):
        return {t["trip_id"]: t["direction_id"] or "0" for t in rtrips}

    def end(t):
        s = stops[st_by_trip[t["trip_id"]][-1]["stop_id"]]
        return (float(s["stop_lat"]), float(s["stop_lon"]))
    ends = {m: Counter(end(t) for t in rtrips if marks[t["trip_id"]] == m).most_common(1)[0][0] for m in "AR"}
    out = {}
    for t in rtrips:
        m = marks[t["trip_id"]] or min("AR", key=lambda x: km_between(end(t), ends[x]))
        out[t["trip_id"]] = "0" if m == "A" else "1"
    return out


def area_of(short):
    if short[:1].isdigit():
        return "Linee intercomunali"
    for k in sorted(COMUNI, key=len, reverse=True):
        if short.startswith(k):
            return COMUNI[k]
    return ""


def km_between(a, b):
    kx = 111.32 * math.cos(math.radians(a[0]))
    return math.hypot((b[1] - a[1]) * kx, (b[0] - a[0]) * 110.54)


def stop_names(stops, used):
    """fermata -> (nome, comune); senza comune nel nome si prende quello della fermata più vicina che lo ha."""
    out = {sid: split_stop_name(stops[sid]["stop_name"]) for sid in used}
    pos = {sid: (float(stops[sid]["stop_lat"]), float(stops[sid]["stop_lon"])) for sid in used}
    known = [sid for sid in used if out[sid][1]]
    for sid in used:
        nome, com = out[sid]
        if com or not known:
            continue
        near = min(known, key=lambda k: km_between(pos[sid], pos[k]))
        if km_between(pos[sid], pos[near]) <= NEAR_MAX_KM:
            out[sid] = (nome, out[near][1])
    return out


def smart_title(text):
    """'ROCCA DI CORNO - ANTRODOCO' -> 'Rocca di Corno - Antrodoco' (sigle come FS restano maiuscole)."""
    small = {"di", "del", "della", "dei", "degli", "delle", "e", "a", "in", "da", "la"}
    words = text.lower().title().split(" ")
    out = []
    for i, w in enumerate(words):
        if i and w.lower() in small:
            w = w.lower()
        elif w.upper() in ("FS", "SS", "IC", "SP", "SR"):
            w = w.upper()
        out.append(w)
    return " ".join(out)


def split_stop_name(name):
    """'CITTAREALE / Località Ricci' -> ('Località Ricci', 'Cittareale'); '(Contigliano) Montisola II' -> ('Montisola II', 'Contigliano')."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    for k, v in REFUSI.items():
        name = name.replace(k, v)
    if name in COMUNE_FERMATA:
        return COMUNE_FERMATA[name]
    m = re.match(r"^([^/|]+?)\s*[/|]\s*(.+)$", name)
    if m:
        loc = m.group(1).strip()
        loc = LOCALITA.get(loc.upper(), smart_title(loc))
        rest = m.group(2).strip()
        if rest.isupper() and len(rest) > 3:  # "SANTA RUFINA", "STAZIONE FS"
            rest = smart_title(rest)
        return (rest[:1].upper() + rest[1:], loc)
    m = re.match(r"^\(([^()]+)\)\s*(.+)$", name)  # "(Contigliano) Montisola II"
    if m:
        return (m.group(2), smart_title(m.group(1)))
    t = smart_title(name)
    if t in NOMI_COMUNI:
        return (t, t)
    m = re.match(r"^(.+?) (centro|p\.le .+)$", name, re.I)  # "MONTEBUONO centro", "CONTIGLIANO P.LE DEGLI EROI"
    if m and smart_title(m.group(1)) in NOMI_COMUNI:
        rest = smart_title(m.group(2)).replace("P.Le", "P.le")
        return (rest, smart_title(m.group(1)))
    return (name[:1].upper() + name[1:], "")


def end_label(nome, com):
    """Nome breve di un capolinea: ('Stazione FS', 'Stimigliano') -> 'Stimigliano FS', ('Centro', 'Configni') -> 'Configni'."""
    if nome in (com, "Centro") and com:
        return com
    if nome == "Stazione FS" and com:
        return f"{com} FS"
    if nome.startswith("Stazione FS "):
        return nome[len("Stazione FS "):] + " FS"
    if com and re.match(r"(Via|Viale|Piazza|P\.le|Largo|Ospedale|Deposito|Scuola|Cimitero|Ist\.)\b", nome):
        return f"{com} {nome}"  # 'Contigliano P.le degli Eroi'; le frazioni ('Torrita', 'Domo') restano sole
    return nome


def route_name(long_name, short):
    """'Linea 3_1 (ROCCA DI CORNO - ANTRODOCO)' -> 'Rocca di Corno - Antrodoco';
    'Linee A: Amatrice - Patarico - Domo' / 'LINEA B Cantalice - S. Liberato' -> senza il codice in testa."""
    name = re.sub(r"\s+", " ", (long_name or "").strip())
    m = re.match(r"^linea \S+ \((.+)\)$", name, re.I)
    if m:
        name = m.group(1)
    else:
        name = re.sub(r"^line[ae] ([A-Z]{1,2}/?:?\s+)?", "", name, flags=re.I)
    name = re.sub(r"\s*-\s*", " - ", name)
    if name.isupper():
        name = smart_title(name)
    return name or short


def minutes(t):
    # orari al minuto (hh:mm): i secondi si troncano, 06:54:30 -> 06:54
    h, m, _ = (int(x) for x in t.split(":"))
    return h * 60 + m


def encode_polyline(points):
    out, plat, plon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def simplify(points, tol_m=5.0):
    """Douglas-Peucker in metri."""
    if len(points) < 3:
        return points
    kx = 111320 * math.cos(math.radians(points[0][0]))
    xy = [(p[1] * kx, p[0] * 110540) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            t = 0 if L2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L2))
            d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > best:
                best, idx = d, i
        if best > tol_m:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def line_colors(n):
    # colori distinti, abbastanza scuri da reggere il testo bianco del riquadro linea
    def hsl(h, s, l):
        s, l = s / 100, l / 100
        c = (1 - abs(2 * l - 1)) * s
        x = c * (1 - abs((h / 60) % 2 - 1))
        m = l - c / 2
        r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
        return "#%02X%02X%02X" % tuple(round((v + m) * 255) for v in (r, g, b))
    return [hsl((i * 137.508) % 360, 70, 40) for i in range(n)]


def service_label(weekdays):
    """Insieme di giorni della settimana -> 'Lunedì–Venerdì', 'Sabato', 'Domenica e festivi', …"""
    ds = sorted(weekdays)
    if not ds:
        return "Nessun giorno"
    if ds == [6]:
        return "Domenica e festivi"
    runs, start = [], ds[0]
    for a, b in zip(ds, ds[1:] + [None]):
        if b != a + 1:
            runs.append((start, a))
            start = b
    parts = [GIORNI[a] if a == b else (f"{GIORNI[a]} e {GIORNI[b]}" if b == a + 1 else f"{GIORNI[a]}–{GIORNI[b]}")
             for a, b in runs]
    return ", ".join(parts)


def ymd(d):
    return d.strftime("%Y%m%d")


def build(zpath):
    zf = zipfile.ZipFile(zpath)
    agency = (read(zf, "agency.txt") or [{}])[0]
    routes = read(zf, "routes.txt")
    stops = {s["stop_id"]: s for s in read(zf, "stops.txt")}
    trips = read(zf, "trips.txt")

    st_by_trip = defaultdict(list)
    for r in read(zf, "stop_times.txt"):
        st_by_trip[r["trip_id"]].append(r)
    for v in st_by_trip.values():
        v.sort(key=lambda r: int(r["stop_sequence"]))
    trips = [t for t in trips if st_by_trip.get(t["trip_id"])]

    # ---- calendari: imposti da CALENDARI, senza eccezioni
    used = {t["service_id"] for t in trips}
    servizi = {}
    for svc, (label, wd, dal, al) in CALENDARI.items():
        if svc not in used:
            continue
        d = datetime.datetime.strptime(dal, "%Y%m%d").date()
        end = datetime.datetime.strptime(al, "%Y%m%d").date()
        ds = []
        while d <= end:
            if d.weekday() in wd:
                ds.append(ymd(d))
            d += datetime.timedelta(days=1)
        servizi[svc] = {"label": label, "dal": ds[0], "al": ds[-1], "esclusi": [],
                        "date": ds, "giorni": len(ds), "scol": True}
    used_svc = list(servizi)
    labels = Counter(v["label"] for v in servizi.values())
    for sv, v in servizi.items():  # due calendari con la stessa etichetta: si distinguono col codice
        if labels[v["label"]] > 1:
            v["label"] += f" ({sv})"
    trips = [t for t in trips if t["service_id"] in servizi]

    # ---- fermate usate
    names = stop_names(stops, {r["stop_id"] for t in trips for r in st_by_trip[t["trip_id"]]})
    used_stops = sorted(names, key=lambda sid: (names[sid][1], names[sid][0], sid))
    stop_idx = {sid: i for i, sid in enumerate(used_stops)}
    fermate = []
    for sid in used_stops:
        s = stops[sid]
        nome, loc = names[sid]
        fermate.append([s.get("stop_code") or sid, nome, loc, round(float(s["stop_lat"]), 6), round(float(s["stop_lon"]), 6)])

    # ---- percorsi
    pts = defaultdict(list)
    for r in read(zf, "shapes.txt"):
        pts[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"]),
                                   float(r.get("shape_dist_traveled") or 0)))
    shapes, shape_km = {}, {}
    for sid in sorted({t["shape_id"] for t in trips}):
        seq = sorted(pts.get(sid, []))
        line = [(p[1], p[2]) for p in seq]
        km = seq[-1][3] / 1000 if seq and seq[-1][3] else 0
        if not km and len(line) > 1:  # senza shape_dist_traveled: lunghezza geometrica
            kx = 111.32 * math.cos(math.radians(line[0][0]))
            km = sum(math.hypot((b[1] - a[1]) * kx, (b[0] - a[0]) * 110.54) for a, b in zip(line, line[1:]))
        shapes[sid] = encode_polyline(simplify(line))
        shape_km[sid] = round(km, 1)

    # ---- linee
    route_lines = defaultdict(set)  # fermata -> linee che la servono (per le fermate di interscambio)
    for t in trips:
        for r in st_by_trip[t["trip_id"]]:
            route_lines[r["stop_id"]].add(t["route_id"])

    # comune principale di ogni linea (quello con più passaggi) e comuni serviti
    passaggi = defaultdict(Counter)
    for t in trips:
        for r in st_by_trip[t["trip_id"]]:
            com = names[r["stop_id"]][1]
            if com:
                passaggi[t["route_id"]][com] += 1
    routes = sorted(routes, key=lambda r: natural(r["route_short_name"]))
    colors = line_colors(len(routes))
    linee = []
    for ri, r in enumerate(routes):
        rtrips = [t for t in trips if t["route_id"] == r["route_id"]]
        if not rtrips:
            continue
        tabs = []
        groups = defaultdict(list)
        dirs = directions(rtrips, r["route_short_name"], st_by_trip, stops)
        for t in rtrips:
            groups[(t["service_id"], dirs[t["trip_id"]])].append(t)
        for (svc, d), ts in sorted(groups.items(), key=lambda kv: (used_svc.index(kv[0][0]), kv[0][1])):
            tabs.append(make_tab(svc, d, ts, st_by_trip, names, stop_idx, route_lines))
        if len({t["titolo"] for t in tabs}) < len({(t["titolo"], t["dir"]) for t in tabs}):
            # due direzioni con lo stesso titolo (giri nei due sensi): si distinguono
            for t in tabs:
                t["titolo"] += " (andata)" if t["dir"] == "0" else " (ritorno)"
        color = (r.get("route_color") or "").strip()
        shp = sorted({t["shape_id"] for t in rtrips})
        linee.append({
            "id": r["route_short_name"], "n": r["route_short_name"], "nome": route_name(r["route_long_name"], r["route_short_name"]),
            "area": area_of(r["route_short_name"]), "comuni": sorted(passaggi[r["route_id"]]), "col": "#" + color.upper() if color else colors[ri],
            "shapes": shp, "km": max(shape_km[s] for s in shp), "tab": tabs,
        })

    linee.sort(key=lambda l: (l["area"] == "Linee intercomunali", l["area"], natural(l["n"])))
    all_dates = [d for v in servizi.values() for d in v["date"]]
    return {
        "agenzia": {"nome": agency.get("agency_name", ""), "url": agency.get("agency_url", ""),
                    "tel": agency.get("agency_phone", "")},
        # nel sito pubblicato non compaiono il nome del file GTFS né dettagli tecnici
        "feed": {"dal": min(all_dates), "al": max(all_dates)},
        "servizi": servizi, "fermate": fermate, "shapes": shapes, "shape_km": shape_km, "linee": linee,
    }


def make_tab(svc, d, ts, st_by_trip, names, stop_idx, route_lines):
    """Quadro orario di una direzione: righe = unione ordinata delle fermate di tutte le corse."""
    # chiave (fermata, n-esima occorrenza): le circolari passano due volte dalla stessa fermata
    def keyed(t):
        seen = Counter()
        out = []
        for r in st_by_trip[t["trip_id"]]:
            out.append((r["stop_id"], seen[r["stop_id"]]))
            seen[r["stop_id"]] += 1
        return out
    ts = sorted(ts, key=lambda t: minutes(st_by_trip[t["trip_id"]][0]["departure_time"]))
    ks = [keyed(t) for t in ts]
    main = Counter(tuple(k) for k in ks).most_common()
    main = max(main, key=lambda kv: (kv[1], len(kv[0])))[0]
    rows = list(main)
    extra = Counter()
    trip_rows = []  # per ogni corsa, la riga del quadro di ciascuna sua fermata
    for k in ks:
        prev, mine = -1, []
        for key in k:
            # la stessa fermata più avanti nel quadro; se la corsa ci passa in un ordine diverso
            # (varianti) si aggiunge una riga, così gli orari della corsa restano in ordine
            at = next((i for i in range(prev + 1, len(rows)) if rows[i][0] == key[0] and
                       (rows[i] == key or rows[i] not in k)), None)
            if at is None:
                extra[key[0]] += 1
                row = key if key not in rows else (key[0], f"x{extra[key[0]]}")
                rows.insert(prev + 1, row)
                at = prev + 1
            mine.append(rows[at])
            prev = at
        trip_rows.append(mine)
    pos = {key: i for i, key in enumerate(rows)}

    corse = []
    for t, k in zip(ts, trip_rows):
        o = [None] * len(rows)
        for key, r in zip(k, st_by_trip[t["trip_id"]]):
            o[pos[key]] = minutes(r["departure_time"] or r["arrival_time"])
        corse.append({"id": t.get("trip_short_name") or t["trip_id"], "shape": t["shape_id"], "o": o})

    # fermate principali (il GTFS non le indica: timepoint è sempre 1)
    princ = [0] * len(rows)
    for c in corse:  # capolinea di ogni corsa
        served = [i for i, v in enumerate(c["o"]) if v is not None]
        princ[served[0]] = princ[served[-1]] = 1
    route_id = ts[0]["route_id"]
    last_com = None
    for i, (sid, _) in enumerate(rows):
        com = names[sid][1]
        if com:
            if last_com and com != last_com:  # ingresso in un altro comune
                princ[i] = 1
            last_com = com
        if len(route_lines[sid] - {route_id}) >= PRINC_NODO_LINEE:
            princ[i] = 1
    longest = max(corse, key=lambda c: sum(v is not None for v in c["o"]))
    last_t = None
    for i, v in enumerate(longest["o"]):
        if v is None:
            continue
        if princ[i] or last_t is None or v - last_t >= PRINC_OGNI_MIN:
            princ[i] = 1
            last_t = v

    # titolo dai capolinea d'arrivo delle corse: i trip_headsign del GTFS sono spesso sbagliati
    # (CAN B "CITTADUCALE" per una circolare di Cantalice, 66_1 "GAVIGNANO FS" per corse che finiscono a Stimigliano)
    ends = Counter(end_label(*names[st_by_trip[t["trip_id"]][-1]["stop_id"]]) for t in ts)
    titolo = " / ".join(e for e, _ in ends.most_common(3))
    return {"svc": svc, "dir": d, "titolo": titolo, "fermate": [stop_idx[sid] for sid, _ in rows],
            "princ": princ, "corse": corse}


def main():
    zpath = sys.argv[1] if len(sys.argv) > 1 else find_gtfs()
    print(f"GTFS: {os.path.basename(zpath)}")
    data = build(zpath)
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(os.path.join(HERE, "logo.png"), "rb") as f:  # logo incluso nella pagina: online va solo index.html
        logo = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    for old, new in (("/*DATI*/null", blob), ("/*TITOLO*/", TITOLO), ("/*LOGO*/", logo)):
        if old not in html:
            raise SystemExit(f"Segnaposto {old} non trovato in template.html")
        html = html.replace(old, new)
    for out in (os.path.join(HERE, "index.html"), os.path.join(ROOT, "index.html")):
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
    n = sum(len(t["corse"]) for l in data["linee"] for t in l["tab"])
    print(f"index.html: {len(html.encode()) / 1024:.0f} KB — {len(data['linee'])} linee, {len(data['fermate'])} fermate, "
          f"{n} corse, dal {data['feed']['dal']} al {data['feed']['al']}")


if __name__ == "__main__":
    main()
