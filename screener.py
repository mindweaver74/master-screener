# -*- coding: utf-8 -*-
"""Stock screener built from the screening prompt (KOSPI/KOSDAQ common stocks).
Run:  py screener.py [--cap-min 1000] [--rank-count 400] [--show 10]
Output: docs/index.html + docs/screen.json (GitHub Pages) and result/screen_result.csv
Steps: trading-value ranking -> daily candles + quote -> required conditions 1-7 -> score -> HTML/CSV.
Values come only from API responses; whatever could not be fetched is written as 'unconfirmed'. No buy/sell wording."""
import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime

import kb_api as kb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "result")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
EOK = 1e8

MIN_VALUE = 300 * EOK        # condition 1
MIN_PRICE = 1000             # condition 4
MULT_MIN = 2.0               # condition 6
POS_MIN = 0.7                # condition 7
WINDOW = 20
BARS = 260                   # candles requested (52-week high needs about 250)
EXCLUDE_IS_ST = {"01": "관리종목", "02": "투자위험", "03": "투자경고", "04": "투자주의", "09": "거래정지"}   # codes confirmed by the KB API owner
RESTRICT_KEYWORDS = ["정지", "정리", "관리", "경고", "위험", "주의", "환기", "불성실", "이상급등"]
ETF_PREFIX = ("KODEX", "TIGER", "KOSEF", "HANARO", "ARIRANG", "KBSTAR", "ACE", "SOL", "RISE", "PLUS", "TIMEFOLIO")


# ---------------------------------------------------------------- helpers
def to_float(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "+"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def rows(body, prefer=None):
    if not isinstance(body, dict):
        return []
    if prefer and isinstance(body.get(prefer), list):
        return [r for r in body[prefer] if isinstance(r, dict)]
    lists = [v for v in body.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
    return max(lists, key=len) if lists else []


def pow10_scale(ratios):
    ratios = [x for x in ratios if x and x > 0]
    if not ratios:
        return 1.0
    return 10.0 ** round(math.log10(statistics.median(ratios)))


def isin_kr(code6):
    base = "KR7" + code6 + "00"
    digits = "".join(str(int(c, 36)) for c in base)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
        total += d // 10 + d % 10
    return base + str((10 - total % 10) % 10)


def is_etf_etn_name(name):
    n = name.upper()
    return n.startswith(ETF_PREFIX) or "ETN" in n


def is_spac_name(name):
    return "스팩" in name or "기업인수목적" in name


def is_preferred(code, name):
    """Heuristic: the short code of an ordinary stock ends in '0'; names ending in '우', '우B', '우(전환)' are preferred shares."""
    return (code[-1:] != "0") or bool(re.search(r"우[A-C]?(\(전환\))?$", name.strip()))


def market_of(s):
    s = str(s or "").upper()
    if "KOSDAQ" in s or "코스닥" in s:
        return "KOSDAQ"
    if "KOSPI" in s or "코스피" in s or "유가증권" in s:
        return "KOSPI"
    return None


def load_exclusions(path):
    out = {}
    if not os.path.exists(path):
        return out
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.lower().startswith("code"):
            continue
        parts = [p.strip() for p in re.split(r"[,\t]", line)]
        digits = re.sub(r"\D", "", parts[0])
        out[digits.zfill(6) if digits else parts[0]] = parts[2] if len(parts) > 2 and parts[2] else "제외 목록"
    return out


def parse_candles(cs):
    out, seen = [], set()
    for c in cs:
        d = str(c.get("dt", "")).strip()
        if not re.fullmatch(r"\d{8}", d) or d in seen:
            continue
        try:
            datetime.strptime(d, "%Y%m%d")
        except ValueError:
            continue
        b = {"dt": d, "vlm": to_float(c.get("vlm")), "value": to_float(c.get("dl_tw_amt")), "close": to_float(c.get("cls_prc_p2")),
             "open": to_float(c.get("opn_prc_p2")), "high": to_float(c.get("hgh_prc_p2")), "low": to_float(c.get("lw_prc_p2"))}
        if None in (b["vlm"], b["value"], b["close"], b["open"], b["high"], b["low"]):
            continue
        seen.add(d)
        out.append(b)
    out.sort(key=lambda x: x["dt"])
    return out


# ---------------------------------------------------------------- metrics and score
def compute(bars, price, scale, shares):
    """bars: ascending daily candles; the last one is the base day. price: current price (or the close)."""
    b = bars[-1]
    hi, lo, op = max(b["high"], price), min(b["low"], price), b["open"]
    rng = hi - lo
    pos = 1.0 if rng <= 0 else (price - lo) / rng
    wick = 0.0 if rng <= 0 else (hi - max(op, price)) / rng
    prior = bars[-1 - WINDOW:-1]
    avg_v = statistics.mean(x["value"] for x in prior) * scale
    med_v = statistics.median(x["value"] for x in prior) * scale
    avg_q = statistics.mean(x["vlm"] for x in prior)
    value = b["value"] * scale
    prev_close = bars[-2]["close"]
    closes = [x["close"] for x in bars]
    ma20 = (sum(closes[-20:-1]) + price) / 20.0         # previous 19 closes + current price
    ret5 = (price / closes[-6] - 1.0) * 100.0 if len(closes) >= 6 and closes[-6] else None
    cap = price * shares if shares else None
    m = {"price": price, "open": op, "high": hi, "low": lo, "chg": (price / prev_close - 1.0) * 100.0 if prev_close else None,
         "value": value, "avg_value": avg_v, "mult": value / avg_v if avg_v else None, "pos": pos, "wick": wick,
         "cap": cap, "turn": (value / cap * 100.0) if cap else None, "ma20": ma20, "above_ma20": price > ma20, "ret5": ret5,
         "bull": price > op, "drawdown": (hi - price) / hi * 100.0 if hi else None, "vol_mult": (b["vlm"] / avg_q) if avg_q else None,
         "med_mult": value / med_v if med_v else None, "near_threshold": None}
    if m["mult"] is not None and MULT_MIN <= m["mult"] <= MULT_MIN * 1.05:
        m["near_threshold"] = True
    m["price_above_known_high"] = price > b["high"] * 1.000001      # the price is higher than the day's high in the candle (timing gap or bad data)
    if len(bars) >= 250:
        h52 = max(max(x["high"] for x in bars[-250:]), hi)
        m["h52"] = h52
        m["from_h52"] = (price / h52 - 1.0) * 100.0
    else:
        m["h52"], m["from_h52"] = None, None
    return m


def lin(x, x0, y0, x1, y1):
    if x <= x0:
        return y0
    if x >= x1:
        return y1
    return y0 + (x - x0) / (x1 - x0) * (y1 - y0)


def score(m):
    s = {}
    s["mult"] = lin(m["mult"], 2.0, 10.0, 10.0, 25.0)
    p = lin(m["pos"], 0.7, 10.0, 1.0, 20.0)
    if m["wick"] > 0.30:
        p = max(0.0, p - 5.0)
    s["pos"] = p
    c = m["chg"]
    if c is None:
        s["chg"] = 0.0
    elif c > 6.0:
        s["chg"] = max(0.0, 20.0 - 2.0 * (c - 6.0))
    elif c >= 1.0:
        s["chg"] = 20.0
    elif c >= 0.5:
        s["chg"] = 13.0
    elif c >= 0.0:
        s["chg"] = 7.0
    else:
        s["chg"] = 0.0
    t = m["turn"]
    if t is None:
        s["turn"] = 0.0
    else:
        v = lin(t, 1.0, 0.0, 5.0, 15.0)
        if t > 30.0:
            v = max(0.0, v - 10.0)
        s["turn"] = v
    s["trend"] = (10.0 if m["above_ma20"] else 0.0) + (10.0 if (m["ret5"] is not None and m["ret5"] <= 20.0) else 0.0)
    s["total"] = sum(s[k] for k in ("mult", "pos", "chg", "turn", "trend"))
    return {k: round(v, 1) for k, v in s.items()}


MAXPT = {"mult": 25, "pos": 20, "chg": 20, "turn": 15, "trend": 20}
LABEL = {"mult": "거래대금 배수", "pos": "종가 위치", "chg": "등락률", "turn": "회전율", "trend": "추세 위치"}


def weakest(sc):
    k = min(MAXPT, key=lambda x: sc[x] / MAXPT[x])
    return "%s %.1f/%d점" % (LABEL[k], sc[k], MAXPT[k])


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap-min", type=float, default=1000.0, help="market cap minimum in eok won (adjustable 500-3000)")
    ap.add_argument("--rank-count", type=int, default=400)
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--now", default="", help="testing only: YYYYMMDDHHMM")
    a = ap.parse_args()
    cap_min = a.cap_min * EOK
    now = datetime.strptime(a.now, "%Y%m%d%H%M") if a.now else datetime.now()
    notes = []
    try:
        srv = kb.body_of(kb.call("szqm0771", {})).get("now_dt")
    except kb.ApiError as e:
        print("ERROR:", e)
        return 1
    today = a.now[:8] if a.now else (str(srv or "").strip() or now.strftime("%Y%m%d"))
    time.sleep(kb.CALL_INTERVAL)
    print("Fetching trading-value ranking (%d rows) ..." % a.rank_count)
    rk = rows(kb.body_of(kb.call("ivu10210", {"inq_cnt": str(a.rank_count)})), "out2")
    time.sleep(kb.CALL_INTERVAL)
    ranking = []
    for x in rk:
        code = str(x.get("is_cd", "")).strip()
        if code:
            ranking.append({"code": code, "name": str(x.get("is_nm", "")).strip(), "is_st": str(x.get("is_st", "")).strip(),
                            "value": to_float(x.get("dl_tw_amt")), "chg": to_float(x.get("up_dwn_r_p2")), "ccd": str(x.get("bdy_cmpr_ccd", "")).strip()})
    if not ranking:
        print("ERROR: the ranking is empty.")
        return 1
    if ranking[-1]["value"] is not None and ranking[-1]["value"] >= MIN_VALUE:
        notes.append("순위 조회 마지막 행의 거래대금이 아직 300억 이상입니다. --rank-count를 늘려 다시 실행하세요.")
    excl = load_exclusions(os.path.join(BASE_DIR, "exclusions.csv"))

    funnel = [("순위 API 조회 종목", len(ranking))]
    pre = [r for r in ranking if r["value"] is not None and r["value"] >= MIN_VALUE]
    funnel.append(("순위 API 거래대금 300억 이상(1차, 대체거래소 합산 값)", len(pre)))
    pre = [r for r in pre if not is_etf_etn_name(r["name"])]
    funnel.append(("대상: ETF·ETN 이름 제외", len(pre)))
    print("  %d stocks go to the detail step" % len(pre))

    # candles for everyone first (condition 1 needs the KRX daily value)
    data = {}
    fails = 0
    for i, r in enumerate(pre, 1):
        code = r["code"]
        try:
            chart = None
            for m in ("0", "1"):
                cs = rows(kb.body_of(kb.call("ivs11560", {"info_ccd": "2", "mkt_clsf": m, "chrt_clsf": "D", "is_cd": code,
                                                         "inq_clsf": "2", "inq_cnt": str(BARS)})), "out2")
                time.sleep(kb.CALL_INTERVAL)
                if cs:
                    chart = parse_candles(cs)
                    break
            data[code] = {"bars": chart or []}
            fails = 0
        except kb.ApiError as e:
            fails += 1
            data[code] = {"bars": [], "error": str(e)}
            print("  %s candles failed: %s" % (code, e))
            if fails >= 5:
                print("ERROR: 5 consecutive failures. Check the connection / CALL_INTERVAL.")
                return 1
        if i % 20 == 0:
            print("  candles %d/%d" % (i, len(pre)), flush=True)
    ratios = [b["vlm"] * b["close"] / b["value"] for d in data.values() for b in d["bars"][-5:] if b["value"] and b["vlm"] and b["close"]]
    scale = pow10_scale(ratios)
    last_dts = [d["bars"][-1]["dt"] for d in data.values() if d["bars"]]
    if not last_dts:
        print("ERROR: no candles were received.")
        return 1
    base_dt = max(last_dts)
    hhmm = now.hour * 100 + now.minute
    if base_dt == today and hhmm < 1540:
        phase = "intraday"
    elif base_dt == today:
        phase = "final"
    else:
        phase = "prev"
        if now.weekday() < 5 and 900 <= hhmm < 1540:
            notes.append("오늘(%s) 봉이 아직 없어 직전 거래일(%s) 기준으로 계산했습니다." % (today, base_dt))

    # stage evaluation, stop at the first failing stage
    stages = ["1. 거래대금(KRX 일봉) 300억 이상", "시장 확인: 코스피·코스닥 (코넥스·OTC 제외)", "2. 시가총액 %s억 이상" % ("{:,.0f}".format(a.cap_min)),
              "3. 최근 거래일 20일 이상(신규상장 제외)", "4. 주가 1,000원 이상", "5. 제외 종목(관리·거래정지·시장경보·스팩·우선주 등)",
              "6. 거래대금 20일 평균의 2배 이상", "7. 종가 위치 0.7 이상"]
    cnt = [0] * len(stages)
    pool = []                      # stocks that passed stages 1-5 (for relaxation)
    final = []
    unconfirmed = {"cap": 0, "restriction": 0, "h52": 0}
    clamp = 0
    for i, r in enumerate(pre, 1):
        code, name = r["code"], r["name"]
        bars = [b for b in data[code]["bars"] if b["dt"] <= today]
        if not bars or bars[-1]["dt"] != base_dt or bars[-1]["value"] * scale < MIN_VALUE:
            continue
        cnt[0] += 1
        try:
            q = kb.body_of(kb.call("ivu10140", {"excg_clsf": kb.EXCG_CLSF, "shrt_cd": code}))
        except kb.ApiError as e:
            print("  quote failed %s: %s" % (code, e))
            continue
        time.sleep(kb.CALL_INTERVAL)
        mk = market_of(q.get("mkt_clsf_nm"))
        if mk is None:
            continue
        cnt[1] += 1
        shares = to_float(q.get("lstng_stk_q"))
        live = to_float(q.get("now_prc"))
        price = live if (phase == "intraday" and live) else bars[-1]["close"]
        if not shares:
            unconfirmed["cap"] += 1
            continue
        if price * shares < cap_min:
            continue
        cnt[2] += 1
        if len(bars) < WINDOW + 1:
            continue
        cnt[3] += 1
        if price < MIN_PRICE:
            continue
        cnt[4] += 1
        # stage 5: exclusions (cheap checks first, then the per-stock restriction query)
        why = None
        if r["is_st"] in EXCLUDE_IS_ST:
            why = EXCLUDE_IS_ST[r["is_st"]]
        elif is_spac_name(name):
            why = "스팩"
        elif is_preferred(code, name):
            why = "우선주(코드·이름 규칙)"
        elif code in excl:
            why = "제외 목록: " + excl[code]
        status = ""
        if why is None:
            try:
                b4 = kb.body_of(kb.call("siqm4900", {"stnd_is_cd": isin_kr(code)}))
                time.sleep(kb.CALL_INTERVAL)
                status = " ".join(str(b4.get(k, "")).strip() for k in ("trd_rstn_clsf_nm", "is_rgst_clsf_nm", "is_typ_nm", "is_dtl_typ_nm")) + " " + str(q.get("etc_info", "") or "")
                hit = next((k for k in RESTRICT_KEYWORDS if k in status), None)
                if hit:
                    why = "상태 문구 '%s'" % hit
                elif "우선" in status:
                    why = "우선주(종목 유형)"
                elif any(k in status.upper() for k in ("ETF", "ETN", "수익증권", "투자회사")):
                    why = "ETF·ETN 등(종목 유형)"
            except kb.ApiError:
                unconfirmed["restriction"] += 1
                status = "확인불가"
        if why is not None:
            continue
        cnt[5] += 1
        m = compute(bars, min(max(price, 0.0), 1e12), scale, shares)
        if live and phase == "intraday" and not (bars[-1]["low"] <= live <= bars[-1]["high"]):
            clamp += 1
        # 52-week high sanity: price above every high of the window (including today's) is a data problem
        if m.get("h52") is None:
            unconfirmed["h52"] += 1
            m["h52_note"] = "확인불가(일봉 %d개)" % len(bars)
        elif m["price_above_known_high"]:
            m["h52_note"] = "데이터 확인 필요(현재가가 일봉 고가보다 높음)"
        else:
            m["h52_note"] = ""
        m.update({"code": code, "name": name, "market": mk, "status": status.strip() or "이상 없음(조회 기준)", "restriction_unconfirmed": status == "확인불가"})
        pool.append(m)
    # remaining stages on the pool
    s6 = [m for m in pool if m["mult"] is not None and m["mult"] >= MULT_MIN]
    cnt[6] = len(s6)
    s7 = [m for m in s6 if m["pos"] >= POS_MIN]
    cnt[7] = len(s7)
    final = s7
    for m in final:
        m["score"] = score(m)
        m["weak"] = weakest(m["score"])
    final.sort(key=lambda m: (-m["score"]["total"], -m["mult"]))
    funnel += list(zip(stages, cnt))

    relax = None
    if len(final) <= 1:
        worst = None
        prev = [n for _, n in funnel][2]
        drops = []
        for (nm, n_) in funnel[3:]:
            drops.append((prev - n_, nm))
            prev = n_
        worst = max(drops)[1] if drops else None
        alt = {}
        for key, (pm, qm) in {"종가 위치 0.6 이상": (MULT_MIN, 0.6), "거래대금 배수 1.5배 이상": (1.5, POS_MIN), "두 가지 모두 완화": (1.5, 0.6)}.items():
            sel = [m for m in pool if m["mult"] is not None and m["mult"] >= pm and m["pos"] >= qm and m not in final]
            for m in sel:
                m.setdefault("score", score(m))
            alt[key] = [{"name": m["name"], "code": m["code"], "mult": m["mult"], "pos": m["pos"], "total": score(m)["total"]} for m in sorted(sel, key=lambda m: -score(m)["total"])[:10]]
        relax = {"worst": worst, "alt": alt}

    phase_text = {"intraday": "장중 잠정치", "final": "마감 후 확정치", "prev": "직전 거래일 확정치"}[phase]
    meta = {"generated": now.strftime("%Y-%m-%d %H:%M"), "base_dt": "%s-%s-%s" % (base_dt[:4], base_dt[4:6], base_dt[6:]), "phase": phase, "phase_text": phase_text,
            "cap_min_eok": a.cap_min, "scale": scale, "universe": len(ranking), "show": a.show, "clamp": clamp}
    if phase == "intraday":
        notes.append("장중 잠정치입니다. 조회할 때마다 값이 달라질 수 있고, 당일 거래대금이 하루 전체가 아니라 그 시점까지의 누적이라 배수가 낮게 나옵니다. 마감(15:40) 후 재확인을 권합니다.")
    if phase == "prev":
        notes.append("기준일은 직전 거래일(%s)이며 확정치입니다." % meta["base_dt"])
    if clamp:
        notes.append("현재가가 일봉의 당일 고저 범위를 벗어난 종목 %d개는 범위를 현재가에 맞춰 계산했습니다(조회 시각 차이)." % clamp)
    not_checked = ["불성실공시법인·투자주의환기종목·이상급등 종목은 API로 확인할 수 있는 필드를 찾지 못해, 상태 문구 키워드와 exclusions.csv에 있는 종목만 제외했습니다. 결과에 포함된 종목이라도 해당 여부를 확인하지 못했습니다.",
                   "우선주·스팩·ETF는 종목 코드·이름·종목 유형 문구 규칙으로 걸렀으며 100% 정확하지 않을 수 있습니다.",
                   "거래대금은 KRX 일봉 기준(대체거래소 제외)입니다. 순위 API 값(대체거래소 합산)은 1차 거르기에만 썼습니다.",
                   "거래정지·정리매매는 거래제한 조회(SIQM4900) 문구와 순위 상태 코드로 판별했고, 모든 사례를 검증하지는 못했습니다."]
    if unconfirmed["cap"]:
        not_checked.append("상장주식수를 받지 못해 시가총액을 계산하지 못한 종목 %d개를 제외했습니다." % unconfirmed["cap"])
    if unconfirmed["restriction"]:
        not_checked.append("거래제한 조회에 실패한 종목 %d개는 해당 항목을 '확인불가'로 두고 통과시켰습니다." % unconfirmed["restriction"])
    if not excl:
        not_checked.append("exclusions.csv가 없거나 비어 있습니다.")

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DOCS_DIR, exist_ok=True)

    def row_out(m, rank=None):
        sc = m.get("score") or score(m)
        return {"rank": rank, "name": m["name"], "code": m["code"], "market": m["market"], "price": m["price"], "chg": m["chg"],
                "value_eok": m["value"] / EOK, "mult": m["mult"], "pos": m["pos"], "cap_eok": (m["cap"] / EOK) if m["cap"] else None,
                "total": sc["total"], "s_mult": sc["mult"], "s_pos": sc["pos"], "s_chg": sc["chg"], "s_turn": sc["turn"], "s_trend": sc["trend"],
                "bull": m["bull"], "drawdown": m["drawdown"], "wick": m["wick"] * 100.0, "vol_mult": m["vol_mult"], "med_mult": m["med_mult"],
                "from_h52": m["from_h52"], "h52_note": m.get("h52_note", ""), "ret5": m["ret5"], "above_ma20": m["above_ma20"],
                "turn": m["turn"], "near": bool(m["near_threshold"]), "status": m["status"], "weak": m.get("weak", weakest(sc))}
    out_rows = [row_out(m, i + 1) for i, m in enumerate(final)]
    payload = {"meta": meta, "funnel": funnel, "rows": out_rows, "relax": relax, "notes": notes, "not_checked": not_checked}
    with open(os.path.join(OUT_DIR, "screen_result.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["순위", "종목명", "코드", "시장", "현재가", "등락률(%)", "거래대금(억)", "거래대금 배수", "종가 위치", "시가총액(억)", "총점",
                    "점수:배수", "점수:종가위치", "점수:등락률", "점수:회전율", "점수:추세", "양봉", "고가 대비 낙폭(%)", "윗꼬리(%)", "거래량 배수",
                    "20일 거래대금 중앙값 대비", "52주 고점 대비(%)", "52주 비고", "5일 상승률(%)", "20일선 위", "회전율(%)", "경계값(2.0~2.1배)", "상태"])
        def n(v, d=2):
            return "확인불가" if v is None else round(v, d)
        for r in out_rows:
            w.writerow([r["rank"], r["name"], r["code"], r["market"], r["price"], n(r["chg"]), n(r["value_eok"], 1), n(r["mult"]), n(r["pos"]), n(r["cap_eok"], 0), r["total"],
                        r["s_mult"], r["s_pos"], r["s_chg"], r["s_turn"], r["s_trend"], "Y" if r["bull"] else "N", n(r["drawdown"]), n(r["wick"], 1), n(r["vol_mult"]),
                        n(r["med_mult"]), n(r["from_h52"]), r["h52_note"], n(r["ret5"]), "Y" if r["above_ma20"] else "N", n(r["turn"]), "Y" if r["near"] else "", r["status"]])
    js = json.dumps(payload, ensure_ascii=False)
    with open(os.path.join(DOCS_DIR, "screen.json"), "w", encoding="utf-8") as f:
        f.write(js)
    html = HTML.replace("__DATA__", js.replace("</", "<\\/"))
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print("\nBase: %s (%s)   passed all required conditions: %d" % (meta["base_dt"], phase_text, len(final)))
    for nm, n_ in funnel:
        print("  %-60s %d" % (nm, n_))
    print("Saved docs\\index.html, docs\\screen.json and result\\screen_result.csv")
    return 0


HTML = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>종목 스크리닝 결과</title>
<style>
:root{color-scheme:light dark;--bg:#f6f7fb;--card:#fff;--ink:#1b2230;--sub:#667089;--line:#dfe3ee;--accent:#2f62d6;--ok:#1b9c6b;--no:#d6453d;--warn:#b7791f;--warnbg:#fff7e6;--infobg:#eef3ff}
@media (prefers-color-scheme:dark){:root{--bg:#0a1020;--card:#111b33;--ink:#e7ecf7;--sub:#8e9bbb;--line:#22315a;--accent:#7db1ff;--ok:#4cd9a0;--no:#ff7b72;--warn:#f2b84b;--warnbg:rgba(242,184,75,.1);--infobg:rgba(91,155,255,.1)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,"Malgun Gothic","Apple SD Gothic Neo",sans-serif}
.wrap{max-width:1150px;margin:0 auto;padding:22px 16px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 10px}
.sub{color:var(--sub);font-size:13px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:14px 0}
.note{border:1px solid var(--line);border-left:4px solid var(--accent);background:var(--infobg);border-radius:10px;padding:9px 13px;margin:10px 0;font-size:13.5px}
.note.warn{border-left-color:var(--warn);background:var(--warnbg)}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{padding:8px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-size:12px;color:var(--sub);font-weight:600}th:nth-child(-n+2),td:nth-child(-n+2){text-align:left}.scroll{overflow-x:auto}
.pos{color:var(--no)}.neg{color:#3b6fe0}.badge{display:inline-block;padding:0 8px;border-radius:999px;border:1px dashed var(--warn);color:var(--warn);font-size:12px}
.btn{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:6px 14px;font-size:13px;cursor:pointer}
.fun td:last-child{font-weight:700}.fun td{text-align:left;white-space:normal}.fun td:last-child{text-align:right;white-space:nowrap}
ul{margin:6px 0 0;padding-left:18px;font-size:13.5px}.empty{padding:26px;text-align:center;color:var(--sub)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px}.mini{border:1px solid var(--line);border-radius:10px;padding:8px 11px;font-size:13px}
</style></head><body><div class="wrap">
<h1>종목 스크리닝 결과</h1><div class="sub" id="meta"></div><div id="notes"></div>
<div class="card"><h2>필터 단계별 통과 종목 수 (누적)</h2><table class="fun" id="fun"></table></div>
<div class="card" id="relax" hidden></div>
<div class="card"><h2>전체 결과표 <span class="sub" id="cnt"></span></h2><div class="scroll"><table id="tbl"><thead><tr>
<th>순위</th><th>종목(코드)</th><th>시장</th><th>현재가</th><th>등락률</th><th>거래대금(억)</th><th>배수</th><th>종가 위치</th><th>시가총액(억)</th><th>총점</th></tr></thead><tbody id="tb"></tbody></table></div>
<div style="margin-top:10px"><button class="btn" id="more" hidden></button></div><div class="empty" id="empty" hidden>모든 필수 조건을 통과한 종목이 없습니다.</div></div>
<div class="card" id="topc" hidden><h2>상위 1~5위 항목별 점수</h2><div class="scroll"><table id="top"><thead><tr><th>순위</th><th>종목</th><th>배수(25)</th><th>종가 위치(20)</th><th>등락률(20)</th><th>회전율(15)</th><th>추세(20)</th><th>총점</th><th style="text-align:left">약한 항목</th></tr></thead><tbody id="tt"></tbody></table></div></div>
<div class="card" id="extc" hidden><h2>추가 확인 항목 (필터링하지 않고 표시만)</h2><div class="scroll"><table id="ext"><thead><tr><th>종목</th><th>양봉</th><th>고가 대비 낙폭</th><th>윗꼬리</th><th>거래량 배수</th><th>20일 중앙값 대비</th><th>52주 고점 대비</th><th>5일 상승률</th><th>20일선 위</th><th>회전율</th><th>비고</th></tr></thead><tbody id="et"></tbody></table></div></div>
<div class="card"><h2>점수식과 기준 요약</h2><ul id="rules"></ul></div>
<div class="card"><h2>확인하지 못한 부분</h2><ul id="nc"></ul></div>
<p class="sub">객관적 조건 충족 여부만 표시하며 매수·매도 권유가 아닙니다. 과거 시험에서 이 계열 조건(거래대금 급증)은 같은 날 평균 대비 우위가 확인되지 않았습니다.</p>
</div>
<script>
var EMBED=__DATA__;
function render(D){
function $(i){return document.getElementById(i)}
function f(x,d,s){if(x==null)return '확인불가';var v=Number(x).toFixed(d==null?2:d);return (s&&x>0?'+':'')+v}
function td(tr,t,c){var e=document.createElement('td');e.textContent=t;if(c)e.className=c;tr.appendChild(e);return e}
function th(tr,t){var e=document.createElement('th');e.textContent=t;tr.appendChild(e)}
var M=D.meta;
$('meta').textContent='기준: '+M.base_dt+' · '+M.phase_text+' · 조회 시각 '+M.generated+' · 시가총액 기준 '+M.cap_min_eok.toLocaleString('ko-KR')+'억 이상';
D.notes.forEach(function(t){var d=document.createElement('div');d.className='note warn';d.textContent=t;$('notes').appendChild(d)});
D.funnel.forEach(function(r){var tr=document.createElement('tr');td(tr,r[0]);td(tr,String(r[1]));$('fun').appendChild(tr)});
var rows=D.rows,shown=M.show;
function fmtRow(tr,r){td(tr,String(r.rank));var n=td(tr,r.name+' ('+r.code+')');if(r.near){var b=document.createElement('span');b.className='badge';b.textContent='경계값';b.style.marginLeft='6px';n.appendChild(b)}
td(tr,r.market);td(tr,Number(r.price).toLocaleString('ko-KR'));td(tr,f(r.chg,2,true)+'%',r.chg>0?'pos':(r.chg<0?'neg':''));td(tr,r.value_eok==null?'확인불가':Number(r.value_eok).toLocaleString('ko-KR',{maximumFractionDigits:0}));td(tr,f(r.mult,2)+'배');td(tr,f(r.pos,2));td(tr,r.cap_eok==null?'확인불가':Number(r.cap_eok).toLocaleString('ko-KR',{maximumFractionDigits:0}));td(tr,f(r.total,1))}
function draw(){var tb=$('tb');tb.textContent='';rows.slice(0,shown).forEach(function(r){var tr=document.createElement('tr');fmtRow(tr,r);tb.appendChild(tr)});
$('cnt').textContent='(통과 '+rows.length+'개, 총점 순 · 동점이면 배수가 큰 쪽 먼저)';$('empty').hidden=rows.length>0;
var b=$('more');if(rows.length>M.show){b.hidden=false;b.textContent=shown>=rows.length?'상위 '+M.show+'개만 보기':'전체 '+rows.length+'개 보기 (CSV에도 전체가 있습니다)'}else b.hidden=true}
$('more').onclick=function(){shown=shown>=rows.length?M.show:rows.length;draw()};draw();
if(rows.length){$('topc').hidden=false;rows.slice(0,5).forEach(function(r){var tr=document.createElement('tr');td(tr,String(r.rank));td(tr,r.name);[r.s_mult,r.s_pos,r.s_chg,r.s_turn,r.s_trend,r.total].forEach(function(v){td(tr,f(v,1))});var w=td(tr,r.weak);w.style.textAlign='left';$('tt').appendChild(tr)});
$('extc').hidden=false;rows.forEach(function(r){var tr=document.createElement('tr');td(tr,r.name);td(tr,r.bull?'양봉':'음봉·보합');td(tr,f(r.drawdown,2)+'%');td(tr,f(r.wick,1)+'%');td(tr,f(r.vol_mult,2)+'배');td(tr,f(r.med_mult,2)+'배');td(tr,r.from_h52==null?(r.h52_note||'확인불가'):f(r.from_h52,2,true)+'%'+(r.h52_note?' ('+r.h52_note+')':''));td(tr,f(r.ret5,2,true)+'%');td(tr,r.above_ma20?'위':'아래');td(tr,f(r.turn,2)+'%');td(tr,r.status);$('et').appendChild(tr)})}
if(D.relax){var c=$('relax');c.hidden=false;var h=document.createElement('h2');h.textContent='통과 종목이 0~1개일 때: 가장 많이 탈락시킨 조건과 완화 결과';c.appendChild(h);
var p=document.createElement('div');p.textContent='가장 많이 탈락시킨 조건: '+(D.relax.worst||'확인불가');c.appendChild(p);
Object.keys(D.relax.alt).forEach(function(k){var g=document.createElement('div');g.className='mini';g.style.marginTop='8px';var a=D.relax.alt[k];g.textContent=k+' → 추가로 통과 '+a.length+'개'+(a.length?': '+a.map(function(x){return x.name+'(배수 '+f(x.mult,2)+', 종가 위치 '+f(x.pos,2)+', 총점 '+f(x.total,1)+')'}).join(', '):'');c.appendChild(g)})}
['1. 필수 조건: 거래대금 300억 이상(KRX 일봉), 시가총액 기준 이상, 최근 거래일 20일 이상, 주가 1,000원 이상, 제외 종목 아님, 거래대금 ≥ 20일 평균의 2배, 종가 위치 0.7 이상',
'20일 평균 = 당일을 제외한 직전 20거래일 평균. 종가 위치 = (현재가-저가)/(고가-저가), 고가=저가이면 1.0. 현재가는 장중에는 조회 시점 가격, 마감 후·휴장 시에는 종가',
'거래대금 배수 25점: 2배=10점, 10배 이상=25점, 사이는 비례',
'종가 위치 20점: 0.7=10점, 1.0=20점, 사이는 비례. 윗꼬리 비율 (고가-max(시가,현재가))/(고가-저가)가 30% 초과면 5점 감점(최저 0점)',
'등락률 20점: +1%~+6%=20점, +0.5%~+1%=13점, 0~+0.5%=7점, 하락=0점. +6% 초과는 초과분 1%p당 2점 감점(최저 0점)',
'회전율(거래대금/시가총액) 15점: 1%=0점, 5% 이상=15점, 사이는 비례. 30% 초과는 과열로 보고 10점 감점(최저 0점)',
'추세 위치 20점: 현재가가 20일선 위=10점(20일선 = 직전 19거래일 종가와 현재가의 평균), 최근 5거래일 상승률(현재가/5거래일 전 종가) +20% 이하=10점',
'점수식은 임의로 정한 값이며 가중치 합계는 100점입니다. 표시·정렬은 총점 순, 동점이면 거래대금 배수가 큰 쪽']
.forEach(function(t){var l=document.createElement('li');l.textContent=t;$('rules').appendChild(l)});
D.not_checked.forEach(function(t){var l=document.createElement('li');l.textContent=t;$('nc').appendChild(l)});
}
if(location.protocol.indexOf('http')===0){fetch('screen.json?ts='+Date.now(),{cache:'no-store'}).then(function(r){return r.ok?r.json():null}).catch(function(){return null}).then(function(j){render(j||EMBED)})}else{render(EMBED)}
</script></body></html>
'''


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kb.ApiError as e:
        print("ERROR:", e)
        sys.exit(1)
