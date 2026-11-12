#!/usr/bin/env python3
"""Комплаенс-карта 2.0 — подготовка независимой проверки опубликованного среза (без ИИ, только чтение).

Берёт с сайта map-data-open.json, выбирает карточки блока «Что изменилось» (новые и обновлённые за срез),
открывает ссылку на первоисточник каждой карточки (код ответа, итоговый адрес, тип, размер) и пишет пакет для проверяющего:
  verify-pack.json — карточки (полный текст) + результат открытия ссылок + механические замечания.
Механические замечания (без ИИ): ссылка не открывается/не http(s); дата карточки в будущем; verified ≠ срезу у новой карточки;
статус «Действует» при дате вступления позже среза; в тексте нет уровня источника (A/B/C/D).
Запуск: python3 -I verify_prepare.py <выход.json> [--map=<map-data-open.json>]
"""
import sys, json, re, time, datetime, urllib.request, urllib.error

SITE = "https://luxtendebant-ux.github.io/Sanctions_map_v.2.0/map-data-open.json"
UA = {"User-Agent": "Mozilla/5.0 (cm2-verify; +https://luxtendebant-ux.github.io/Sanctions_map_v.2.0/)", "Accept": "text/html,application/pdf,*/*"}
GROUPS = {"SECTIONS": "sections", "EXPORT_CONTROL": "exportControl", "RUSSIAN_MEASURES": "russianMeasures"}


def probe(url):
    if not re.match(r"^https?://", url or ""):
        return {"ok": False, "code": None, "note": "ссылка отсутствует или не http(s)"}
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, headers=UA, method=method)
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read(200000) if method == "GET" else b""
                return {"ok": r.status < 400, "code": r.status, "final": r.geturl(), "type": r.headers.get("Content-Type", "")[:60],
                        "bytes": len(body) if body else r.headers.get("Content-Length")}
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405, 400, 404, 501):
                continue
            return {"ok": False, "code": e.code, "note": str(e.reason)[:80]}
        except Exception as e:
            if method == "HEAD":
                continue
            return {"ok": False, "code": None, "note": str(e)[:120]}
    return {"ok": False, "code": None, "note": "не открылась"}


def ru(s):
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s or "")
    return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def main():
    out = sys.argv[1]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[2:] if a.startswith("--") and "=" in a)
    raw = open(opt["map"], "rb").read() if opt.get("map") else urllib.request.urlopen(urllib.request.Request(SITE + "?t=%d" % time.time(), headers={"Cache-Control": "no-cache"}), timeout=90).read()
    d = json.loads(raw.decode("utf-8"))
    cut = d["meta"]["cut"]
    cutd = ru(cut)
    ch = d.get("changelog") or {}
    pack = []
    for it in ch.get("items", []):
        grp = d.get(GROUPS.get(it.get("group"), ""), [])
        sec = next((s for s in grp if s.get("id") == it.get("sec")), None)
        card = next((c for c in (sec or {}).get("items", []) if c.get("title") == it.get("title")), None)
        if not card:
            pack.append({"kind": it.get("kind"), "sec": it.get("secLabel"), "title": it.get("title"), "problems": ["карточка из «Что изменилось» не найдена в разделе"]})
            continue
        src = card.get("source") or {}
        pr = probe(src.get("url", ""))
        probs = []
        if not pr.get("ok"):
            probs.append("первоисточник не открылся: %s %s" % (pr.get("code"), pr.get("note", "")))
        dd = ru(card.get("date", ""))
        if dd and cutd and dd > cutd and card.get("status") == "active":
            probs.append("статус «Действует», но дата карточки позже среза")
        if it.get("kind") == "new" and card.get("verified") and card.get("verified") != cut:
            probs.append("новая карточка: verified %s ≠ срез %s" % (card.get("verified"), cut))
        if not src.get("level") and not re.search(r"\((A|B|C|D)\)|уровень [ABCD]", card.get("detail", "") + card.get("desc", "")):
            probs.append("не указан уровень источника")
        pack.append({"kind": it.get("kind"), "sec": it.get("secLabel"), "status": card.get("status"), "date": card.get("date"),
                     "verified": card.get("verified"), "expires": card.get("expires"), "title": card.get("title"), "desc": card.get("desc"),
                     "detail": (card.get("detail") or "")[-4000:], "source": src, "link": pr, "problems": probs})
    res = {"cut": cut, "prev": ch.get("from"), "checkedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "cards": len(pack), "new": sum(1 for p in pack if p.get("kind") == "new"), "linkFail": sum(1 for p in pack if not (p.get("link") or {}).get("ok")),
           "mechanical": sum(len(p["problems"]) for p in pack), "items": pack}
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: res[k] for k in ("cut", "prev", "cards", "new", "linkFail", "mechanical")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
