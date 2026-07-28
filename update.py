#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jリーグ・マネー図鑑 更新スクリプト。
- python update.py         : デイリー更新（GitHub Actionsで毎日実行）
    - 最終更新日を更新（確実）
    - 選手スタッツ（出場/得点/アシスト）を Football LAB から更新（確実）
    - 確定移籍 IN/OUT を公式まとめから best-effort で更新（取れた時だけ）
- python update.py --finance : 会計情報の月次更新（別ワークフローから月1回実行）
    - Jリーグ公式「クラブ経営情報開示資料」PDFの要旨（売上高合計・前期比など）を更新
    - クラブ別の詳細決算数値（資産・人件費等）はPDFの表構造が年により変わり誤読リスクが
      高いため自動反映の対象外。「本発表」確定後に人手で反映する運用とする。
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

FINANCE_PDF_URL = "https://aboutj.jleague.jp/corporate/assets/pdf/club_info/club_doc-2025.pdf"

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


def fetch_transfers():
    """Jリーグ公式まとめページから確定移籍IN/OUTを取得。{短縮キー:{"i":[...]} または {"i":[...],"o":[...]}}
    公式ページは（クラブにより）OUT側の表がHTMLに含まれないことがあるため、
    OUT表が実際に見つかったクラブについてのみ "o" キーを含める（＝見つからない限り既存データは保持）。
    """
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
        entry = {}
        if len(tables) >= 1:
            entry["i"] = _parse_transfer_table(tables[0])[:MAX_ENTRIES_PER_DIRECTION]
        if len(tables) >= 2:
            entry["o"] = _parse_transfer_table(tables[1])[:MAX_ENTRIES_PER_DIRECTION]
        if entry:
            result[CLUB_NAME_MAP[official]] = entry
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
        line_re = re.compile(r'^ "%s":\{i:(\[.*\]),o:(\[.*\])\}(,?)$' % re.escape(key), re.MULTILINE)
        mo = line_re.search(block)
        if not mo:
            continue
        old_i_json, old_o_json, trailing = mo.group(1), mo.group(2), mo.group(3)
        new_i_json = (json.dumps(data["i"], ensure_ascii=False, separators=(",", ":"))
                      if "i" in data else old_i_json)
        new_o_json = (json.dumps(data["o"], ensure_ascii=False, separators=(",", ":"))
                      if "o" in data else old_o_json)
        if new_i_json == old_i_json and new_o_json == old_o_json:
            continue
        new_line = f' "{key}":{{i:{new_i_json},o:{new_o_json}}}{trailing}'
        block = block[:mo.start()] + new_line + block[mo.end():]
        changed.append(key)
    if block == orig_block:
        return s, False
    print("transfers: 更新クラブ", ",".join(changed))
    return s.replace(orig_block, block, 1), True


def update_date(s):
    s2 = re.sub(r'(<b id="t26updated">)[^<]*(</b>)', r"\g<1>" + TODAY + r"\g<2>", s, count=1)
    return s2, (s2 != s)


def fetch_finance_headline():
    """公式PDFの要旨（1-2. 主なトピックス）から売上高サマリーを抽出。
    詳細なクラブ別数値はPDFの表構造に依存し誤読リスクが高いため対象外。
    """
    try:
        import pdfplumber
        r = requests.get(FINANCE_PDF_URL, headers=UA, timeout=30)
        if r.status_code != 200:
            print("finance: status", r.status_code); return None
        text = ""
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for page in pdf.pages[:5]:
                text += page.extract_text() or ""
    except Exception as e:
        print("finance fetch err:", e); return None

    m_total = re.search(r"売上高は(\d+)クラブ合計で([\d,]+)億円", text)
    m_yoy = re.search(r"前期比(\d+)%の成長", text)
    m_grow = re.search(r"(\d+)クラブが増収", text)
    m_league = re.search(r"Ｊクラブ全体での売上高は([\d,]+)億円超", text)
    if not (m_total and m_yoy and m_grow and m_league):
        print("finance: 主要指標を抽出できず → スキップ")
        return None
    return {
        "clubs": m_total.group(1),
        "total": m_total.group(2).replace(",", ""),
        "yoy": m_yoy.group(1),
        "grow": m_grow.group(1),
        "league_total": m_league.group(1).replace(",", ""),
    }


def update_finance(s, fin):
    if not fin:
        return s, False
    fields = {
        "finYear": fin["clubs"], "finTotal": fin["total"], "finYoY": fin["yoy"],
        "finGrowClubs": fin["grow"], "finLeagueTotal": fin["league_total"], "finAsOf": TODAY,
    }
    changed = False
    for elem_id, val in fields.items():
        pattern = r'(<b id="%s">)[^<]*(</b>)' % re.escape(elem_id)
        s2 = re.sub(pattern, r"\g<1>" + str(val) + r"\g<2>", s, count=1)
        if s2 != s:
            changed = True
        s = s2
    return s, changed


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


def main_finance():
    try:
        s = io.open(HTML, encoding="utf-8").read()
    except Exception as e:
        print("index.html 読み込み失敗:", e); sys.exit(0)
    orig = s

    try:
        fin = fetch_finance_headline()
        s, ok = update_finance(s, fin)
    except Exception as e:
        print("finance 更新スキップ:", e); return

    if s == orig:
        print("変更なし"); return
    if not valid(s):
        print("妥当性チェック失敗 → 書き込みを中止（元のまま）"); return
    io.open(HTML, "w", encoding="utf-8").write(s)
    print("更新しました: 会計情報(2025年度速報)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--finance":
        main_finance()
    else:
        main()
