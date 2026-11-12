#!/usr/bin/env python3
"""Комплаенс-карта 2.0 — подготовка данных для ИИ-агентов (без участия модели, без изменения карты).
Запуск: python3 -I cm2_sync.py <папка_выдачи>
Читает только опубликованную открытую часть карты (map-data-open.json с сайта), проверяет её и пишет пять файлов:
  cm2-map-data-open_ДД-ММ-ГГГГ.json — полная открытая часть (для рабочего места, читает страница);
  cm2-brief_ДД-ММ-ГГГГ.md            — краткая сводка (для чатов проекта: ~5–10 тыс. знаков);
  cm2-index-sanctions|export|ru_ДД-ММ-ГГГГ.md — перечни карточек по разделам, одна строка на карточку.
Последняя строка вывода — JSON с датой среза, именами, путями и sha256 файлов; при ошибке — «ОШИБКА: …» и код 1.
Карту, репозиторий и закрытые разделы не читает и не меняет."""
import sys, os, json, re, hashlib, time, urllib.request

URL = "https://luxtendebant-ux.github.io/Sanctions_map_v.2.0/map-data-open.json"
# сведения закрытого раздела в открытой выгрузке быть не должно — иначе ничего не выпускаем
PRIVATE = re.compile(r"Буйнов|Buinov|Buynov|Седых|Sedykh", re.I)
STATUS = {"active": "Действует", "drafting": "Принята, не вступила", "pending": "В процессе принятия",
          "announced": "Заявлено официально", "rumor": "Слух/неподтверждено", "expired": "Истекла/требует сверки"}
GROUPS = [("sections", "Санкции"), ("exportControl", "Экспортный контроль"), ("russianMeasures", "Меры РФ")]

def fail(msg):
    print("ОШИБКА: " + msg); sys.exit(1)

def strip(s, n=None):
    s = re.sub(r"<[^>]+>", "", str(s or "")).replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip()
    return s if n is None or len(s) <= n else s[: n - 1].rstrip() + "…"

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "out"
    os.makedirs(out, exist_ok=True)
    raw = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL + "?t=%d" % time.time(), headers={"Cache-Control": "no-cache"})
            raw = urllib.request.urlopen(req, timeout=60).read()
            break
        except Exception as e:
            err = e; time.sleep(15)
    if raw is None: fail("сайт карты недоступен: %s" % err)
    try:
        d = json.loads(raw.decode("utf-8"))
    except Exception as e:
        fail("выгрузка не читается как JSON: %s" % e)
    meta = d.get("meta") or {}
    if d.get("schema") != "compliance-map-open/1" or not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", str(meta.get("cut", ""))):
        fail("неожиданный формат выгрузки (schema/meta.cut)")
    cards = sum(len(s.get("items", [])) for g, _ in GROUPS for s in (d.get(g) or []))
    if cards < 150: fail("в выгрузке всего %d карточек — похоже на неполные данные" % cards)
    m = PRIVATE.search(raw.decode("utf-8"))
    if m: fail("в открытой выгрузке найдены сведения закрытого раздела («%s») — файлы не выпускаются" % m.group(0))
    cut = meta["cut"]; tag = cut.replace(".", "-")

    # --- краткая сводка ---
    B = ["# Комплаенс-карта 2.0 — краткая сводка, срез %s" % cut,
         "Источник: https://luxtendebant-ux.github.io/Sanctions_map_v.2.0/ (открытая часть). Предыдущий срез: %s. Версия карты: %s." % (meta.get("prev", "—"), meta.get("release", "—")), ""]
    ex = d.get("execSummary") or {}
    if ex.get("points"):
        B.append("## Главное за период")
        B += ["- **%s.** %s" % (strip(p.get("h")), strip(p.get("t"), 300)) for p in ex["points"]]
    if ex.get("actions"):
        B.append("\n## Что сделать")
        B += ["- " + strip(a, 220) for a in ex["actions"]]
    cl = d.get("changelog") or {}
    if cl.get("items"):
        B.append("\n## Что изменилось (%s → %s)" % (cl.get("from", "—"), cl.get("to", "—")))
        kinds = {"new": "новое", "upd": "обновлено", "fix": "исправлено"}
        for it in [x for x in cl["items"] if x.get("kind") in ("new", "fix")][:25]:
            B.append("- [%s] %s — %s (%s)" % (kinds.get(it.get("kind"), it.get("kind")), strip(it.get("secLabel")), strip(it.get("title"), 130), STATUS.get(it.get("status"), it.get("status"))))
        nu = sum(1 for x in cl["items"] if x.get("kind") == "upd")
        if nu: B.append("- обновлено (в т.ч. плановая сверка без изменения сути): %d карточек — см. перечни" % nu)
    dl = (d.get("deadlines") or {}).get("items") or []
    if dl:
        B.append("\n## Контрольные даты")
        for x in sorted(dl, key=lambda x: x.get("date", ""))[:15]:
            B.append("- %s%s — %s%s" % ("≈" if x.get("approx") else "", x.get("date"), (strip(x.get("jur")) + ": ") if x.get("jur") else "", strip(x.get("text"), 160)))
    oc = d.get("officerConclusion") or {}
    if oc.get("text"):
        B.append("\n## Заключение комплаенс-офицера (открытая часть)")
        B.append(strip(oc["text"], 1800))
    B.append("\n_Аналитический материал на дату среза, не юридическое заключение. Перед решением — сверка с первоисточником по ссылке в карточке._")
    brief = "\n".join(B) + "\n"

    # --- перечни карточек: по файлу на раздел (чат читает только нужный) ---
    idx = {}
    for g, gname in GROUPS:
        I = ["# Комплаенс-карта 2.0 — %s: перечень карточек, срез %s" % (gname, cut),
             "Формат: статус | дата | заголовок | уровень источника | ссылка на первоисточник. Полный текст — в рабочем месте или на сайте карты.", ""]
        for sct in d.get(g) or []:
            I.append("\n## " + strip(sct.get("title")))
            for it in sct.get("items", []):
                src = it.get("source") or {}
                I.append("- %s | %s | %s | %s | %s" % (STATUS.get(it.get("status"), it.get("status")), strip(it.get("date"), 40), strip(it.get("title"), 140), src.get("level", "—"), src.get("url", "")))
        if g == "sections" and d.get("rumors"):
            I.append("\n## Слухи и шум (не основание для решений)")
            I += ["- %s | %s | уровень %s" % (strip(r.get("date"), 40), strip(r.get("title"), 140), r.get("level", "—")) for r in d["rumors"]]
        idx[g] = "\n".join(I) + "\n"

    files = [("cm2-map-data-open_%s.json" % tag, raw, "application/json"),
             ("cm2-brief_%s.md" % tag, brief.encode("utf-8"), "text/markdown"),
             ("cm2-index-sanctions_%s.md" % tag, idx["sections"].encode("utf-8"), "text/markdown"),
             ("cm2-index-export_%s.md" % tag, idx["exportControl"].encode("utf-8"), "text/markdown"),
             ("cm2-index-ru_%s.md" % tag, idx["russianMeasures"].encode("utf-8"), "text/markdown")]
    res = {"cut": cut, "cards": cards, "files": []}
    for name, data, mime in files:
        p = os.path.abspath(os.path.join(out, name))
        with open(p, "wb") as f: f.write(data)
        res["files"].append({"name": name, "path": p, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "mime": mime})
    print(json.dumps(res, ensure_ascii=False))

if __name__ == "__main__":
    main()
