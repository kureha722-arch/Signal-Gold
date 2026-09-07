import json
import os
import sys
import time
import urllib.parse
import urllib.request

SYMBOL = "XAU/USD"
INTERVAL = "15min"
BARS = 260

EXPIRY_BARS = 96          # ゾーンの有効期限（15分足96本 = 24時間）
LOOKBACK_BARS = 4         # 実行が落ちた分を拾うため、直近この本数まで遡ってタッチ判定
ATR_PERIOD = 14           # 通知に載せるためだけに計算（フィルターには使わない）

STATE_FILE = "state.json"

TD_KEY = os.environ.get("TD_API_KEY", "")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")


def fetch_bars():
    q = urllib.parse.urlencode({
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": BARS,
        "order": "ASC",
        "apikey": TD_KEY,
    })
    url = "https://api.twelvedata.com/time_series?" + q

    data = None
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
            break
        except Exception as e:
            last_err = e
            print("attempt %d failed: %s" % (attempt + 1, e))
            if attempt < 2:
                time.sleep(20)

    if data is None:
        raise RuntimeError("all attempts failed: %s" % last_err)
    if "values" not in data:
        raise RuntimeError("API error: %s" % data.get("message", data))

    out = []
    for v in data["values"]:
        out.append({
            "t": v["datetime"],
            "o": float(v["open"]),
            "h": float(v["high"]),
            "l": float(v["low"]),
            "c": float(v["close"]),
        })
    return out


def atr(bars, i, period=ATR_PERIOD):
    if i < period:
        return None
    s = 0.0
    for k in range(i - period + 1, i + 1):
        tr = max(
            bars[k]["h"] - bars[k]["l"],
            abs(bars[k]["h"] - bars[k - 1]["c"]),
            abs(bars[k]["l"] - bars[k - 1]["c"]),
        )
        s += tr
    return s / period


def find_zones(bars):
    """3本のローソク足からFVG（インバランス）を検出する。サイズによる除外はしない。"""
    zones = []
    for i in range(1, len(bars) - 1):
        a, c = bars[i - 1], bars[i + 1]

        if a["h"] < c["l"]:
            zones.append({
                "id": "L" + bars[i]["t"],
                "side": "long",
                "bottom": a["h"],
                "top": c["l"],
                "born": i + 1,
                "born_t": bars[i + 1]["t"],
            })
        elif a["l"] > c["h"]:
            zones.append({
                "id": "S" + bars[i]["t"],
                "side": "short",
                "bottom": c["h"],
                "top": a["l"],
                "born": i + 1,
                "born_t": bars[i + 1]["t"],
            })
    return zones


def alive(zone, bars, upto):
    """期限切れ・完全な埋め戻しでゾーンを失効させる。"""
    if upto - zone["born"] > EXPIRY_BARS:
        return False
    for k in range(zone["born"] + 1, upto):
        b = bars[k]
        if zone["side"] == "long" and b["l"] <= zone["bottom"]:
            return False
        if zone["side"] == "short" and b["h"] >= zone["top"]:
            return False
    return True


def touched(zone, bar):
    if zone["side"] == "long":
        return bar["l"] <= zone["top"] and bar["h"] >= zone["bottom"]
    return bar["h"] >= zone["bottom"] and bar["l"] <= zone["top"]


def notify(text):
    if not WEBHOOK:
        print("no webhook set:\n" + text)
        return
    body = json.dumps({"content": text}).encode()
    req = urllib.request.Request(
        WEBHOOK, data=body, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=30).read()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"notified": []}


def save_state(st):
    st["notified"] = st["notified"][-400:]
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=1)


def main():
    if not TD_KEY:
        print("TD_API_KEY is not set")
        return 1

    bars = fetch_bars()
    if len(bars) < ATR_PERIOD + 5:
        print("not enough bars")
        return 1

    last = len(bars) - 1
    st = load_state()
    seen = set(st["notified"])
    av = atr(bars, last)

    hits = []
    for z in find_zones(bars):
        if z["born"] >= last:
            continue
        if z["id"] in seen:
            continue
        start = max(z["born"] + 1, last - LOOKBACK_BARS + 1)
        for k in range(start, last + 1):
            if not alive(z, bars, k):
                break
            if touched(z, bars[k]):
                hits.append((z, k))
                break

    for z, k in hits:
        bar = bars[k]
        side = "ロング" if z["side"] == "long" else "ショート"
        delay = "" if k == last else "（%d本前）" % (last - k)
        msg = (
            "**%s シグナル — XAUUSD 15分足**%s\n"
            "ゾーン: %.2f – %.2f\n"
            "タッチ足: %s\n"
            "現在値: %.2f\n"
            "ATR(14): %.2f\n"
            "ゾーン生成: %s"
        ) % (side, delay, z["bottom"], z["top"], bar["t"],
             bars[last]["c"], av or 0.0, z["born_t"])
        notify(msg)
        seen.add(z["id"])
        print("alert: " + z["id"])

    st["notified"] = sorted(seen)
    save_state(st)
    print("checked %d bars, %d alerts" % (len(bars), len(hits)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
