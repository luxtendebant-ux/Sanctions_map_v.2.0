#!/usr/bin/env python3
"""Комплаенс-карта 2.0 — индекс официальных санкционных списков для раздела «Рабочее место СК».

Без ИИ и без сторонних библиотек. Скачивает полные официальные списки регуляторов, собирает из них
компактный индекс screen-index.json (имена, псевдонимы, дата рождения, идентификаторы, программа, дата включения)
и сводку изменений screen-delta.json (новые и исключённые записи по сравнению с прежним индексом).

Запуск: python3 -I build_screen_index.py <папка_с_прежним_индексом_и_выхода> [--offline=<папка с файлами>]
Последняя строка вывода — JSON-итог; при отказе всех источников — «ОШИБКА: …» и код 1.
Если источник не скачался или не разобрался, в индекс переносятся его записи из прежнего индекса
(status = "stale", с датой), чтобы скрининг не потерял охват; это видно на странице.

Источники (первичные, уровень A):
  OFAC SDN и Consolidated (non-SDN) — https://ofac.treasury.gov/specially-designated-nationals-list-data-formats-data-schemas
  ЕС, консолидированный список (FSF) — https://webgate.ec.europa.eu/fsd/fsf
  Великобритания, UK Sanctions List (FCDO) — https://www.gov.uk/government/publications/the-uk-sanctions-list
  Канада, SEMA (Global Affairs Canada) — https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolide.aspx
  Швейцария, SECO SESAM — https://www.sesam.search.admin.ch/
"""
import csv, io, json, os, re, sys, time, hashlib, datetime, urllib.request
import xml.etree.ElementTree as ET

csv.field_size_limit(10**8)
UA = {"User-Agent": "cm2-screen-index/1.0 (+https://luxtendebant-ux.github.io/Sanctions_map_v.2.0/)"}
SOURCES = {
    "OFAC": {"name": "США — OFAC SDN", "page": "https://sanctionssearch.ofac.treas.gov/",
             "files": {"sdn": "https://www.treasury.gov/ofac/downloads/sdn.csv",
                       "alt": "https://www.treasury.gov/ofac/downloads/alt.csv",
                       "add": "https://www.treasury.gov/ofac/downloads/add.csv"}},
    "OFACNS": {"name": "США — OFAC Consolidated (non-SDN: SSI, NS-MBS, CAPTA и др.)", "page": "https://sanctionssearch.ofac.treas.gov/",
               "files": {"prim": "https://www.treasury.gov/ofac/downloads/consolidated/cons_prim.csv",
                         "alt": "https://www.treasury.gov/ofac/downloads/consolidated/cons_alt.csv"}},
    "EU": {"name": "ЕС — консолидированный список финансовых санкций", "page": "https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions",
           "files": {"csv": "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"}},
    "UK": {"name": "Великобритания — UK Sanctions List (FCDO)", "page": "https://search-uk-sanctions-list.service.gov.uk/",
           "files": {"csv": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv"}},
    "CA": {"name": "Канада — SEMA (консолидированный список)", "page": "https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolide.aspx",
           "files": {"xml": "https://www.international.gc.ca/world-monde/assets/office_docs/international_relations-relations_internationales/sanctions/sema-lmes.xml"}},
    "CH": {"name": "Швейцария — SECO (SESAM)", "page": "https://www.sesam.search.admin.ch/sesam-search-web/pages/search.xhtml?lang=en",
           "files": {"xml": "https://www.sesam.search.admin.ch/sesam-search-web/pages/downloadXmlGesamtliste.xhtml?lang=en&action=downloadXmlGesamtlisteAction"}},
}
MIN_COUNT = {"OFAC": 8000, "OFACNS": 100, "EU": 3000, "UK": 3000, "CA": 2000, "CH": 3000}
NONE = {"", "-0-", "-0- "}
DIG = re.compile(r"(?<![\d./-])\d{7,15}(?![\d./-])")
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def clean(s):
    s = "" if s is None else str(s)
    return "" if s.strip() in NONE else re.sub(r"\s+", " ", s).strip()


def fetch(url, tries=3):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                return r.read()
        except Exception as e:
            err = e
            time.sleep(10 * (i + 1))
    raise RuntimeError("не скачано: %s (%s)" % (url.split("?")[0], err))


def ids_from(*texts):
    out = []
    for t in texts:
        for m in DIG.findall(t or ""):
            if m not in out:
                out.append(m)
    return out[:12]


def ofac_dob(rem):
    out = []
    for m in re.finditer(r"DOB ([^;.]+)", rem or ""):
        v = m.group(1).strip()
        d = re.match(r"(\d{1,2}) (\w{3}) (\d{4})$", v)
        out.append("%04d-%02d-%02d" % (int(d.group(3)), MON.get(d.group(2).lower(), 0), int(d.group(1))) if d and MON.get(d.group(2).lower()) else v)
    return ", ".join(out[:3])


def entry(src, rid, typ, name, aliases, dob="", prog="", listed="", ids=None, country=""):
    al = []
    for a in aliases:
        a = clean(a)
        if a and a.lower() != name.lower() and a not in al:
            al.append(a)
    return {"s": src, "i": str(rid), "t": typ, "n": name, "a": al[:40], "b": dob, "p": prog[:160],
            "l": listed, "x": ids or [], "c": country[:80]}


# ---------- разбор ----------
def parse_ofac(files, src):
    prim = files.get("sdn") or files.get("prim")
    alts, addr = {}, {}
    for row in csv.reader(io.StringIO(files["alt"].decode("utf-8", "replace"))):
        if len(row) >= 4:
            alts.setdefault(row[0], []).append(row[3])
    if "add" in files:
        for row in csv.reader(io.StringIO(files["add"].decode("utf-8", "replace"))):
            if len(row) >= 5 and clean(row[4]):
                addr.setdefault(row[0], [])
                if clean(row[4]) not in addr[row[0]]:
                    addr[row[0]].append(clean(row[4]))
    out = []
    for row in csv.reader(io.StringIO(prim.decode("utf-8", "replace"))):
        if len(row) < 12 or not row[0].strip().isdigit():
            continue
        t = clean(row[2]).lower()
        typ = "i" if t == "individual" else "v" if t == "vessel" else "a" if t == "aircraft" else "e"
        rem = clean(row[11])
        ids = ids_from(rem)
        imo = re.findall(r"IMO (\d{7})", rem)
        for m in imo:
            if m not in ids:
                ids.insert(0, m)
        out.append(entry(src, row[0], typ, clean(row[1]), alts.get(row[0], []), ofac_dob(rem),
                         clean(row[3]), "", ids, ", ".join(addr.get(row[0], [])[:3])))
    return out, ""


def parse_eu(files):
    text = files["csv"].decode("utf-8-sig", "replace")
    rd = csv.reader(io.StringIO(text), delimiter=";")
    head = next(rd)
    H = {h: i for i, h in enumerate(head)}
    g = lambda r, k: clean(r[H[k]]) if k in H and H[k] < len(r) else ""
    ents, gen = {}, ""
    for r in rd:
        if not r:
            continue
        gen = gen or g(r, "fileGenerationDate")
        lid = g(r, "Entity_LogicalId")
        if not lid:
            continue
        e = ents.setdefault(lid, {"names": [], "b": [], "x": [], "p": "", "l": "", "t": "e", "ref": "", "c": []})
        st = g(r, "Entity_SubjectType_ClassificationCode") or g(r, "Entity_SubjectType")
        e["t"] = "i" if st in ("person", "P") else "e"
        e["ref"] = g(r, "Entity_EU_ReferenceNumber") or e["ref"]
        e["p"] = e["p"] or g(r, "Entity_Regulation_Programme")
        dd = g(r, "Entity_DesignationDate") or g(r, "Entity_Regulation_PublicationDate")
        if dd and (not e["l"] or dd < e["l"]):
            e["l"] = dd
        n = g(r, "NameAlias_WholeName")
        if n and n not in e["names"]:
            e["names"].append(n)
        b = g(r, "BirthDate_BirthDate") or g(r, "BirthDate_Year")
        if b and b not in e["b"]:
            e["b"].append(b)
        for k in ("Identification_Number", "Identification_LatinNumber"):
            v = re.sub(r"\s", "", g(r, k))
            if v and v not in e["x"]:
                e["x"].append(v)
        c = g(r, "Address_CountryDescription")
        if c and c not in e["c"]:
            e["c"].append(c)
    out = [entry("EU", e["ref"] or lid, e["t"], e["names"][0], e["names"][1:], ", ".join(e["b"][:3]), e["p"], e["l"],
                 e["x"][:12], ", ".join(e["c"][:3])) for lid, e in ents.items() if e["names"]]
    d = re.match(r"(\d{2})/(\d{2})/(\d{4})", gen or "")
    return out, ("%s-%s-%s" % (d.group(3), d.group(2), d.group(1)) if d else "")


def parse_uk(files):
    text = files["csv"].decode("utf-8-sig", "replace")
    lines = text.split("\n", 1)
    rep = ""
    m = re.search(r"Report Date:\s*(\d{1,2})-(\w{3})-(\d{4})", lines[0])
    if m:
        rep = "%s-%02d-%02d" % (m.group(3), MON.get(m.group(2).lower(), 0), int(m.group(1)))
        text = lines[1]
    rd = csv.reader(io.StringIO(text))
    head = next(rd)
    H = {h.strip(): i for i, h in enumerate(head)}
    g = lambda r, k: clean(r[H[k]]) if k in H and H[k] < len(r) else ""
    ents = {}
    for r in rd:
        uid = g(r, "Unique ID")
        if not uid:
            continue
        e = ents.setdefault(uid, {"pn": "", "names": [], "b": [], "x": [], "p": "", "l": "", "t": "e", "c": []})
        dt = g(r, "Designation Type").lower()
        e["t"] = "i" if dt.startswith("indiv") else "v" if dt.startswith("ship") else "e"
        e["p"] = e["p"] or g(r, "Regime Name")
        dd = g(r, "Date Designated")
        m2 = re.match(r"(\d{2})/(\d{2})/(\d{4})", dd)
        if m2:
            iso = "%s-%s-%s" % (m2.group(3), m2.group(2), m2.group(1))
            if not e["l"] or iso < e["l"]:
                e["l"] = iso
        nm = " ".join(x for x in [g(r, "Name 1"), g(r, "Name 2"), g(r, "Name 3"), g(r, "Name 4"), g(r, "Name 5"), g(r, "Name 6")] if x)
        if nm:
            if g(r, "Name type").lower().startswith("primary") and not e["pn"]:
                e["pn"] = nm
            elif nm not in e["names"]:
                e["names"].append(nm)
        nl = g(r, "Name non-latin script")
        if nl and nl not in e["names"]:
            e["names"].append(nl)
        b = g(r, "D.O.B")
        if b and b not in e["b"]:
            e["b"].append(b)
        for k in ("National Identifier number", "Passport number", "Business registration number (s)", "IMO number"):
            for v in re.split(r"[;,]|\s{2,}", g(r, k)):
                v = re.sub(r"[^\w-]", "", v)
                if len(v) >= 5 and v not in e["x"]:
                    e["x"].append(v)
        for v in ids_from(g(r, "Other Information")):
            if v not in e["x"]:
                e["x"].append(v)
        c = g(r, "Address Country")
        if c and c not in e["c"]:
            e["c"].append(c)
    out = []
    for uid, e in ents.items():
        pn = e["pn"] or (e["names"][0] if e["names"] else "")
        if pn:
            out.append(entry("UK", uid, e["t"], pn, e["names"], ", ".join(e["b"][:3]), e["p"], e["l"], e["x"][:12], ", ".join(e["c"][:3])))
    return out, rep


def parse_ca(files):
    root = ET.fromstring(files["xml"])
    out = []
    for i, rec in enumerate(root.iter("record")):
        f = {c.tag: clean(c.text) for c in rec}
        ent = f.get("EntityOrShip-EntiteOuNavire", "")
        imo = f.get("ShipIMONumber-NumeroOMIDuNavire", "")
        name = ent or " ".join(x for x in [f.get("GivenName-Prenom", ""), f.get("LastName-NomDeFamille", "")] if x)
        if not name:
            continue
        typ = "v" if imo else ("e" if ent else "i")
        al = [a.strip() for a in re.split(r"[;,]", f.get("Aliases-Alias", "")) if a.strip()]
        rid = "%s/%s/%s" % (f.get("Country-Pays", "").split(" / ")[0], f.get("Schedule-Annexe", ""), f.get("Item-NumeroDarticle", ""))
        out.append(entry("CA", rid, typ, name, al, f.get("DateOfBirthOrShipBuildDate-DateDeNaissanceOuDateDeConstructionDuNavire", "") if typ == "i" else "",
                         f.get("Country-Pays", ""), f.get("DateOfListing-DateDinscription", ""), [imo] if imo else [], ""))
    return out, ""


def parse_ch(files):
    root = ET.fromstring(files["xml"])
    date = root.get("date", "")
    progs = {}
    for p in root.iter("sanctions-program"):
        key = next((k.text for k in p.findall("program-key") if k.get("lang") == "eng"), "")
        for s in p.findall("sanctions-set"):
            progs[s.get("ssid")] = key
    out = []
    for t in root.findall("target"):
        mods = sorted(t.findall("modification"), key=lambda m: m.get("effective-date") or m.get("publication-date") or "")
        if mods and mods[-1].get("modification-type") == "de-listed":
            continue
        listed = next((m.get("effective-date") or m.get("publication-date") or "" for m in mods if m.get("modification-type") == "listed"), "")
        node, typ = None, "e"
        for tag, ty in (("individual", "i"), ("entity", "e"), ("object", "v")):
            node = t.find(tag)
            if node is not None:
                typ = ty
                break
        if node is None:
            continue
        names, dob, ids = [], [], []
        for idn in node.findall("identity"):
            for nm in idn.findall("name"):
                parts = sorted(nm.findall("name-part"), key=lambda p: int(p.get("order") or 0))
                if typ == "i":
                    fam = [p for p in parts if p.get("name-part-type") == "family-name"]
                    rest = [p for p in parts if p.get("name-part-type") != "family-name"]
                    parts = rest + fam
                base = " ".join(clean(p.findtext("value")) for p in parts if clean(p.findtext("value")))
                if base and base not in names:
                    names.append(base)
                for script in ("LATN", "CYRL"):
                    for k in range(3):
                        vs = []
                        for p in parts:
                            sv = [clean(x.text) for x in p.findall("spelling-variant") if x.get("script") == script and clean(x.text)]
                            vs.append(sv[k] if k < len(sv) else (sv[-1] if sv else clean(p.findtext("value"))))
                        v = " ".join(x for x in vs if x).strip()
                        if v and v not in names:
                            names.append(v)
            for d in idn.findall("day-month-year"):
                y, mo, da = d.get("year"), d.get("month"), d.get("day")
                if y:
                    dob.append("-".join(x for x in [y, (mo or "").zfill(2) if mo else "", (da or "").zfill(2) if da else ""] if x))
            for doc in idn.findall("identification-document"):
                n = clean(doc.findtext("number"))
                if n:
                    ids.append(re.sub(r"\s", "", n))
        if node is not None and typ == "v":
            ids += ids_from(" ".join(clean(x.text) for x in node.iter() if x.text))
        if not names:
            continue
        prog = progs.get(t.findtext("sanctions-set-id") or "", "")
        out.append(entry("CH", t.get("ssid"), typ, names[0], names[1:], ", ".join(dob[:3]), prog, listed, ids[:12], ""))
    return out, date


PARSERS = {"OFAC": lambda f: parse_ofac(f, "OFAC"), "OFACNS": lambda f: parse_ofac(f, "OFACNS"),
           "EU": parse_eu, "UK": parse_uk, "CA": parse_ca, "CH": parse_ch}


def key(e):
    return e["s"] + "|" + e["i"]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    offline = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--offline=")), "")
    out_dir = args[0] if args else "."
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.strftime("%Y-%m-%d")
    prev = {}
    try:
        prev = json.load(open(os.path.join(out_dir, "screen-index.json"), encoding="utf-8"))
    except Exception:
        prev = {}
    prev_src = {s["id"]: s for s in prev.get("sources", [])}
    prev_first = {}
    prev_by_src = {}
    cols = prev.get("cols") or []
    for row in prev.get("e", []):
        e = dict(zip(cols, row))
        prev_first[key(e)] = e.get("f", "")
        prev_by_src.setdefault(e["s"], []).append(e)

    entries, sources, errors = [], [], []
    for sid, S in SOURCES.items():
        info = {"id": sid, "name": S["name"], "page": S["page"], "url": list(S["files"].values())[0]}
        try:
            files = {}
            for k, u in S["files"].items():
                if offline:
                    fn = {"OFAC": {"sdn": "sdn.csv", "alt": "alt.csv", "add": "add.csv"},
                          "OFACNS": {"prim": "cons_prim.csv", "alt": "cons_alt.csv"},
                          "EU": {"csv": "eu.csv"}, "UK": {"csv": "uk.csv"}, "CA": {"xml": "ca.xml"}, "CH": {"xml": "ch.xml"}}[sid][k]
                    files[k] = open(os.path.join(offline, fn), "rb").read()
                else:
                    files[k] = fetch(u)
            got, fdate = PARSERS[sid](files)
            if len(got) < MIN_COUNT[sid]:
                raise RuntimeError("записей %d — меньше порога %d, похоже на неполный файл" % (len(got), MIN_COUNT[sid]))
            info.update(status="ok", count=len(got), fileDate=fdate, fetched=now.strftime("%Y-%m-%dT%H:%MZ"),
                        sha256=hashlib.sha256(b"".join(files[k] for k in sorted(files))).hexdigest()[:16])
            entries += got
        except Exception as ex:
            old = prev_by_src.get(sid, [])
            p = prev_src.get(sid, {})
            info.update(status="stale" if old else "fail", count=len(old), fileDate=p.get("fileDate", ""),
                        fetched=p.get("fetched", ""), error=str(ex)[:300])
            entries += old
            errors.append("%s: %s" % (sid, ex))
        sources.append(info)

    if not any(s["status"] == "ok" for s in sources):
        print("ОШИБКА: ни один список не обновлён — " + "; ".join(errors))
        sys.exit(1)

    seen = set()
    added, removed = [], []
    fresh_src = {s["id"] for s in sources if s["status"] == "ok"}
    for e in entries:
        k = key(e)
        seen.add(k)
        if k in prev_first:
            e["f"] = prev_first[k] or e.get("f", "")
        else:
            e["f"] = e.get("f") or (today if prev else "")
            if prev and e["s"] in fresh_src and e["s"] in prev_src:
                added.append(e)
    if prev:
        for sid, old in prev_by_src.items():
            if sid in fresh_src:
                removed += [e for e in old if key(e) not in seen]

    COLS = ["s", "i", "t", "n", "a", "b", "p", "l", "x", "c", "f"]
    idx = {"schema": "cm2-screen/1", "built": now.strftime("%Y-%m-%dT%H:%MZ"), "sources": sources, "cols": COLS,
           "e": [[e.get(c, "" if c not in ("a", "x") else []) for c in COLS] for e in entries]}
    raw = json.dumps(idx, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(out_dir, "screen-index.json"), "w", encoding="utf-8") as fh:
        fh.write(raw)

    # сводка изменений: накапливается за 45 дней
    dpath = os.path.join(out_dir, "screen-delta.json")
    try:
        delta = json.load(open(dpath, encoding="utf-8"))
    except Exception:
        delta = {"schema": "cm2-screen-delta/1", "items": []}
    keep_from = (now - datetime.timedelta(days=45)).strftime("%Y-%m-%d")
    items = [x for x in delta.get("items", []) if x.get("d", "") >= keep_from]
    small = lambda e: {"s": e["s"], "i": e["i"], "t": e["t"], "n": e["n"], "a": e["a"][:8], "b": e["b"], "p": e["p"], "x": e["x"][:6]}
    items += [dict(small(e), d=today, op="add") for e in added]
    items += [dict(small(e), d=today, op="del") for e in removed]
    delta = {"schema": "cm2-screen-delta/1", "built": idx["built"], "items": items}
    with open(dpath, "w", encoding="utf-8") as fh:
        json.dump(delta, fh, ensure_ascii=False, separators=(",", ":"))

    print(json.dumps({"built": idx["built"], "entries": len(entries), "bytes": len(raw.encode("utf-8")), "added": len(added),
                      "removed": len(removed), "sources": [{k: s.get(k) for k in ("id", "status", "count", "fileDate", "error") if s.get(k) is not None} for s in sources]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
