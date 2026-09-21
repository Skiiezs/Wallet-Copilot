import os
import time
import threading

import requests
from flask import Flask, jsonify, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
WALLET = os.environ.get("WALLET", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
PUMP_JWT = os.environ.get("PUMP_JWT", "")

SURGE_PCT = float(os.environ.get("SURGE_PCT", "12"))
WHALE_5M_USD = float(os.environ.get("WHALE_5M_USD", "2500"))
POLL_SEC = int(os.environ.get("POLL_SEC", "45"))

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WSOL = "So11111111111111111111111111111111111111112"
PUMP_API = "https://frontend-api-v3.pump.fun"

seen = set()
grok_done = set()
watch = {}
last_whale_ping = {}

KOLS = {
    "Cented": "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
    "Gake": "DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm",
    "Euris": "DfMxre4cKmvogbLrPigxmibVTTQDuzjdXojWzjCXXhzj",
    "Profit": "G5nxEXuFMfV74DSnsrSatqCW32F34XUnBeq3PfDS7w5E",
    "Waddles": "73LnJ7G9ffBDjEBGgJDdgvLUhD5APLonKrNiHsKDCw5B",
    "Mr Frog": "4DdrfiDHpmx55i4SPssxVzS9ZaKLb8qr45NKY9Er9nNh",
    "Joji": "525LueqAyZJueCoiisfWy6nyh4MTvmF4X9jSqi6efXJT",
    "Jijo": "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk",
    "Letterbomb": "BtMBMPkoNbnLF9Xn552guQq528KKXcsNBNNBre3oaQtr",
    "Orangie": "26kZ9rg8Y5pd4j1tdT4cbT8BQRu5uDbXkaVs3L5QasHy",
    "Cooker": "8deJ9xeUvXSJwicYptA9mHsU2rN2pDx37KWzkDkEXhU6",
    "Insentos": "7SDs3PjT2mswKQ7Zo4FTucn9gJdtuW4jaacPA65BseHS",
    "Frank": "CRVidEDtEUTYZisCxBZkpELzhQc9eauMLR3FWg74tReL",
    "Bastille": "3kebnKw7cPdSkLRfiMEALyZJGZ4wdiSRvmoN4rD1yPzV",
    "Yenni": "5B52w1ZW9tuwUduueP5J7HXz5AcGfruGoX6YoAudvyxG",
    "Publix": "86AEJExyjeNNgcp7GrAvCXTDicf5aGWgoERbXFiG1EdD",
    "Heyitsyolo": "Av3xWHJ5EsoLZag6pr7LKbrGgLRTaykXomDD5kBhL9YQ",
}

PUMP_HDR = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}
if PUMP_JWT:
    PUMP_HDR["Authorization"] = f"Bearer {PUMP_JWT}"


def money(n):
    try:
        n = float(n)
    except Exception:
        return "—"
    if abs(n) >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"${n/1_000:.1f}k"
    return f"${n:.2f}"


def helius_rpc(method, params):
    if not HELIUS_API_KEY:
        return {}
    url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    try:
        r = requests.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=20,
        )
        return (r.json() or {}).get("result") or {}
    except Exception:
        return {}


def dex_stats(mint):
    out = {
        "ticker": "?",
        "name": "unknown",
        "mc": None,
        "liq": None,
        "vol24": None,
        "vol5": None,
        "price": None,
        "chg24": None,
        "age": None,
        "image": None,
        "dex": f"https://dexscreener.com/solana/{mint}",
        "pair": None,
    }
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=15,
        )
        pairs = (r.json() or {}).get("pairs") or []
        sol = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol:
            return out
        p = sorted(
            sol,
            key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0),
            reverse=True,
        )[0]
        base = p.get("baseToken") or {}
        out["ticker"] = base.get("symbol") or "?"
        out["name"] = base.get("name") or out["ticker"]
        out["mc"] = p.get("marketCap") or p.get("fdv")
        out["liq"] = (p.get("liquidity") or {}).get("usd")
        vol = p.get("volume") or {}
        out["vol24"] = vol.get("h24")
        out["vol5"] = vol.get("m5")
        out["price"] = p.get("priceUsd")
        chg = p.get("priceChange") or {}
        out["chg24"] = chg.get("h24")
        info = p.get("info") or {}
        out["image"] = info.get("imageUrl")
        out["dex"] = p.get("url") or out["dex"]
        out["pair"] = p.get("pairAddress")
        created = p.get("pairCreatedAt")
        if created:
            age_m = max(0, (time.time() * 1000 - created) / 60000)
            if age_m < 60:
                out["age"] = f"{int(age_m)}m"
            elif age_m < 1440:
                out["age"] = f"{int(age_m/60)}h"
            else:
                out["age"] = f"{int(age_m/1440)}d"
    except Exception:
        pass
    return out


def helius_asset(mint):
    name, ticker = None, None
    try:
        res = helius_rpc("getAsset", {"id": mint}) or {}
        content = res.get("content") or {}
        meta = content.get("metadata") or {}
        name = meta.get("name")
        ticker = meta.get("symbol")
    except Exception:
        pass
    return name, ticker


def holder_dist(mint):
    dist = {"top1": None, "top5": None, "top10": None, "top20": None, "rows": []}
    try:
        res = helius_rpc(
            "getTokenLargestAccounts",
            [mint, {"commitment": "confirmed"}],
        ) or {}
        accs = res.get("value") or []
        supply_res = helius_rpc("getTokenSupply", [mint]) or {}
        supply = float(((supply_res.get("value") or {}).get("uiAmount")) or 0)
        rows = []
        for a in accs[:20]:
            amt = float((a.get("uiAmount")) or 0)
            owner = a.get("address")
            pct = (amt / supply * 100) if supply else 0
            rows.append({"wallet": owner, "amt": amt, "pct": round(pct, 2)})
        dist["rows"] = rows
        if rows:
            dist["top1"] = round(rows[0]["pct"], 2)
            dist["top5"] = round(sum(r["pct"] for r in rows[:5]), 2)
            dist["top10"] = round(sum(r["pct"] for r in rows[:10]), 2)
            dist["top20"] = round(sum(r["pct"] for r in rows[:20]), 2)
    except Exception:
        pass
    return dist


def wallet_holds(owner, mint):
    try:
        res = helius_rpc(
            "getTokenAccountsByOwner",
            [owner, {"mint": mint}, {"encoding": "jsonParsed"}],
        ) or {}
        for acc in res.get("value") or []:
            info = (
                ((acc.get("account") or {}).get("data") or {}).get("parsed") or {}
            ).get("info") or {}
            if info.get("mint") != mint:
                continue
            amt = float(((info.get("tokenAmount") or {}).get("uiAmount")) or 0)
            if amt > 0:
                return amt
    except Exception:
        pass
    for program in (TOKEN_PROGRAM, TOKEN_2022):
        try:
            res = helius_rpc(
                "getTokenAccountsByOwner",
                [owner, {"programId": program}, {"encoding": "jsonParsed"}],
            ) or {}
            for acc in res.get("value") or []:
                info = (
                    ((acc.get("account") or {}).get("data") or {}).get("parsed") or {}
                ).get("info") or {}
                if info.get("mint") != mint:
                    continue
                amt = float(((info.get("tokenAmount") or {}).get("uiAmount")) or 0)
                if amt > 0:
                    return amt
        except Exception:
            continue
    return 0.0


def smart_wallets(mint):
    hits = []
    for name, addr in KOLS.items():
        amt = wallet_holds(addr, mint)
        if amt > 0:
            hits.append({"wallet": addr, "name": name, "amt": amt, "tag": "KOL"})
    return hits


def book_lines(dist, smarts):
    d1 = f"{dist.get('top1')}%" if dist.get("top1") is not None else "—"
    d5 = f"{dist.get('top5')}%" if dist.get("top5") is not None else "—"
    d10 = f"{dist.get('top10')}%" if dist.get("top10") is not None else "—"
    d20 = f"{dist.get('top20')}%" if dist.get("top20") is not None else "—"
    dist_s = f"T1 `{d1}`  T5 `{d5}`  T10 `{d10}`  T20 `{d20}`"
    if dist.get("rows"):
        dist_s += "\n" + "\n".join(
            f"`{r['wallet'][:4]}…{r['wallet'][-4:]}`  {r['pct']}%"
            for r in dist["rows"][:4]
        )
    if smarts:
        smart_s = "\n".join(
            f"**{s.get('name') or 'KOL'}**  `{s['wallet'][:4]}…{s['wallet'][-4:]}`"
            for s in smarts[:8]
        )
    else:
        smart_s = "no tracked KOLs in this bag"
    return dist_s[:1024], smart_s[:1024]


def pump_get(path, params=None):
    try:
        r = requests.get(
            PUMP_API + path,
            headers=PUMP_HDR,
            params=params or {},
            timeout=12,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def pump_coin(mint):
    data = pump_get(f"/coins/{mint}") or {}
    if not isinstance(data, dict):
        return {}
    return {
        "replies": data.get("reply_count") or 0,
        "usd_mc": data.get("usd_market_cap"),
        "graduated": bool(data.get("complete")),
        "username": data.get("username") or "",
        "desc": (data.get("description") or "")[:180],
    }


def pump_replies(mint, n=6):
    data = pump_get(
        f"/replies/{mint}",
        {"limit": n, "offset": 0, "reverseOrder": "true"},
    )
    rows = []
    if isinstance(data, dict):
        rows = data.get("replies") or data.get("data") or []
    elif isinstance(data, list):
        rows = data
    out = []
    for row in rows[:n]:
        if not isinstance(row, dict):
            continue
        user = (
            row.get("username")
            or (row.get("user") or {}).get("username")
            or ((row.get("user") or {}).get("address") or "")[:6]
            or "?"
        )
        text = (row.get("text") or row.get("message") or "").replace("\n", " ").strip()
        if text:
            out.append(f"**{user}:** {text[:90]}")
    return out


def pump_callouts(mint, n=6):
    for path in (
        f"/callout/coin/{mint}",
        f"/callouts/{mint}",
        f"/callout/list-by-mint/{mint}",
    ):
        data = pump_get(
            path,
            {"sortBy": "TIMESTAMP", "sortOrder": "DESC", "limit": n},
        )
        if not data:
            continue
        rows = (
            data
            if isinstance(data, list)
            else (data.get("callouts") or data.get("data") or [])
        )
        hits = []
        for row in rows[:n]:
            if not isinstance(row, dict):
                continue
            user = row.get("username") or row.get("caller") or "?"
            hits.append(f"**{user}** called")
        if hits:
            return hits
    return []


def fomo_line(stats, coin, replies, calls):
    vol5 = float(stats.get("vol5") or 0)
    nrep = int(coin.get("replies") or len(replies) or 0)
    ncall = len(calls)
    score = 0
    if vol5 >= 2500:
        score += 2
    if nrep >= 20:
        score += 2
    elif nrep >= 5:
        score += 1
    if ncall:
        score += 2
    if stats.get("age") and str(stats["age"]).endswith("m"):
        score += 1
    label = {
        0: "cold",
        1: "warm",
        2: "heating",
        3: "fomo",
        4: "FOMO",
        5: "FOMO+",
    }.get(min(score, 5), "FOMO+")
    extra = "  graduated" if coin.get("graduated") else ""
    return f"`{label}`  replies `{nrep}`  callouts `{ncall}`  5m {money(vol5)}{extra}"


def social_field(replies, calls):
    parts = []
    if calls:
        parts.append("**calls**\n" + "\n".join(calls[:4]))
    if replies:
        parts.append("**pump chat**\n" + "\n".join(replies[:4]))
    return ("\n\n".join(parts) or "no pump chat / callouts")[:1024]


def grok_report(ticker, name, mint, stats, dist, smarts, fomo):
    if not XAI_API_KEY:
        return "no xAI key"
    prompt = (
        f"Blunt 8-line Solana meme DD. No fluff.\n"
        f"Token ${ticker} {name}\nCA {mint}\n"
        f"MC {stats.get('mc')} liq {stats.get('liq')} vol24 {stats.get('vol24')} "
        f"age {stats.get('age')} chg24 {stats.get('chg24')}\n"
        f"FOMO {fomo}\n"
        f"Top holders T1/T5/T10/T20 {dist.get('top1')}/{dist.get('top5')}/"
        f"{dist.get('top10')}/{dist.get('top20')}\n"
        f"Tracked KOLs in: {[s.get('name') for s in smarts] or 'none'}\n"
        f"Say if bundled/farm vs organic. Kill or hold. Risk first."
    )
    try:
        r = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "grok-4.3",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            timeout=45,
        )
        data = r.json() or {}
        return (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            or f"grok empty {r.status_code}"
        )
    except Exception as e:
        return f"grok fail {e}"


def rick_embed(
    kind,
    ticker,
    name,
    mint,
    body,
    stats,
    color,
    dist_s,
    smart_s,
    fomo="",
    social="",
):
    dex = stats.get("dex") or f"https://dexscreener.com/solana/{mint}"
    chg = stats.get("chg24")
    chg_s = f"{chg:+.1f}%" if isinstance(chg, (int, float)) else "—"
    block = (
        f"`MC` {money(stats.get('mc'))}   `LIQ` {money(stats.get('liq'))}\n"
        f"`VOL` {money(stats.get('vol24'))}   `5m` {money(stats.get('vol5'))}\n"
        f"`PX` {stats.get('price') or '—'}   `24h` {chg_s}   `AGE` {stats.get('age') or '—'}"
    )
    desc = f"{block}\n\n{body}".strip()[:3900]
    venues = (
        f"[Axiom](https://axiom.trade/t/{mint}) · "
        f"[Photon](https://photon-sol.tinyastro.io/en/lp/{mint}) · "
        f"[Dex]({dex}) · "
        f"[GMGN](https://gmgn.ai/sol/token/{mint}) · "
        f"[Pump](https://pump.fun/coin/{mint})"
    )
    embed = {
        "author": {"name": "SKYZ  ·  scan"},
        "title": f"${ticker}   {kind}",
        "url": dex,
        "description": desc,
        "color": color,
        "footer": {"text": f"{name} · {mint[:6]}…{mint[-4:]}"},
        "fields": [
            {"name": "FOMO", "value": fomo or "—", "inline": False},
            {"name": "Pump", "value": social or "—", "inline": False},
            {"name": "Supply", "value": dist_s or "—", "inline": False},
            {"name": "KOLs in", "value": smart_s or "—", "inline": False},
            {"name": "Venues", "value": venues, "inline": False},
        ],
    }
    if stats.get("image"):
        embed["thumbnail"] = {"url": stats["image"]}
    return embed


def discord_embed(embed):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=15)
    except Exception:
        pass


def process_buy(mint, sig=None):
    mint = (mint or "").strip()
    if not mint or mint == WSOL:
        return
    first = mint not in grok_done
    stats = dex_stats(mint)
    aname, asym = helius_asset(mint)
    ticker = stats.get("ticker") if stats.get("ticker") != "?" else (asym or "?")
    name = stats.get("name") if stats.get("name") != "unknown" else (aname or ticker)
    dist = holder_dist(mint)
    smarts = smart_wallets(mint)
    dist_s, smart_s = book_lines(dist, smarts)
    coin = pump_coin(mint)
    replies = pump_replies(mint)
    calls = pump_callouts(mint)
    fomo = fomo_line(stats, coin, replies, calls)
    social = social_field(replies, calls)
    if first:
        body = grok_report(ticker, name, mint, stats, dist, smarts, fomo)
        grok_done.add(mint)
    else:
        body = "repeat buy · grok skipped"
    color = 0x3DFF8A if first else 0x58A6FF
    kind = "first scan" if first else "add"
    discord_embed(
        rick_embed(
            kind,
            ticker,
            name,
            mint,
            body,
            stats,
            color,
            dist_s,
            smart_s,
            fomo=fomo,
            social=social,
        )
    )
    watch[mint] = {
        "ticker": ticker,
        "name": name,
        "entry_px": float(stats.get("price") or 0) or None,
        "last_px": float(stats.get("price") or 0) or None,
        "t": time.time(),
    }


def extract_mints(payload):
    mints = []
    events = payload if isinstance(payload, list) else [payload]
    for ev in events:
        if not isinstance(ev, dict):
            continue
        for xf in ev.get("tokenTransfers") or []:
            mint = xf.get("mint")
            to_ = (xf.get("toUserAccount") or xf.get("toUser") or "").strip()
            if mint and WALLET and to_ == WALLET and mint != WSOL:
                mints.append(mint)
        swap = (ev.get("events") or {}).get("swap")
        if swap:
            for outt in swap.get("tokenOutputs") or []:
                mint = outt.get("mint")
                if mint and mint != WSOL:
                    mints.append(mint)
    return list(dict.fromkeys(mints))


@app.get("/")
def home():
    return jsonify({"ok": True, "wallet": WALLET[-6:] if WALLET else None})


@app.get("/health")
def health():
    return jsonify({"ok": True, "watch": list(watch.keys())})


@app.post("/helius")
def helius():
    if WEBHOOK_SECRET:
        got = request.headers.get("Authorization") or request.args.get("secret")
        if got != WEBHOOK_SECRET and got != f"Bearer {WEBHOOK_SECRET}":
            return jsonify({"error": "auth"}), 401
    payload = request.get_json(silent=True) or []
    mints = extract_mints(payload)
    for mint in mints:
        if mint in seen:
            continue
        seen.add(mint)
        threading.Thread(target=process_buy, args=(mint,), daemon=True).start()
    return jsonify({"ok": True, "mints": mints})


@app.get("/test")
def test():
    mint = request.args.get("mint") or request.args.get("ca")
    if not mint:
        return jsonify({"error": "pass ?mint="}), 400
    process_buy(mint)
    return jsonify({"ok": True, "mint": mint})


def poll_positions():
    while True:
        time.sleep(POLL_SEC)
        now = time.time()
        for mint, pos in list(watch.items()):
            stats = dex_stats(mint)
            px = float(stats.get("price") or 0) or None
            if not px:
                continue
            last = pos.get("last_px") or px
            entry = pos.get("entry_px") or px
            pos["last_px"] = px
            chg = ((px - last) / last * 100) if last else 0
            from_entry = ((px - entry) / entry * 100) if entry else 0
            vol5 = float(stats.get("vol5") or 0)
            ticker = pos.get("ticker") or stats.get("ticker") or "?"
            name = pos.get("name") or stats.get("name") or ticker
            if abs(chg) >= SURGE_PCT:
                color = 0x3DFF8A if chg > 0 else 0xFF4D4D
                body = f"move `{chg:+.1f}%` vs last poll\nfrom entry `{from_entry:+.1f}%`"
                discord_embed(
                    rick_embed(
                        "surge",
                        ticker,
                        name,
                        mint,
                        body,
                        stats,
                        color,
                        "live book",
                        "—",
                    )
                )
            if vol5 >= WHALE_5M_USD and now - last_whale_ping.get(mint, 0) > 180:
                last_whale_ping[mint] = now
                discord_embed(
                    rick_embed(
                        "whale tape",
                        ticker,
                        name,
                        mint,
                        f"5m vol {money(vol5)}",
                        stats,
                        0xF5C542,
                        "live book",
                        "—",
                    )
                )
            calls = pump_callouts(mint)
            ck = mint + ":call"
            if calls and now - last_whale_ping.get(ck, 0) > 180:
                last_whale_ping[ck] = now
                discord_embed(
                    rick_embed(
                        "pump call",
                        ticker,
                        name,
                        mint,
                        "\n".join(calls[:5]),
                        stats,
                        0xF5C542,
                        "live book",
                        "—",
                        fomo="callout hit",
                        social="\n".join(calls[:5]),
                    )
                )


def start_poller():
    threading.Thread(target=poll_positions, daemon=True).start()


start_poller()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)