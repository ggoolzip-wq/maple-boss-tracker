#!/usr/bin/env python3
"""보스 캐릭터 관리 — 로컬 도우미 서버 (선택 사항)

사용법:  python serve.py      (Windows: start-windows.bat 더블클릭, 창은 열어 두기만 하면 됩니다)
         python serve.py --open   → 브라우저로 http://localhost:8787 도 함께 엽니다

하는 일
1) /nexon-prices : 메이플스토리 공식 홈페이지 '업데이트' 공지에서 '강렬한 힘의 결정' 판매 가격표를 찾아 JSON으로 돌려줍니다.
   (index.html 을 더블클릭으로 연 file:// 페이지에서도 호출할 수 있도록 CORS 허용 — 이 PC 안에서만 접속 가능)
2) /nxapi/...    : 넥슨 Open API 프록시 (브라우저에서 직접 호출이 막히는 환경용)
3) 이 폴더의 index.html 제공 (http://localhost:8787)

- 127.0.0.1(내 PC)에만 열리며, API 키는 저장/기록하지 않습니다. 외부 라이브러리 필요 없음(파이썬 표준 라이브러리).
"""
import http.server, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error, webbrowser
from html.parser import HTMLParser

PORT = int(os.environ.get("PORT", "8787"))
ROOT = os.path.dirname(os.path.abspath(__file__))
UPSTREAM = "https://open.api.nexon.com"
PREFIX = "/nxapi"
SITE = "https://maplestory.nexon.com"
LIST_URL = SITE + "/News/Update?page={page}"
POST_URL = SITE + "/News/Update/{id}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
FIXTURE_DIR = os.environ.get("MBT_FIXTURE_DIR")  # 테스트용: 실제 접속 대신 저장된 HTML 사용
_cache = {"at": 0, "key": None, "data": None}


# ---------------------------------------------------------------- 가져오기
def fetch(url, timeout=15):
    if FIXTURE_DIR:  # list_page1.html, post_813.html ...
        m = re.search(r"page=(\d+)", url)
        name = f"list_page{m.group(1)}.html" if "page=" in url else "post_%s.html" % url.rstrip("/").rsplit("/", 1)[-1]
        path = os.path.join(FIXTURE_DIR, name)
        if not os.path.exists(path):
            raise urllib.error.HTTPError(url, 404, "fixture not found", None, None)
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9", "Referer": SITE + "/News/Update"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        cs = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(cs, errors="replace")


def parse_list(html):
    """업데이트 목록 → [{id, title, date}] (최신순)"""
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(?:https?://maplestory\.nexon\.com)?/news/update/(\d+)[^"]*"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+href="[^"]*/news/update/\d+|</ul>|$)',
                         html, re.I | re.S):
        pid = int(m.group(1))
        if pid in seen:
            continue
        seen.add(pid)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        title = re.sub(r"&nbsp;", " ", title)
        d = re.search(r"(\d{4}\.\d{2}\.\d{2})", m.group(3))
        out.append({"id": pid, "title": title, "date": d.group(1) if d else ""})
    out.sort(key=lambda x: -x["id"])
    return out


class _PostParser(HTMLParser):
    """공지 본문의 표(셀 텍스트)와 문단 텍스트를 순서대로 모음"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []          # ('p', text) | ('table', [[cell,...],...])
        self.title = ""
        self._t = []              # 표 스택
        self._cell = None
        self._buf = []
        self._in_title = 0
        self._title_buf = []

    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "li", "br"}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "p" and "qs_title" in (a.get("class") or ""):
            self._in_title = 1
        if tag == "table":
            self._flush()
            self._t.append([])
        elif tag == "tr" and self._t:
            self._t[-1].append([])
        elif tag in ("td", "th") and self._t:
            self._cell = []
        elif tag in self.BLOCK and self._cell is None and not self._t:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "p" and self._in_title:
            self._in_title = 0
            self.title = re.sub(r"\s+", " ", "".join(self._title_buf)).strip()
        if tag in ("td", "th") and self._t and self._cell is not None:
            txt = re.sub(r"\s+", " ", " ".join(self._cell)).strip()
            if not self._t[-1]:
                self._t[-1].append([])
            self._t[-1][-1].append(txt)
            self._cell = None
        elif tag == "table" and self._t:
            rows = [r for r in self._t.pop() if any(c for c in r)]
            self.blocks.append(("table", rows))
        elif tag in self.BLOCK and self._cell is None and not self._t:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self._title_buf.append(data)
        if self._cell is not None:
            self._cell.append(data)
        elif not self._t:
            self._buf.append(data)

    def _flush(self):
        t = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if t:
            self.blocks.append(("p", t))
        self._buf = []

    def close(self):
        super().close()
        self._flush()


def _num(s):
    s = (s or "").replace(",", "").replace(" ", "")
    m = re.fullmatch(r"(\d+)(?:메소)?", s)
    if m:
        return int(m.group(1))
    # '1억 2,345만' 같은 표기도 허용
    m = re.fullmatch(r"(?:(\d+)억)?(?:(\d+)만)?(\d+)?(?:메소)?", s)
    if m and any(m.groups()):
        return int(m.group(1) or 0) * 100000000 + int(m.group(2) or 0) * 10000 + int(m.group(3) or 0)
    return None


DATE_RE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def parse_post(html):
    """공지 HTML → {title, rows:[{boss, old, new}], notes:[...]} (가격표가 없으면 rows=[])"""
    p = _PostParser()
    p.feed(html)
    p.close()
    rows, notes, ctx = [], [], ""
    for kind, val in p.blocks:
        if kind == "p":
            ctx = val
            if re.search(r"힘의 결정|결정 판매|결정 가격|결정의 판매|결정석", val):
                if val not in notes:
                    notes.append(val)
            continue
        table = val
        if not table:
            continue
        head = " ".join(table[0])
        is_price = ("보스" in head and any(k in head for k in ("가격", "기존", "변경"))) or ("결정" in ctx and "가격" in ctx)
        if not is_price:
            continue
        body = table[1:] if not any(_num(c) for c in table[0][1:]) else table
        for r in body:
            if len(r) < 2:
                continue
            nums = [_num(c) for c in r[1:]]
            nums = [n for n in nums if n is not None]
            if not nums or not r[0]:
                continue
            rows.append({"boss": r[0], "old": nums[0] if len(nums) > 1 else None, "new": nums[-1]})
    # 적용일 메모 (예: '※ 검은 마법사의 결정 판매 가격은 2026년 10월 1일(목)부터 적용됩니다.')
    for r in rows:
        for n in notes:
            m = DATE_RE.search(n)
            name = re.sub(r"\s*\(.*\)$", "", r["boss"]).strip()
            if m and name and name in n:
                r["effective"] = "%04d-%02d-%02d" % tuple(map(int, m.groups()))
                r["effective_note"] = n
    return {"title": p.title, "rows": rows, "notes": notes}


def nexon_prices(posts=8, gather=1, pages=1):
    scanned, found = [], []
    items = []
    for page in range(1, pages + 1):
        items += parse_list(fetch(LIST_URL.format(page=page)))
    if not items:
        raise RuntimeError("업데이트 목록을 읽지 못했습니다 (페이지 구조가 바뀌었을 수 있음)")
    for it in items[:posts]:
        url = POST_URL.format(id=it["id"])
        try:
            info = parse_post(fetch(url))
        except Exception as e:  # 한 글 실패는 건너뜀
            scanned.append({"url": url, "title": it["title"], "error": str(e)[:200]})
            continue
        scanned.append({"url": url, "title": info["title"] or it["title"], "rows": len(info["rows"])})
        if info["rows"]:
            found.append({"url": url, "title": info["title"] or it["title"], "date": it["date"],
                          "rows": info["rows"], "notes": info["notes"]})
            if len(found) >= gather:
                break
    if not found:
        return {"ok": False, "error": {"name": "NO_PRICE_TABLE", "message": f"최근 업데이트 공지 {len(scanned)}개에서 결정 가격표를 찾지 못했습니다."}, "scanned": scanned}
    first = found[0]
    return {"ok": True, "source": {"url": first["url"], "title": first["title"], "date": first["date"]},
            "rows": first["rows"], "notes": first["notes"], "more": found[1:], "scanned": scanned,
            "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


# ---------------------------------------------------------------- 서버
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def cors(self):
        # file:// 로 연 index.html (Origin: null) 에서도 호출 가능. 서버는 127.0.0.1 에만 열려 있음
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "x-nxopen-api-key, accept, content-type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path)
        if path.path.startswith(PREFIX + "/maplestory/"):
            return self.proxy()
        if path.path == "/nexon-prices":
            return self.prices(urllib.parse.parse_qs(path.query))
        if path.path == "/health":
            return self.json(200, {"ok": True, "app": "maple-boss-tracker", "port": PORT})
        return super().do_GET()

    def json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def prices(self, q):
        posts = max(1, min(30, int((q.get("posts") or ["8"])[0])))
        gather = max(1, min(5, int((q.get("gather") or ["1"])[0])))
        key = (posts, gather)
        if _cache["data"] and _cache["key"] == key and time.time() - _cache["at"] < 600 and not q.get("refresh"):
            return self.json(200, _cache["data"])
        try:
            data = nexon_prices(posts=posts, gather=gather)
        except urllib.error.HTTPError as e:
            return self.json(502, {"ok": False, "error": {"name": "HTTP_%d" % e.code, "message": f"메이플스토리 홈페이지 응답 오류 (HTTP {e.code})"}})
        except Exception as e:
            return self.json(502, {"ok": False, "error": {"name": "FETCH", "message": f"메이플스토리 홈페이지에 연결하지 못했습니다: {e}"}})
        if data.get("ok"):
            _cache.update(at=time.time(), key=key, data=data)
        self.json(200 if data.get("ok") else 404, data)

    def proxy(self):
        url = UPSTREAM + self.path[len(PREFIX):]
        req = urllib.request.Request(url, headers={
            "x-nxopen-api-key": self.headers.get("x-nxopen-api-key", ""),
            "accept": "application/json", "user-agent": "maple-boss-tracker-local"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                status, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read()
        except Exception as e:
            status, body = 502, json.dumps({"error": {"name": "PROXY", "message": str(e)}}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 키가 로그에 남지 않도록 경로만 간단히 출력
        sys.stderr.write("%s\n" % (fmt % args))


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"보스 캐릭터 관리 도우미 실행 중: http://localhost:{PORT}  (종료: Ctrl+C 또는 창 닫기)")
    print("  · 더블클릭으로 연 index.html 에서 [결정석 가격 갱신]을 누르면 됩니다. 이 창은 열어 두세요.")
    if "--open" in sys.argv:
        try: webbrowser.open(f"http://localhost:{PORT}/index.html")
        except Exception: pass
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\n종료합니다.")
