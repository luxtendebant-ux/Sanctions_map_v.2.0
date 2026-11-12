#!/usr/bin/env python3
"""Комплаенс-карта 2.0 — календарь контрольных дат (deadlines.ics) из открытой выгрузки карты map-data-open.json.

Без ИИ. Подписка: Google Календарь → «Другие календари» → «+» → «По URL» →
https://luxtendebant-ux.github.io/Sanctions_map_v.2.0/deadlines.ics
У каждого события — напоминания за 3 дня и в день события; UID постоянный (дата + текст), поэтому при обновлении
календарь не дублирует события. Ориентировочные даты помечены «≈». Сведения закрытого раздела в выгрузке отсутствуют.
Запуск: python3 -I build_deadlines_ics.py <map-data-open.json> <deadlines.ics>
"""
import sys, json, re, hashlib, datetime

SITE = "https://luxtendebant-ux.github.io/Sanctions_map_v.2.0/"


def esc(s):
    s = re.sub(r"<[^>]+>", "", str(s or "")).replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def fold(line):
    b = line.encode("utf-8")
    out, cur = [], b""
    for ch in line:
        c = ch.encode("utf-8")
        if len(cur) + len(c) > 73:
            out.append(cur)
            cur = b" " + c
        else:
            cur += c
    out.append(cur)
    return b"\r\n".join(out).decode("utf-8")


def main():
    src, dst = sys.argv[1], sys.argv[2]
    d = json.load(open(src, encoding="utf-8"))
    if d.get("schema") != "compliance-map-open/1":
        print("ОШИБКА: неожиданный формат выгрузки"); sys.exit(1)
    cut = (d.get("meta") or {}).get("cut", "")
    items = (d.get("deadlines") or {}).get("items") or []
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Komplaens-karta 2.0//Kontrolnye daty//RU", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
         "X-WR-CALNAME:Комплаенс-карта 2.0 — контрольные даты", "X-WR-TIMEZONE:Asia/Irkutsk",
         "X-WR-CALDESC:" + esc("Контрольные даты санкционных мер из Комплаенс-карты 2.0 (срез %s). Аналитический материал; первоисточник — в карточке." % cut),
         "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"]
    n = 0
    for x in items:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(x.get("date", "")))
        if not m:
            continue
        day = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        text = re.sub(r"<[^>]+>", "", str(x.get("text", ""))).strip()
        uid = hashlib.sha1((x.get("date", "") + "|" + text[:120]).encode("utf-8")).hexdigest()[:20] + "@cm2-deadlines"
        summ = ("≈ " if x.get("approx") else "") + (x.get("jur") + ": " if x.get("jur") else "") + text
        desc = text + ("\n\nКарточка: " + re.sub(r"<[^>]+>", "", x.get("title", "")) if x.get("title") else "") + \
            ("\nДата ориентировочная." if x.get("approx") else "") + "\n\nКомплаенс-карта 2.0, срез %s: %s" % (cut, SITE)
        L += ["BEGIN:VEVENT", "UID:" + uid, "DTSTAMP:" + now, "DTSTART;VALUE=DATE:" + day.strftime("%Y%m%d"),
              "DTEND;VALUE=DATE:" + (day + datetime.timedelta(days=1)).strftime("%Y%m%d"),
              "SUMMARY:" + esc(summ[:240]), "DESCRIPTION:" + esc(desc[:1800]), "URL:" + SITE, "TRANSP:TRANSPARENT",
              "CATEGORIES:" + esc(x.get("jur") or "Санкции"),
              "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + esc("Через 3 дня: " + summ[:150]), "TRIGGER:-P3D", "END:VALARM",
              "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + esc("Сегодня: " + summ[:150]), "TRIGGER:PT9H", "END:VALARM",
              "END:VEVENT"]
        n += 1
    L.append("END:VCALENDAR")
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        fh.write("\r\n".join(fold(l) for l in L) + "\r\n")
    print(json.dumps({"cut": cut, "events": n}, ensure_ascii=False))


if __name__ == "__main__":
    main()
