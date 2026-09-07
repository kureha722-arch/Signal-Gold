import json
import os
import sys
import urllib.parse
import urllib.request

SYMBOL = "XAU/USD"
INTERVAL = "15min"
BARS = 260

EXPIRY_BARS = 96          # ゾーンの有効期限（15分足96本 = 24時間）
MIN_SIZE_ATR = 0.30       # ATRのこの倍率未満のギャップは無視
ATR_PERIOD = 14

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
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read().decode())
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
    """3本のローソク足からFVG（インバランス）を検出する。"""
    zones = []
    for i in range(1, len(bars) - 1):
        a, c = bars[i - 1], bars[i + 1]
        av = atr(bars, i + 1)
        if av is None:
            continue
        floor = av * MIN_SIZE_ATR

        if a["h"] < c["l"] and (c["l"] - a["h"]) >= floor:
            zones.append({
                "id": "L" + bars[i]["t"],
                "side": "long",
                "bottom": a["h"],
                "top": c["l"],
                "born": i + 1,
                "born_t": bars[i + 1]["t"],
            })
        elif a["l"] > c["h"] and (a["l"] - c["h"]) >= floor:
            zones.append({
                "id": "S" + bars[i]["t"],
                "side": "short",
                "bottom": c["h"],
                "top": a["l"],
                "born": i + 1,
                "born_t": bars[i + 1]["t"],
            })
    return zones


def alive(zone, bars, last):
    """期限切れ・完全な埋め戻しでゾーンを失効させる。"""
    if last - zone["born"] > EXPIRY_BARS:
        return False
    for k in range(zone["born"] + 1, last):
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
    cur = bars[last]
    st = load_state()
    seen = set(st["notified"])
    av = atr(bars, last)

    hits = []
    for z in find_zones(bars):
        if z["born"] >= last:
            continue
        if z["id"] in seen:
            continue
        if not alive(z, bars, last):
            continue
        if touched(z, cur):
            hits.append(z)

    for z in hits:
        side = "ロング" if z["side"] == "long" else "ショート"
        msg = (
            "**%s シグナル — XAUUSD 15分足**\n"
            "ゾーン: %.2f – %.2f\n"
            "現在値: %.2f （足: %s）\n"
            "ATR(14): %.2f\n"
            "ゾーン生成: %s"
        ) % (side, z["bottom"], z["top"], cur["c"], cur["t"], av or 0.0, z["born_t"])
        notify(msg)
        seen.add(z["id"])
        print("alert: " + z["id"])

    st["notified"] = sorted(seen)
    save_state(st)
    print("checked %d bars, %d alerts" % (len(bars), len(hits)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
