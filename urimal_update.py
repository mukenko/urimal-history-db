#!/usr/bin/env python3
"""우리말역사어휘사전 정기 업데이트: 새 내려받기 파일 확인 → 신규·변경·삭제 확인 → 조회용 DB 재빌드. 표준 라이브러리만 사용.

  python3 urimal_update.py                 # 오늘 할 일 전체(매일 launchd 가 부름)
  python3 urimal_update.py --dry-run       # 할 일만 보여 줌(호출·변경 없음)
  python3 urimal_update.py --steps tail,oldest --oldest-n 50   # 일부 단계만(시험용)
  python3 urimal_update.py --force-download                    # 내려받기 파일을 날짜와 상관없이 다시 받아 가져옴
  python3 urimal_update.py --status        # 마지막 실행 결과와 로그 끝부분

이 사전의 Open API에는 '고친 날짜' 검색이 없어서 변경은 다시 받아 비교해서 찾는다. 표제어가 1만 5천 개라 돌려가며 확인해도 부담이 적다.

  download  누리집 '전체 사전 내려받기' 파일(매달 1회 생성)의 생성일이 가져온 것보다 새로우면 받아서 raw_download 에 가져온다
            (API가 256자에서 자른 설명을 채우는 원본이므로 새 달 파일이 나오면 반드시 갱신). 호출 수에 포함되지 않는다.
  new       내려받기 파일에는 있는데 API 원본에는 없는 표제어를 API로 받아 본다(API가 못 주는 표제어는 7일에 한 번만 다시 시도)
  tail      가장 큰 코드 뒤 100개를 확인(새 표제어 감지)
  pending   전날 빈 응답이 와 표시(fetch_state.note='empty@날짜')된 코드를 다시 받아 삭제 확인(또 비면 확정, 내용이 돌아오면 표시 지움)
  oldest    가장 오래 확인하지 않은 표제어를 N개(기본 3000) 다시 받아 비교(수정·삭제 감지; 약 5일에 전체가 한 바퀴)
  build     바뀐 것이 있을 때만 urimal_build.py 로 조회용 DB 재빌드(검증을 통과해야 교체)

새 표제어가 생기거나 항목의 표제어·어깨번호가 바뀌면 같은 표제어의 다른 항목을 같은 실행에서 바로 다시 받는다(사전이 동형이의어 번호를 밀면
옛 번호의 항목이 며칠 남아 빌드 검증 '(표제어, 어깨번호) 중복'이 실패하기 때문). 그래도 빌드가 중복으로 실패하면 빌드가 알려 준 코드만 다시 받고 한 번만 다시 빌드한다.

삭제는 **다른 날** 두 번 빈 응답이 올 때만 확정하고(같은 날에는 표시만; 일시적인 빈 응답이 같은 순간에 되풀이될 수 있다), 원본은 raw_removed 표에 남긴다. 내려받기 파일에도 있는 표제어는 계속 조회용 DB에 들어간다.
하루 API 호출 상한 20,000건(--limit; 하루 한도 50,000건 중 수동 작업 여유). 응답의 lastBuildDate 는 호출마다 달라서 <item> 부분만 비교한다.

폴더 동기화 스크립트(rsync)가 DB를 쓰는 중에 불완전한 사본을 보내지 않도록 같은 잠금 폴더를 잡는다
(기본 /tmp/rsync_to_rpi.lock, 환경변수 URIMAL_UPDATE_LOCK; 안 쓰면 --no-lock).

로그: data/update.log (줄마다 [날짜 시간], 실행할 때마다 15일 지난 줄을 지운다). 인증키·요청 주소는 출력하지 않는다.
새 표제어·삭제 확정이 있으면 macOS 알림을 띄운다.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from pathlib import Path

import urimal_fetch as uf
import urimal_import as ui

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "data" / "update.log"
LAUNCHD_LOG = ROOT / "data" / "update_launchd.log"
DOWNLOAD_DIR = ROOT / "data" / "download"
RSYNC_LOCK = Path(os.environ.get("URIMAL_UPDATE_LOCK") or "/tmp/rsync_to_rpi.lock")
INFO_URL = f"{uf.BASE_URL}/common/download/fullDownloadInfo.json"
DOWNLOAD_URL = f"{uf.BASE_URL}/common/download.do"
LOG_KEEP_DAYS = 15                         # projects/CLAUDE.md 작업 원칙 1
CALL_LIMIT = 20000
OLDEST_PER_DAY = 3000
TAIL_PROBE = 100
RETRY_NEW_DAYS = 7
KEEP_ZIPS = 2
ALL_STEPS = ("download", "new", "tail", "pending", "oldest", "build")


# ---------------------------------------------------------------- 로그
def stamp():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{stamp()}] {msg}\n")
    if sys.stdout.isatty():
        print(msg, flush=True)


def trim_log(path, days=LOG_KEEP_DAYS, now=None):
    """[YYYY-MM-DD HH:MM:SS] 로 시작하는 줄 중 days 일 지난 것을 지운다. 시각 없는 줄은 그 앞 줄과 함께 취급한다."""
    path = Path(path)
    if not path.exists():
        return
    cutoff = ((now or dt.datetime.now()) - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    keep, keeping = [], True
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True):
        m = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
        if m:
            keeping = m.group(1) >= cutoff
        if keeping:
            keep.append(line)
    path.write_text("".join(keep), encoding="utf-8")


def trim_by_mtime(path, days=LOG_KEEP_DAYS):
    """launchd 가 직접 받는 로그(줄마다 시간 없음): 마지막 수정 후 days 일이 지났으면 비운다."""
    path = Path(path)
    if path.exists() and time.time() - path.stat().st_mtime > days * 86400:
        path.write_text("")


def notify(title, text):
    try:
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}"'],
                       check=False, timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- 잠금
class AlreadyRunning(Exception):
    pass


class LockBusy(Exception):
    pass


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_rsync_lock(wait_s=1800, poll_s=20):
    lock = RSYNC_LOCK
    deadline = time.time() + wait_s
    while True:
        try:
            lock.mkdir()
            (lock / "owner").write_text("urimal_update\n")
            (lock / "pid").write_text(f"{os.getpid()}\n")
            return
        except FileExistsError:
            pass
        try:
            pid = int((lock / "pid").read_text().strip())
        except (OSError, ValueError):
            pid = None
        if pid is None or not _alive(pid):               # 비정상 종료로 남은 잠금
            for f in ("pid", "owner"):
                try:
                    (lock / f).unlink()
                except OSError:
                    pass
            try:
                lock.rmdir()
            except OSError:
                pass
            continue
        if time.time() >= deadline:
            raise LockBusy()
        time.sleep(poll_s)


def release_rsync_lock():
    for f in ("pid", "owner"):
        try:
            (RSYNC_LOCK / f).unlink()
        except OSError:
            pass
    try:
        RSYNC_LOCK.rmdir()
    except OSError:
        pass


# ---------------------------------------------------------------- DB
def open_db():
    db = uf.open_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS changes(      -- 업데이트가 찾은 변경 기록
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL, target_code INTEGER NOT NULL,
            kind TEXT NOT NULL,                  -- added | changed | removed
            form TEXT);
        CREATE TABLE IF NOT EXISTS raw_removed(  -- 삭제 확정된 원본(되돌릴 수 있게 남김)
            target_code INTEGER PRIMARY KEY, removed_at TEXT NOT NULL, xml BLOB NOT NULL);
        """)
    return db


_ITEM = re.compile(r"<item>.*</item>", re.S)


def item_text(xml):
    """lastBuildDate 처럼 호출마다 달라지는 바깥 부분을 빼고 <item> 부분만."""
    m = _ITEM.search(xml)
    return m.group(0) if m else xml


def form_of(xml):
    m = re.search(r"<form>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</form>", xml)
    return m.group(1) if m else None


def number_of(xml):
    m = re.search(r"<word_number>(?:<!\[CDATA\[)?\s*(\d+)", xml)
    return int(m.group(1)) if m else 1


def record(db, code, kind, xml):
    db.execute("INSERT INTO changes(at, target_code, kind, form) VALUES(?,?,?,?)",
               (uf.now(), code, kind, form_of(xml)))


# ---------------------------------------------------------------- 단계: download
def http_get(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "urimal-history-db/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_download_info():
    info = json.loads(http_get(INFO_URL).decode("utf-8"))
    x = info.get("xml")
    if not x or "linkKey" not in x or "createdTime" not in x:
        raise ValueError("전체 내려받기 정보에 xml 항목이 없습니다")
    return int(x["linkKey"]), str(x["createdTime"])


def imported_created(db):
    try:
        row = db.execute("SELECT MAX(created) FROM raw_download").fetchone()
    except Exception:  # noqa: BLE001  (표가 아직 없음)
        return None
    return row[0] if row and row[0] else None


def step_download(db, force=False, dry=False):
    """새 내려받기 파일이 있으면 받아 가져온다. 바뀌었으면 True."""
    have = imported_created(db)
    if dry:
        log(f"download: 가져온 파일 생성일 {have or '없음'} (실제 실행 때 누리집의 최신 생성일과 비교)")
        return False
    link, created_time = latest_download_info()
    created = created_time[:8]
    if not force and have and created <= have:
        log(f"download: 새 파일 없음 (가져온 것 {have}, 누리집 {created})")
        return False
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    part = DOWNLOAD_DIR / f"full_{created}.zip.part"
    final = DOWNLOAD_DIR / f"full_{created}.zip"
    data = http_get(DOWNLOAD_URL, data=f"linkKey={link}".encode(), timeout=600)
    part.write_bytes(data)
    try:
        if not zipfile.is_zipfile(part):
            raise ValueError("내려받은 파일이 zip 이 아닙니다")
        root = ui.load_root(part)
        n_old = db.execute("SELECT COUNT(*) FROM raw_download").fetchone()[0] if have else 0
        n_new = len(root.findall("item"))
        if n_old and n_new < n_old * 0.9:
            raise ValueError(f"항목 수가 크게 줄었습니다 ({n_old} → {n_new}); 가져오지 않고 멈춥니다")
        n = ui.import_root(db, root, final.name, created)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    part.replace(final)
    for old in sorted(DOWNLOAD_DIR.glob("full_*.zip"))[:-KEEP_ZIPS]:        # 오래된 파일 정리
        old.unlink(missing_ok=True)
    log(f"download: {final.name} ({len(data) // 1024}KB) 가져옴, 표제어 {n}개")
    return True


# ---------------------------------------------------------------- API 확인
def refresh_code(client, code, info=None):
    """한 코드를 다시 받아 저장된 것과 비교한다. 반환: added | changed | same | empty
    info(dict)를 주면 info['old'], info['new'] 에 (표제어, 어깨번호)를 담는다(동형이의어 번호가 밀렸는지 보려고)."""
    db = client.db
    text = client.call("/api/view", {"method": "target_code", "q": str(code), "req_type": "xml"})
    root = uf.parse_xml(text)
    uf.check_error(root)
    total = int((root.findtext("total") or "0").strip() or 0)
    item = root.find("item")
    if total == 0 or item is None:
        return "empty"
    got = (item.findtext("target_code") or "").strip()
    if got != str(code):
        raise uf.ApiError(f"요청 코드와 응답 코드가 다릅니다 ({code} != {got})")
    row = db.execute("SELECT xml FROM raw_xml WHERE target_code=?", (code,)).fetchone()
    blob = zlib.compress(text.encode("utf-8"), 6)
    if info is not None:
        info["new"] = (form_of(text), number_of(text))
    if row is None:
        db.execute("INSERT INTO raw_xml(target_code, fetched_at, xml) VALUES(?,?,?)", (code, uf.now(), blob))
        record(db, code, "added", text)
        result = "added"
    else:
        old = zlib.decompress(row[0]).decode("utf-8")
        if info is not None:
            info["old"] = (form_of(old), number_of(old))
        if item_text(old) != item_text(text):
            db.execute("UPDATE raw_xml SET fetched_at=?, xml=? WHERE target_code=?", (uf.now(), blob, code))
            record(db, code, "changed", text)
            result = "changed"
        else:
            result = "same"
    uf.set_state(db, code, "ok", None)
    return result


def remove_code(db, code):
    """삭제 확정: 원본을 raw_removed 로 옮겨 되돌릴 수 있게 남긴다."""
    row = db.execute("SELECT xml FROM raw_xml WHERE target_code=?", (code,)).fetchone()
    if row:
        xml = zlib.decompress(row[0]).decode("utf-8")
        db.execute("INSERT OR REPLACE INTO raw_removed VALUES(?,?,?)", (code, uf.now(), row[0]))
        db.execute("DELETE FROM raw_xml WHERE target_code=?", (code,))
        record(db, code, "removed", xml)
    uf.set_state(db, code, "gone", None)


def handle_empty(db, code):
    """이전에 있던 코드가 비어 왔다. 일시적인 빈 응답일 수 있어(같은 순간에 되풀이되기도 한다) 같은 날에는 확정하지 않고
    fetch_state.note 에 'empty@날짜' 로 표시만 한다. **다른 날** 또 비어 오면 삭제로 확정한다. 반환: pending | removed"""
    row = db.execute("SELECT note FROM fetch_state WHERE target_code=?", (code,)).fetchone()
    note = (row[0] or "") if row else ""
    if note.startswith("empty@"):
        if note[len("empty@"):] < uf.today():
            remove_code(db, code)
            return "removed"
        return "pending"                                       # 오늘 이미 표시함: 내일 이후에 다시 확인
    uf.set_state(db, code, "ok", f"empty@{uf.today()}")
    return "pending"


def regroup_forms(result, info):
    """다시 받은 결과가 같은 표제어 항목들의 번호를 밀리게 했을 수 있으면 그 표제어들을 돌려준다(새 항목, 표제어·번호가 바뀐 항목)."""
    new, old = info.get("new"), info.get("old")
    if result == "added" and new and new[0]:
        return {new[0]}
    if result == "changed" and new and old and new != old:
        return {f for f in (new[0], old[0]) if f}
    return set()


def run_refresh(client, codes, label, counts, previously_ok=True, done=None, regroup=None):
    """codes 를 차례로 다시 받는다. 한도·치명 오류에서 멈추고 False 를 돌려준다.
    done: 이번 실행에서 이미 받은 코드(중복 호출 방지), regroup: 같은 표제어 항목을 다시 받아야 하는 표제어를 모은다."""
    consecutive_err = 0
    for code in codes:
        if done is not None:
            if code in done:
                continue
            done.add(code)
        try:
            info = {}
            r = refresh_code(client, code, info)
            if r == "empty":
                if previously_ok:
                    r = handle_empty(client.db, code)
                else:
                    uf.set_state(client.db, code, "empty", None)
            elif regroup is not None:
                regroup |= regroup_forms(r, info)
            consecutive_err = 0
            counts[r] += 1
        except uf.LimitReached:
            log(f"{label}: 오늘 호출 한도({client.limit})에 닿아 멈춥니다.")
            return False
        except uf.FatalApiError as e:
            log(f"{label}: 치명 오류로 멈춥니다 - {e}")
            return False
        except uf.ApiError as e:
            counts["error"] += 1
            consecutive_err += 1
            uf.set_state(client.db, code, "error", str(e)[:200])
            if consecutive_err >= uf.MAX_CONSECUTIVE_ERRORS:
                log(f"{label}: 연속 오류 {uf.MAX_CONSECUTIVE_ERRORS}회, 멈춥니다 - {e}")
                return False
        client.db.commit()
    return True


def codes_with_form(db, forms):
    """raw_xml 에서 표제어가 forms 에 드는 코드를 {표제어: [코드]} 로. 드물게(번호가 밀릴 때만) 불리므로 한 번 훑는다."""
    out = {f: [] for f in forms}
    for code, blob in db.execute("SELECT target_code, xml FROM raw_xml ORDER BY target_code"):
        f = form_of(zlib.decompress(blob).decode("utf-8"))
        if f in out:
            out[f].append(code)
    return out


def refresh_groups(client, forms, done, counts):
    """번호가 밀렸을 수 있는 표제어의 항목들을 이번 실행에서 아직 안 받은 것만 다시 받는다(최대 10개 안팎의 그룹)."""
    if not forms:
        return True
    ok = True
    for form, codes in sorted(codes_with_form(client.db, forms).items()):
        todo = [c for c in codes if c not in done]
        if todo:
            log(f"group: 표제어 '{form}' 의 동형이의어 {len(todo)}개를 다시 확인합니다 (번호가 밀렸을 수 있음)")
            ok = run_refresh(client, todo, "group", counts, previously_ok=True, done=done) and ok
    return ok


def refresh_group_steps(client, codes, counts):
    """codes 를 다시 받고, 번호가 밀렸을 수 있으면 같은 표제어 항목도 받는다(단계 하나를 통째로 시험할 때 쓰는 묶음)."""
    done, regroup = set(), set()
    ok = run_refresh(client, codes, "t", counts, previously_ok=True, done=done, regroup=regroup)
    return refresh_groups(client, regroup, done, counts) if ok else False


def duplicate_codes(text):
    """빌드 실패 메시지의 '중복 코드: 1,2,3' 에서 코드를 꺼낸다."""
    m = re.search(r"중복 코드:\s*([\d,\s]+)", text or "")
    return [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []


def run_build():
    r = subprocess.run([sys.executable, str(ROOT / "urimal_build.py")], capture_output=True, text=True)
    return r.returncode, (r.stderr or "") + (r.stdout or "")


def build_with_retry(client, build_fn, max_codes=60):
    """빌드한다. (표제어, 어깨번호) 중복으로 실패하면 그 코드들만 다시 받고 **한 번만** 다시 빌드한다."""
    rc, out = build_fn()
    if rc != 0:
        codes = duplicate_codes(out)
        if codes:
            log(f"build: 어깨번호 중복으로 실패 - 해당 코드 {len(codes[:max_codes])}개를 다시 받고 한 번 더 시도합니다")
            counts = Counter()
            run_refresh(client, codes[:max_codes], "build-retry", counts, previously_ok=True, done=set())
            log("build-retry: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "처리한 코드 없음"))
            rc, out = build_fn()
    if rc == 0:
        log("build: 조회용 DB 재빌드 완료")
        return True
    lines = out.strip().splitlines()
    log("build: 실패(기존 DB는 그대로) - " + (lines[-1][:200] if lines else "원인 불명"))
    return False


def codes_new(db):
    """내려받기 파일에는 있고 API 원본에는 없는 코드(API가 못 주는 것은 일주일에 한 번만 다시 시도)."""
    cutoff = (dt.datetime.now() - dt.timedelta(days=RETRY_NEW_DAYS)).isoformat(timespec="seconds")
    try:
        rows = db.execute(
            "SELECT d.target_code FROM raw_download d LEFT JOIN raw_xml x USING(target_code) "
            "LEFT JOIN fetch_state s USING(target_code) "
            "WHERE x.target_code IS NULL AND (s.fetched_at IS NULL OR s.fetched_at < ?) ORDER BY 1", (cutoff,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [r[0] for r in rows]


def codes_pending(db):
    """이전 실행에서 빈 응답이 와 삭제 확인을 기다리는 코드(오늘 표시한 것은 내일 이후에 본다)."""
    return [r[0] for r in db.execute(
        "SELECT s.target_code FROM fetch_state s JOIN raw_xml x USING(target_code) "
        "WHERE s.note LIKE 'empty@%' AND substr(s.note, 7) < ? ORDER BY 1", (uf.today(),))]


def codes_tail(db):
    mx = max([r[0] or 0 for r in (db.execute("SELECT MAX(target_code) FROM raw_xml").fetchone(),
                                   db.execute("SELECT MAX(target_code) FROM raw_download").fetchone()
                                   if imported_created(db) else (0,))])
    return list(range(mx + 1, mx + 1 + TAIL_PROBE))


def codes_oldest(db, n):
    return [r[0] for r in db.execute(
        "SELECT s.target_code FROM fetch_state s JOIN raw_xml x USING(target_code) WHERE s.status='ok' "
        "ORDER BY s.fetched_at, s.target_code LIMIT ?", (n,))]


# ---------------------------------------------------------------- 실행
def run(args):
    steps = [s for s in (args.steps.split(",") if args.steps else ALL_STEPS) if s]
    bad = [s for s in steps if s not in ALL_STEPS]
    if bad:
        sys.exit(f"알 수 없는 단계: {', '.join(bad)} (가능: {', '.join(ALL_STEPS)})")
    db = open_db()
    client = uf.Client(uf.load_key(), db, args.limit, args.sleep)
    counts_total = {}
    changed = False

    if args.dry_run:
        log(f"[dry-run] 단계: {', '.join(steps)} / 오늘 호출 {client.used_today()}/{client.limit}")
        if "download" in steps:
            step_download(db, dry=True)
        if "new" in steps:
            log(f"[dry-run] new: {len(codes_new(db))}개 확인 대상")
        if "tail" in steps:
            t = codes_tail(db)
            log(f"[dry-run] tail: 코드 {t[0]}~{t[-1]} ({len(t)}개)")
        if "pending" in steps:
            log(f"[dry-run] pending: 삭제 확인 대기 {len(codes_pending(db))}개")
        if "oldest" in steps:
            log(f"[dry-run] oldest: 가장 오래 확인하지 않은 {len(codes_oldest(db, args.oldest_n))}개")
        return

    log(f"시작: 단계 {', '.join(steps)} / 오늘 호출 {client.used_today()}/{client.limit}")
    if "download" in steps:
        try:
            changed |= step_download(db, force=args.force_download)
        except (urllib.error.URLError, OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as e:
            log(f"download: 실패 ({type(e).__name__}: {str(e)[:150]}) - 다음 실행에서 다시 시도합니다.")
    ok = True
    done, regroup = set(), set()                  # 이번 실행에서 받은 코드 / 번호가 밀렸을 수 있는 표제어
    counts_group = Counter()
    for name, getter, prev_ok in (("new", lambda: codes_new(db), False), ("tail", lambda: codes_tail(db), False),
                                  ("pending", lambda: codes_pending(db), True),
                                  ("oldest", lambda: codes_oldest(db, args.oldest_n), True)):
        if name not in steps or not ok:
            continue
        counts = Counter()
        ok = run_refresh(client, getter(), name, counts, previously_ok=prev_ok, done=done, regroup=regroup)
        db.commit()
        log(f"{name}: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "처리한 코드 없음"))
        for k, v in counts.items():
            counts_total[k] = counts_total.get(k, 0) + v
    if ok and regroup:
        ok = refresh_groups(client, regroup, done, counts_group)
        db.commit()
        log("group: " + (", ".join(f"{k} {v}" for k, v in sorted(counts_group.items())) or "처리한 코드 없음"))
        for k, v in counts_group.items():
            counts_total[k] = counts_total.get(k, 0) + v
    n_added, n_changed, n_removed = (counts_total.get(k, 0) for k in ("added", "changed", "removed"))
    changed |= bool(n_added or n_changed or n_removed)

    if n_added or n_removed:
        notify("우리말역사어휘사전", f"새 표제어 {n_added}개, 삭제 {n_removed}개")
    if "build" in steps:
        if changed or args.force_build:
            build_with_retry(client, run_build)
        else:
            log("build: 바뀐 것이 없어 건너뜀")
    log(f"끝: 오늘 호출 {client.used_today()}/{client.limit}")


def status():
    db = open_db()
    print(f"가져온 내려받기 파일 생성일: {imported_created(db) or '없음'}")
    for row in db.execute("SELECT status, COUNT(*) FROM fetch_state GROUP BY status"):
        print(f"  {row[0]:<6} {row[1]}")
    marked = db.execute("SELECT COUNT(*) FROM fetch_state WHERE note LIKE 'empty@%'").fetchone()[0]
    print(f"  삭제 확인 대기(빈 응답이 한 번 온 코드): {marked}개 (그중 오늘 확인 대상 {len(codes_pending(db))}개)")
    print("최근 변경 기록:")
    for r in db.execute("SELECT at, kind, target_code, form FROM changes ORDER BY id DESC LIMIT 10"):
        print(f"  {r[0]} {r[1]:<8} #{r[2]} {r[3] or ''}")
    if LOG_PATH.exists():
        print("로그 끝부분:")
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines()[-12:]:
            print("  " + line)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--steps", help=f"쉼표로 구분한 단계 ({','.join(ALL_STEPS)})")
    ap.add_argument("--oldest-n", type=int, default=OLDEST_PER_DAY)
    ap.add_argument("--limit", type=int, default=CALL_LIMIT, help="하루 API 호출 상한 (기본 %(default)s)")
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--force-build", action="store_true")
    ap.add_argument("--no-lock", action="store_true", help="rsync 잠금 폴더를 잡지 않는다")
    args = ap.parse_args()
    if args.status:
        return status()
    if not args.dry_run:
        trim_log(LOG_PATH)
        trim_by_mtime(LAUNCHD_LOG)
    own = (ROOT / "data").joinpath(".update.lock")
    own.parent.mkdir(parents=True, exist_ok=True)
    fh = open(own, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("이미 다른 업데이트가 실행 중입니다.")
    locked = False
    try:
        if not (args.dry_run or args.no_lock):
            try:
                acquire_rsync_lock()
                locked = True
            except LockBusy:
                log("동기화 작업이 오래 끝나지 않아 이번 실행은 건너뜁니다.")
                return
        run(args)
    finally:
        if locked:
            release_rsync_lock()
        fh.close()


if __name__ == "__main__":
    main()
