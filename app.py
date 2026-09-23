import os
import time
import threading
from datetime import datetime

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
WHALE_5M_USD = float(os.environ.get("WHALE_5M_USD", "250"))
POLL_SEC = int(os.environ.get("POLL_SEC", "8"))

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
SKIP_MINTS = {WSOL, USDC, USDT}
PUMP_API = "https://frontend-api-v3.pump.fun"
GECKO = "https://api.geckoterminal.com/api/v2"

seen = set()
grok_done = set()
watch = {}
last_whale_ping = {}
seen_trades = set()
wallet_cache = {}

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
KOL_BY_ADDR = {v: k for k, v in KOLS.items()}

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
        data = r.json() or {}
        if "result" in data:
            return data.get("result")
        return {}
    except Exception:
        return {}


def helius_rpc_list(method, params):
    res = helius_rpc(method, params)
    return res if isinstance(res, list) else []


def token_account_owner(token_acc):
    try:
        res = helius_rpc(
            "getAccountInfo",
            [token_acc, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        ) or {}
        value = res.get("value") or {}
        data = value.get("data")
        if isinstance(data, dict):
            info = ((data.get("parsed") or {}).get("info") or {})
            return info.get("owner")
    except Exception:
        pass
    return None


def token_supply(mint):
    try:
        res = helius_rpc("getTokenSupply", [mint]) or {}
        return float(((res.get("value") or {}).get("uiAmount")) or 0)
    except Exception:
        return 0.0


def sol_px():
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{WSOL}",
            timeout=10,
        )
        pairs = (r.json() or {}).get("pairs") or []
        for p in pairs:
            if p.get("chainId") != "solana":
                continue
            px = p.get("priceUsd")
            if px:
                return float(px)
    except Exception:
        pass
    return 150.0


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
        "socials": [],
        "boosts": 0,
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
        out["boosts"] = int((p.get("boosts") or {}).get("active") or 0)
        socials = []
        for s in info.get("socials") or []:
            url = s.get("url") if isinstance(s, dict) else None
            if url:
                socials.append(url)
        for w in info.get("websites") or []:
            url = w.get("url") if isinstance(w, dict) else w
            if url:
                socials.append(url)
        out["socials"] = socials[:4]
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
        supply = token_supply(mint)
        rows = []
        for a in accs[:20]:
            amt = float((a.get("uiAmount")) or 0)
            token_acc = a.get("address")
            owner = token_account_owner(token_acc) or token_acc
            pct = (amt / supply * 100) if supply else 0
            tag = KOL_BY_ADDR.get(owner)
            rows.append(
                {
                    "wallet": owner,
                    "ata": token_acc,
                    "amt": amt,
                    "pct": round(pct, 2),
                    "tag": tag,
                }
            )
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
    dist_s = f"T1 `{d1}` · T5 `{d5}` · T10 `{d10}` · T20 `{d20}`"
    if dist.get("rows"):
        lines = []
        for r in dist["rows"][:4]:
            w = r["wallet"]
            tag = f"  **{r['tag']}**" if r.get("tag") else ""
            lines.append(f"`{w[:4]}…{w[-4:]}`  {r['pct']}%{tag}")
        dist_s += "\n" + "\n".join(lines)
    if smarts:
        smart_s = "\n".join(
            f"👑 **{s.get('name') or 'KOL'}**  `{s['wallet'][:4]}…{s['wallet'][-4:]}`"
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
        "creator": data.get("creator") or "",
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
    extra = "  · 🎓 graduated" if coin.get("graduated") else ""
    creator = coin.get("username") or ""
    cre = f"  · 👤 `{creator}`" if creator else ""
    return f"`{label}`  ·  💬 `{nrep}`  ·  📣 `{ncall}`  ·  5m {money(vol5)}{extra}{cre}"


def social_field(replies, calls, stats, coin):
    parts = []
    links = stats.get("socials") or []
    if links:
        parts.append(" · ".join(f"[link]({u})" for u in links[:4]))
    if coin.get("creator"):
        c = coin["creator"]
        parts.append(f"👤 creator `{c[:4]}…{c[-4:]}`")
    if calls:
        parts.append("📣 **calls**\n" + "\n".join(calls[:4]))
    if replies:
        parts.append("💬 **pump chat**\n" + "\n".join(replies[:4]))
    return ("\n".join(parts) or "quiet")[:1024]


def parse_trade_ts(ts):
    if not ts:
        return time.time()
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()


def whale_buys(pair, mint, min_usd, cutoff=None):
    if not pair:
        return []
    try:
        r = requests.get(
            f"{GECKO}/networks/solana/pools/{pair}/trades",
            params={"trade_volume_in_usd_greater_than": int(min_usd)},
            headers={"Accept": "application/json"},
            timeout=12,
        )
        if r.status_code != 200:
            return []
        rows = (r.json() or {}).get("data") or []
    except Exception:
        return []
    px = sol_px()
    supply = token_supply(mint)
    out = []
    for row in rows:
        attr = (row.get("attributes") or {}) if isinstance(row, dict) else {}
        if (attr.get("kind") or "").lower() != "buy":
            continue
        usd = float(attr.get("volume_in_usd") or 0)
        if usd < min_usd:
            continue
        tbuy = parse_trade_ts(attr.get("block_timestamp"))
        if cutoff and tbuy < cutoff - 120:
            continue
        tx = attr.get("tx_hash") or row.get("id")
        if not tx or tx in seen_trades:
            continue
        wallet = attr.get("tx_from_address") or ""
        from_amt = float(attr.get("from_token_amount") or 0)
        to_amt = float(attr.get("to_token_amount") or 0)
        from_px = float(attr.get("price_from_in_usd") or 0)
        sol = None
        if from_px and 50 <= from_px <= 800 and from_amt:
            sol = from_amt
        elif px:
            sol = usd / px
        pct = (to_amt / supply * 100) if supply and to_amt else None
        out.append(
            {
                "tx": tx,
                "wallet": wallet,
                "usd": usd,
                "sol": sol,
                "tokens": to_amt,
                "pct": pct,
                "name": KOL_BY_ADDR.get(wallet),
            }
        )
    return out


def sol_balance(addr):
    try:
        res = helius_rpc("getBalance", [addr]) or {}
        lamports = res.get("value") if isinstance(res, dict) else res
        return float(lamports or 0) / 1_000_000_000
    except Exception:
        return 0.0


def wallet_profile(addr):
    if not addr:
        return {}
    hit = wallet_cache.get(addr)
    if hit and time.time() - hit.get("t", 0) < 600:
        return hit
    sigs = helius_rpc_list(
        "getSignaturesForAddress",
        [addr, {"limit": 40}],
    )
    n = len(sigs)
    first_ts = None
    last_ts = None
    err = 0
    for s in sigs:
        if not isinstance(s, dict):
            continue
        if s.get("err"):
            err += 1
        ts = s.get("blockTime")
        if ts:
            last_ts = last_ts or ts
            first_ts = ts
    age_h = None
    if first_ts:
        age_h = max(0.0, (time.time() - first_ts) / 3600)
    recent = 0
    if last_ts and first_ts and n >= 2:
        span = max(1.0, last_ts - first_ts)
        recent = n / (span / 3600)
    bal = sol_balance(addr)
    tokens = 0
    try:
        accs = helius_rpc(
            "getTokenAccountsByOwner",
            [addr, {"programId": TOKEN_PROGRAM}, {"encoding": "jsonParsed"}],
        ) or {}
        tokens += len(accs.get("value") or [])
    except Exception:
        pass
    kind = "trader"
    score = 3
    if addr in KOL_BY_ADDR:
        kind = "KOL / trader"
        score = 5
    elif age_h is not None and age_h < 3 and n >= 20:
        kind = "vol bot"
        score = 1
    elif n >= 30 and recent >= 15:
        kind = "vol bot"
        score = 1
    elif tokens >= 20 and n >= 20:
        kind = "spray bot"
        score = 2
    elif age_h is not None and age_h < 6:
        kind = "fresh / likely bot"
        score = 2
    elif age_h is not None and age_h >= 24 * 14 and n >= 8 and tokens < 20:
        kind = "trader"
        score = 4
    if addr in KOL_BY_ADDR:
        label = "follow"
    elif score >= 4:
        label = "follow"
    elif score <= 2:
        label = "ignore"
    else:
        label = "mixed"
    age_s = "—"
    if age_h is not None:
        if age_h < 1:
            age_s = f"{int(age_h * 60)}m"
        elif age_h < 48:
            age_s = f"{age_h:.1f}h"
        else:
            age_s = f"{int(age_h / 24)}d"
    out = {
        "t": time.time(),
        "n": n,
        "err": err,
        "age": age_s,
        "sol": bal,
        "tokens": tokens,
        "kind": kind,
        "score": score,
        "label": label,
        "kol": KOL_BY_ADDR.get(addr),
    }
    wallet_cache[addr] = out
    return out


def grok_wallet(addr, prof, usd, ticker):
    if not XAI_API_KEY:
        return ""
    prompt = (
        "You grade Solana meme buyers.\n"
        "ONLY two classes: (1) automated vol/chart bot  (2) discretionary trader.\n"
        "Vol bot = high tx density, many token accounts, fresh wallet, "
        "mechanical size, here to paint volume or hold the chart up.\n"
        "Trader = older wallet, fewer bags, irregular timing, size looks chosen.\n"
        "Do not invent PnL. If facts are thin, say unknown and score 3.\n"
        f"wallet {addr}\n"
        f"buy ${usd} of ${ticker}\n"
        f"sampled_sigs {prof.get('n')} failed {prof.get('err')} "
        f"wallet_age {prof.get('age')} sol {prof.get('sol')} "
        f"token_accounts {prof.get('tokens')} "
        f"heuristic {prof.get('kind')} {prof.get('score')}/5 "
        f"kol {prof.get('kol') or 'no'}\n"
        "Output exactly 4 lines:\n"
        "1) X/5\n"
        "2) vol bot OR trader\n"
        "3) follow / ignore\n"
        "4) one sentence why"
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
                "temperature": 0.05,
            },
            timeout=25,
        )
        data = r.json() or {}
        return (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        )[:600]
    except Exception:
        return ""


def format_wallet_field(addr, prof, grok_txt):
    icon = "👑" if prof.get("kol") else ("🤖" if "bot" in str(prof.get("kind")) else "🧑‍💻")
    who = prof.get("kol") or prof.get("kind") or "wallet"
    stars = "⭐" * int(prof.get("score") or 0)
    line = (
        f"{icon} **{who}**   {stars}  `{prof.get('score')}/5`  ·  `{prof.get('label')}`\n"
        f"🎂 `{prof.get('age')}`   ◎ `{prof.get('sol'):.2f}` SOL   "
        f"🧾 `{prof.get('n')}` txs   🎒 `{prof.get('tokens')}`"
    )
    if grok_txt:
        line += "\n" + grok_txt
    return line[:1024]


def grok_report(ticker, name, mint, stats, dist, smarts, fomo):
    if not XAI_API_KEY:
        return "no xAI key"
    prompt = (
        f"Blunt 8-line Solana meme DD. No fluff.\n"
        f"Token ${ticker} {name}\nCA {mint}\n"
        f"MC {stats.get('mc')} liq {stats.get('liq')} vol24 {stats.get('vol24')} "
        f"age {stats.get('age')} chg24 {stats.get('chg24')} boosts {stats.get('boosts')}\n"
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


def venues_line(stats, mint):
    dex = stats.get("dex") or f"https://dexscreener.com/solana/{mint}"
    return (
        f"[Axiom](https://axiom.trade/t/{mint}) · "
        f"[Photon](https://photon-sol.tinyastro.io/en/lp/{mint}) · "
        f"[Dex]({dex}) · "
        f"[GMGN](https://gmgn.ai/sol/token/{mint}) · "
        f"[Pump](https://pump.fun/coin/{mint})"
    )


def kind_title(kind):
    return {
        "first scan": "🆕  first scan",
        "add": "➕  add",
        "Whale Purchase": "🐋  Whale Purchase",
        "surge": "📈  surge",
        "pump call": "📣  pump call",
    }.get(kind, kind)


def rick_embed(
    kind,
    ticker,
    name,
    mint,
    body,
    stats,
    color,
    dist_s="",
    smart_s="",
    fomo="",
    social="",
    extra_fields=None,
):
    dex = stats.get("dex") or f"https://dexscreener.com/solana/{mint}"
    chg = stats.get("chg24")
    chg_s = f"{chg:+.1f}%" if isinstance(chg, (int, float)) else "—"
    boost = stats.get("boosts") or 0
    boost_s = f"   🚀 `{boost}`" if boost else ""
    block = (
        f"`{mint}`\n"
        f"💰 `{money(stats.get('mc'))}`   💧 `{money(stats.get('liq'))}`   "
        f"📊 `{money(stats.get('vol24'))}`\n"
        f"⏱ 5m `{money(stats.get('vol5'))}`   💵 `{stats.get('price') or '—'}`   "
        f"📉 `{chg_s}`   ⏳ `{stats.get('age') or '—'}`{boost_s}"
    )
    desc = f"{block}\n\n{body}".strip()[:3900]
    fields = []
    if extra_fields:
        fields.extend(extra_fields)
    if fomo and fomo not in ("—",):
        fields.append({"name": "🔥 FOMO", "value": fomo, "inline": False})
    if social and social not in ("—",):
        fields.append({"name": "💬 Pump", "value": social, "inline": False})
    if dist_s and dist_s not in ("—", "live book"):
        fields.append({"name": "📊 Supply", "value": dist_s, "inline": False})
    if smart_s and smart_s not in ("—", "whale", "live book"):
        fields.append({"name": "👑 KOLs", "value": smart_s, "inline": False})
    fields.append({"name": "🔗 Trade", "value": venues_line(stats, mint), "inline": False})
    embed = {
        "author": {"name": "SKYZ  ·  scan"},
        "title": f"${ticker}   {kind_title(kind)}",
        "url": dex,
        "description": desc,
        "color": color,
        "footer": {"text": f"{name} · {mint[:6]}…{mint[-4:]}"},
        "fields": fields,
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


def add_watch(mint, stats=None, ticker=None, name=None):
    mint = (mint or "").strip()
    if not mint or mint in SKIP_MINTS:
        return
    stats = stats or dex_stats(mint)
    watch[mint] = {
        "ticker": ticker or stats.get("ticker") or "?",
        "name": name or stats.get("name") or ticker or "?",
        "pair": stats.get("pair"),
        "entry_px": float(stats.get("price") or 0) or None,
        "last_px": float(stats.get("price") or 0) or None,
        "t": time.time(),
    }


def process_buy(mint, sig=None):
    mint = (mint or "").strip()
    if not mint or mint in SKIP_MINTS:
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
    social = social_field(replies, calls, stats, coin)
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
            dist_s=dist_s,
            smart_s=smart_s,
            fomo=fomo,
            social=social,
        )
    )
    add_watch(mint, stats=stats, ticker=ticker, name=name)


def _swap_paid(swap):
    native_in = swap.get("nativeInput") or {}
    try:
        if int(native_in.get("amount") or 0) > 0:
            return True
    except Exception:
        pass
    for inn in swap.get("tokenInputs") or []:
        mint = inn.get("mint") or ""
        if mint not in SKIP_MINTS:
            continue
        amt = inn.get("tokenAmount")
        if amt is None:
            raw = inn.get("rawTokenAmount") or {}
            amt = raw.get("tokenAmount")
        try:
            if float(amt or 0) > 0:
                return True
        except Exception:
            continue
    return False


def extract_mints(payload):
    mints = []
    events = payload if isinstance(payload, list) else [payload]
    for ev in events:
        if not isinstance(ev, dict):
            continue
        payer = (ev.get("feePayer") or "").strip()
        if WALLET and payer and payer != WALLET:
            continue
        swap = (ev.get("events") or {}).get("swap") or {}
        if not swap or not _swap_paid(swap):
            continue
        for outt in swap.get("tokenOutputs") or []:
            mint = outt.get("mint")
            if mint and mint not in SKIP_MINTS:
                mints.append(mint)
    return list(dict.fromkeys(mints))


@app.get("/")
def home():
    return jsonify({"ok": True, "wallet": WALLET[-6:] if WALLET else None})


@app.get("/health")
def health():
    return jsonify({"ok": True, "watch": list(watch.keys())})


@app.get("/watch")
def watch_only():
    mint = request.args.get("mint") or request.args.get("ca")
    if not mint:
        return jsonify({"error": "pass ?mint="}), 400
    if mint in SKIP_MINTS:
        return jsonify({"error": "stable/wsol skipped", "mint": mint}), 400
    add_watch(mint)
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
    if mint in SKIP_MINTS:
        return jsonify({"error": "stable/wsol skipped", "mint": mint}), 400
    process_buy(mint)
    return jsonify({"ok": True, "mint": mint})


def format_whale(w):
    who = w.get("name") or "wallet"
    addr = w.get("wallet") or "?"
    sol = w.get("sol")
    sol_s = f"{sol:.2f} SOL" if sol else "—"
    pct = w.get("pct")
    if pct is None:
        pct_s = "unknown"
    elif pct >= 0.01:
        pct_s = f"{pct:.2f}%"
    else:
        pct_s = f"{pct:.4f}%"
    tx = w.get("tx") or ""
    return (
        f"🛒 **{who}** bought `{money(w.get('usd'))}`  ·  `{sol_s}`\n"
        f"📦 picked up `{pct_s}` of supply\n"
        f"`{addr}`\n"
        f"https://solscan.io/account/{addr}\n"
        + (f"https://solscan.io/tx/{tx}" if tx else "")
    )


def ping_whales(mint, pos, stats):
    if mint in SKIP_MINTS:
        return
    pair = pos.get("pair") or stats.get("pair")
    if stats.get("pair"):
        pos["pair"] = stats["pair"]
    if not pair:
        return
    ticker = pos.get("ticker") or stats.get("ticker") or "?"
    name = pos.get("name") or stats.get("name") or ticker
    calls = pump_callouts(mint)
    buys = whale_buys(pair, mint, WHALE_5M_USD, cutoff=pos.get("t"))
    for w in buys[:5]:
        seen_trades.add(w["tx"])
        if len(seen_trades) > 4000:
            seen_trades.clear()
        addr = w.get("wallet") or ""
        prof = wallet_profile(addr)
        gtxt = grok_wallet(addr, prof, w.get("usd"), ticker)
        extra = [
            {
                "name": "📦 This buy",
                "value": (
                    f"`{w['pct']:.4f}%` of supply"
                    if w.get("pct") is not None
                    else "—"
                ),
                "inline": False,
            },
            {
                "name": "🧠 Buyer",
                "value": format_wallet_field(addr, prof, gtxt) or "—",
                "inline": False,
            },
        ]
        discord_embed(
            rick_embed(
                "Whale Purchase",
                ticker,
                name,
                mint,
                format_whale(w),
                stats,
                0xF5C542,
                smart_s=w.get("name") or "",
                social=("\n".join(calls[:4]) if calls else ""),
                extra_fields=extra,
            )
        )


def poll_positions():
    while True:
        now = time.time()
        for mint, pos in list(watch.items()):
            if mint in SKIP_MINTS:
                watch.pop(mint, None)
                continue
            stats = dex_stats(mint)
            ping_whales(mint, pos, stats)
            px = float(stats.get("price") or 0) or None
            if not px:
                continue
            last = pos.get("last_px") or px
            entry = pos.get("entry_px") or px
            pos["last_px"] = px
            chg = ((px - last) / last * 100) if last else 0
            from_entry = ((px - entry) / entry * 100) if entry else 0
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
                        fomo="callout hit",
                        social="\n".join(calls[:5]),
                    )
                )
        time.sleep(POLL_SEC)


def start_poller():
    threading.Thread(target=poll_positions, daemon=True).start()


start_poller()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)