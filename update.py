#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jリーグ・マネー図鑑 デイリー更新スクリプト（GitHub Actions で毎日実行）。
- 最終更新日を更新（確実）
- 選手スタッツ（出場/得点/アシスト）を Football LAB から更新（確実）
- 確定移籍 IN/OUT を公式まとめから best-effort で更新（取れた時だけ）
安全第一：更新後に妥当性チェックし、少しでも壊れていたら元に戻して書き込まない。
"""
import io, re, sys, json, datetime

HTML = "index.html"
JST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(JST).strftime("%Y-%m-%d")
YEAR = datetime.datetime.now(JST).year
UA = {"User-Agent": "Mozilla/5.0 (compatible; jmoney-bot/1.0)"}

TRANSFER_URL = "https://www.jleague.jp/j1/special/transfer/"
# 公式まとめページのクラブ正式名 → サイト内の短縮キー（T26のキーと一致させる）
CLUB_NAME_MAP = {
    "鹿島アントラーズ": "鹿島", "浦和レッズ": "浦和", "柏レイソル": "柏",
    "ＦＣ東京": "FC東京", "東京ヴェルディ": "東京V", "ＦＣ町田ゼルビア": "町田",
    "川崎フロンターレ": "川崎F", "横浜Ｆ・マリノス": "横浜FM", "清水エスパルス": "清水",
    "名古屋グランパス": "名古屋", "京都サンガF.C.": "京都", "ガンバ大阪": "G大阪",
    "セレッソ大阪": "C大阪", "ヴィッセル神戸": "神戸", "ファジアーノ岡山": "岡山",
    "サンフレッチェ広島": "広島", "アビスパ福岡": "福岡", "Ｖ・ファーレン長崎": "長崎",
    "ジェフユナイテッド千葉": "千葉", "水戸ホーリーホック": "水戸",
}
TRANSFER_TYPE_MAP = [("完全", "完"), ("期限付き", "期"), ("満了", "満"), ("復帰", "復"), ("新加入", "新")]
MAX_ENTRIES_PER_DIRECTION = 6

def norm(n):
    return re.sub(r"[\s　・.]", "", n or "")

try:
    import requests
    from bs4 import BeautifulSoup
except Exception as e:
    print("依存ライブラリなし:", e); sys.exit(0)


def fetch_stats():
    """Football LAB の得点ランキングから {正規化名:(出場,G,A)} を返す。"""
    out = {}
    for yr in (YEAR, YEAR - 1):
        url = f"https://www.football-lab.jp/summary/player_ranking/j1/?year={yr}&data=goal"
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            print("stats fetch err:", e); continue
        tbl = None
        for t in soup.find_all("table"):
            h = t.get_text()
            if "ゴール" in h and "出場" in h and "アシスト" in h:
                tbl = t; break
        if not tbl:
            continue
        cnt = 0
        for tr in tbl.find_all("tr"):
            a = tr.find("a", href=re.compile(r"/player/"))
            if not a:
                continue
            name = norm(a.get_text())
            ints = [c.get_text(strip=True) for c in tr.find_all("td")]
            ints = [int(x) for x in ints if re.fullmatch(r"\d+", x)]
            if len(ints) >= 3 and name:
                g, app, ast = ints[-3], ints[-2], ints[-1]
                out[name] = (app, g, ast); cnt += 1
        if cnt >= 5:
            print(f"stats: {yr} シーズンから {cnt} 名取得")
            return out
    print("stats: 取得できず")
    return out


def update_pstat(s, stats):
    if not stats:
        return s, False
    m = re.search(r"const PSTAT=\{.*?\};", s, re.S)
    if not m:
        return s, False
    block = m.group(0)
    def repl(mm):
        key = mm.group(1)
        if key in stats:
            app, g, a = stats[key]
            return f'"{key}":{{app:{app},g:{g},a:{a}}}'
        return mm.group(0)
    newblock = re.sub(r'"([^"]+)":\{app:\d+,g:\d+,a:\d+\}', repl, block)
    if newblock != block:
        return s.replace(block, newblock, 1), True
    return s, False


def _transfer_type_code(label):
    for kw, code in TRANSFER_TYPE_MAP:
        if kw in label:
            return code
    return "移"


def _parse_transfer_table(table):
    rows = []
    for tr in table.find_all("tr"):
        if tr.find("th"):
            continue
        pos_el = tr.select_one(".o-table__player-position")
        name_el = tr.select_one(".o-table__player-name")
        club_el = tr.select_one(".o-table__cell--transfer-club p")
        type_el = tr.select_one(".o-table__cell--transfer-type p")
        if not (pos_el and name_el and club_el and type_el):
            continue
        rows.append([
            pos_el.get_text(strip=True), name_el.get_text(strip=True),
            club_el.get_text(strip=True), _transfer_type_code(type_el.get_text(strip=True)),
        ])
    return rows


def debug_probe():
    r = requests.get(TRANSFER_URL, headers=UA, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    nd = soup.find("script", id="__NEXT_DATA__")
    print("__NEXT_DATA__ present:", bool(nd))
    if nd:
        print("__NEXT_DATA__ len:", len(nd.string or ""))
        print("__NEXT_DATA__ head:", (nd.string or "")[:1500])
    # also check for OUT-labeled tables anywhere and count all tables per club more precisely
    sections = [sec for sec in soup.find_all("div", class_="p-transfer-list") if sec.find("table")]
    for sec in sections[:3]:
        name = sec.get_text(" ", strip=True)[:12]
        tabs = sec.find_all(attrs={"role": "tab"}) or sec.find_all("button")
        print(name, "tables=", len(sec.find_all("table")), "buttons/tabs=", len(tabs))
        for b in tabs[:4]:
            print("   TAB:", b.get_text(strip=True), b.attrs)
    other_scripts = [sc for sc in soup.find_all("script") if sc.get("id") and "next" in sc.get("id", "").lower()]
    print("other next-ish scripts:", [sc.get("id") for sc in other_scripts])


def fetch_transfers():
    """Jリーグ公式まとめページから確定移籍IN/OUTを取得。{短縮キー:{"i":[[pos,name,club,type]],"o":[...]}}"""
    try:
        r = requests.get(TRANSFER_URL, headers=UA, timeout=30)
        if r.status_code != 200:
            print("transfers: status", r.status_code); return {}
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print("transfers fetch err:", e); return {}

    sections = [sec for sec in soup.find_all("div", class_="p-transfer-list") if sec.find("table")]
    result = {}
    for sec in sections:
        header_txt = sec.get_text(" ", strip=True)
        official = next((n for n in CLUB_NAME_MAP if header_txt.startswith(n)), None)
        if not official:
            continue
        tables = sec.find_all("table")
        ins = _parse_transfer_table(tables[0]) if len(tables) >= 1 else []
        outs = _parse_transfer_table(tables[1]) if len(tables) >= 2 else []
        result[CLUB_NAME_MAP[official]] = {
            "i": ins[:MAX_ENTRIES_PER_DIRECTION], "o": outs[:MAX_ENTRIES_PER_DIRECTION],
        }
    print(f"transfers: {len(result)}/{len(CLUB_NAME_MAP)} クラブ取得")
    if len(result) < 15:
        print("transfers: 取得クラブ数が少なすぎるため安全のためスキップ")
        return {}
    return result


def update_transfers(s, transfers):
    if not transfers:
        return s, False
    m = re.search(r"const T26=\{.*?\n\};", s, re.S)
    if not m:
        return s, False
    block = orig_block = m.group(0)
    changed = []
    for key, data in transfers.items():
        ins_js = json.dumps(data.get("i", []), ensure_ascii=False, separators=(",", ":"))
        outs_js = json.dumps(data.get("o", []), ensure_ascii=False, separators=(",", ":"))
        pattern = re.compile(r'^( "%s":\{)i:\[.*\],o:\[.*\](\},?)$' % re.escape(key), re.MULTILINE)
        newblock, n = pattern.subn(
            lambda mo: mo.group(1) + "i:" + ins_js + ",o:" + outs_js + mo.group(2), block, count=1)
        if n:
            block = newblock
            changed.append(key)
    if block == orig_block:
        return s, False
    print("transfers: 更新クラブ", ",".join(changed))
    return s.replace(orig_block, block, 1), True


def update_date(s):
    s2 = re.sub(r'(<b id="t26updated">)[^<]*(</b>)', r"\g<1>" + TODAY + r"\g<2>", s, count=1)
    return s2, (s2 != s)


def valid(s):
    if not s.rstrip().endswith("</html>"):
        return False
    if s.count("<div") != s.count("</div>"):
        return False
    if s.count("<section id=") != s.count("</section>"):
        return False
    for marker in ('const PSTAT=', 'const T26=', 'id="t26updated"', "function openPlayer"):
        if marker not in s:
            return False
    # 波括弧の対応（ざっくり）
    if s.count("{") != s.count("}"):
        return False
    return True


def main():
    try:
        s = io.open(HTML, encoding="utf-8").read()
    except Exception as e:
        print("index.html 読み込み失敗:", e); sys.exit(0)
    orig = s
    changes = []

    s, ok = update_date(s)
    if ok: changes.append("最終更新日")

    try:
        stats = fetch_stats()
        s, ok = update_pstat(s, stats)
        if ok: changes.append("選手スタッツ")
    except Exception as e:
        print("stats 更新スキップ:", e)

    try:
        transfers = fetch_transfers()
        s, ok = update_transfers(s, transfers)
        if ok: changes.append("移籍情報")
    except Exception as e:
        print("transfers 更新スキップ:", e)

    if s == orig:
        print("変更なし"); return
    if not valid(s):
        print("妥当性チェック失敗 → 書き込みを中止（元のまま）"); return
    io.open(HTML, "w", encoding="utf-8").write(s)
    print("更新しました:", "、".join(changes) if changes else "(なし)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--debug-probe":
        debug_probe()
    else:
        main()
