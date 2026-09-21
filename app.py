import json
import os
import threading
import time

import requests
from flask import Flask, jsonify, request
from dotenv import load_dotenv

load_dotenv()

WALLET = os.environ["WALLET"].strip()
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"].strip()
HELIUS_API_KEY = os.environ["HELIUS_API_KEY"].strip()
XAI_API_KEY = os.environ["XAI_API_KEY"].strip()
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.3")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
MIN_SOL = float(os.environ.get("MIN_SOL", "0.03"))
PORT = int(os.environ.get("PORT", "8080"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "20"))
SURGE_PCT = float(os.environ.get("SURGE_PCT", "12"))
WHALE_5M_USD = float(os.environ.get("WHALE_5M_USD", "2500"))

WSOL = "So11111111111111111111111111111111111111112"
IGNORE_MINTS = {
    WSOL,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB",
}
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

app = Flask(__name__)
seen = set()
grok_done = set()
watch = {}
last_whale_ping = {}

SYSTEM = """You are a blunt Solana memecoin desk analyst.
Write a Grok-style token diagnostic for a wallet that JUST bought.
Numbers over hopium. No long disclaimer.

Cover:
1. What it is (name, age, narrative guess, clone risk)
2. Live stats from the data packet
3. Contract / book risk if data exists; say UNKNOWN if missing
4. Holder profitability and supply concentration from the packet
5. Market-made / bundled / KOL-farm vs organic
6. Verdict: hold / trim / exit
7. Kill conditions

Do not invent holder % or wallet PnL if not in the packet.
Keep it under 500 words. Discord markdown. No links.
"""


def discord(content=None, embeds=None):
    payload = {"allowed_mentions": {"parse": []}}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)


def money(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"${n / 1_000:.1f}K"
    if abs(n) >= 1:
        return f"${n:.2f}"
    return f"${n:.6f}".rstrip("0")


def helius_rpc(method, params):
    r = requests.post(
        HELIUS_RPC,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=20,
    )
    r.raise_for_status()
    return (r.json() or {}).get("result")


def supply_distribution(mint):
    """Top holder % from on-chain largest accounts. Not tagged smart money."""
    out = {
        "top1": None,
        "top5": None,
        "top10": None,
        "top20": None,
        "rows": [],
        "error": None,
    }
    try:
        supply_res = helius_rpc("getTokenSupply", [mint]) or {}
        supply = float((supply_res.get("value") or {}).get("uiAmount") or 0)
        largest = helius_rpc("getTokenLargestAccounts", [mint]) or {}
        accs = (largest.get("value") or [])[:20]
        amounts = [float(a.get("uiAmount") or 0) for a in accs]
        addrs = [a.get("address") for a in accs if a.get("address")]
        owners = {}
        if addrs:
            info = helius_rpc(
                "getMultipleAccounts",
                [addrs, {"encoding": "jsonParsed"}],
            ) or {}
            for i, acc in enumerate(info.get("value") or []):
                if not acc:
                    continue
                parsed = ((acc.get("data") or {}).get("parsed") or {}).get("info") or {}
                owners[addrs[i]] = parsed.get("owner") or addrs[i]
        if supply > 0 and amounts:
            def pct(n):
                return round(sum(amounts[:n]) / supply * 100, 1)

            out["top1"] = pct(1)
            out["top5"] = pct(5)
            out["top10"] = pct(min(10, len(amounts)))
            out["top20"] = pct(min(20, len(amounts)))
            for i, a in enumerate(accs[:5]):
                owner = owners.get(a.get("address"), a.get("address") or "?")
                share = round(float(a.get("uiAmount") or 0) / supply * 100, 1)
                out["rows"].append({"wallet": owner, "pct": share})
    except Exception as e:
        out["error"] = str(e)
    return out


def smart_wallets(mint):
    """Best-effort GMGN top traders still holding with profit. Fail soft."""
    url = (
        f"https://gmgn.ai/vas/api/v1/token_traders"
        f"?chain=sol&address={mint}&order_by=profit&direction=desc&limit=8"
    )
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": f"https://gmgn.ai/sol/token/{mint}",
    }
    rows = []
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            url2 = f"https://gmgn.ai/defi/quotation/v1/tokens/top_traders/sol/{mint}"
            r = requests.get(url2, headers=headers, timeout=15)
        data = r.json() if r.status_code == 200 else {}
        traders = (
            data.get("data")
            or (data.get("data") or {}).get("list")
            or data.get("list")
            or []
        )
        if isinstance(traders, dict):
            traders = traders.get("list") or traders.get("traders") or []
        for t in traders[:6]:
            if not isinstance(t, dict):
                continue
            addr = (
                t.get("address")
                or t.get("wallet_address")
                or t.get("account")
                or ""
            )
            pnl = t.get("profit") or t.get("realized_profit") or t.get("pnl")
            hold = t.get("unrealized_profit") or t.get("hold")
            bal_pct = (
                t.get("amount_percentage")
                or t.get("token_percentage")
                or t.get("share")
            )
            try:
                pnl_n = float(pnl)
            except (TypeError, ValueError):
                pnl_n = None
            if pnl_n is not None and pnl_n <= 0:
                continue
            if not addr:
                continue
            rows.append(
                {
                    "wallet": addr,
                    "pnl": pnl_n,
                    "hold": hold,
                    "pct": bal_pct,
                    "tag": ",".join(t.get("tags") or t.get("tag") or [])
                    if isinstance(t.get("tags") or t.get("tag"), list)
                    else (t.get("tag") or ""),
                }
            )
    except Exception:
        return []
    return rows[:5]


def book_lines(dist, smarts):
    d1 = f"{dist.get('top1')}%" if dist.get("top1") is not None else "—"
    d5 = f"{dist.get('top5')}%" if dist.get("top5") is not None else "—"
    d10 = f"{dist.get('top10')}%" if dist.get("top10") is not None else "—"
    d20 = f"{dist.get('top20')}%" if dist.get("top20") is not None else "—"
    dist_s = f"T1 `{d1}`  T5 `{d5}`  T10 `{d10}`  T20 `{d20}`"
    if dist.get("rows"):
        tops = "\n".join(
            f"`{r['wallet'][:4]}…{r['wallet'][-4:]}`  {r['pct']}%"
            for r in dist["rows"][:4]
        )
        dist_s += f"\n{tops}"

    if smarts:
        smart_s = "\n".join(
            f"`{s['wallet'][:4]}…{s['wallet'][-4:]}`  PnL {money(s['pnl'])}"
            + (f"  {s['tag']}" if s.get("tag") else "")
            for s in smarts
        )
    else:
        smart_s = "no live PnL feed (GMGN blocked). open GMGN holders."
    return dist_s[:1024], smart_s[:1024]


def rick_embed(kind, ticker, name, mint, body, stats, color, dist=None, smarts=None):
    title_name = name or ticker or mint[:6]
    px = stats.get("priceUsd") or "—"
    chg1 = stats.get("chg1")
    try:
        chg_n = float(chg1)
    except (TypeError, ValueError):
        chg_n = None
    if chg_n is None:
        arrow, chg_s = "•", "—"
    elif chg_n >= 0:
        arrow, chg_s, color = "▲", f"+{chg_n:.1f}%", 0x3BA55C
    else:
        arrow, chg_s, color = "▼", f"{chg_n:.1f}%", 0xED4245

    pair = stats.get("pairUrl") or f"https://dexscreener.com/solana/{mint}"
    axiom = f"https://axiom.trade/t/{mint}"
    photon = f"https://photon-sol.tinyastro.io/en/lp/{mint}"
    gmgn = f"https://gmgn.ai/sol/token/{mint}"
    pf = f"https://pump.fun/{mint}"
    dex = (stats.get("dex") or "dex").lower()
    age = stats.get("ageHours")
    age_s = f"{age}h" if age is not None else "—"
    dist_s, smart_s = book_lines(dist or {}, smarts or [])

    embed = {
        "author": {"name": f"SKYZ  ·  {kind}  ·  {dex}"},
        "title": f"${ticker}" if ticker else title_name,
        "url": pair,
        "description": f"`{mint}`",
        "color": color,
        "fields": [
            {"name": "USD", "value": f"`{px}`", "inline": True},
            {
                "name": "MC",
                "value": f"**{money(stats.get('mc') or stats.get('fdv'))}**",
                "inline": True,
            },
            {"name": "Liq", "value": f"**{money(stats.get('liq'))}**", "inline": True},
            {
                "name": "Vol 24h",
                "value": f"**{money(stats.get('vol24'))}**",
                "inline": True,
            },
            {"name": "Age", "value": f"**{age_s}**", "inline": True},
            {
                "name": "1H",
                "value": f"**{money(stats.get('vol1'))}**  {arrow} {chg_s}",
                "inline": True,
            },
            {
                "name": "Flow",
                "value": (
                    f"buys `{stats.get('buys1') or stats.get('buys24') or '—'}`   "
                    f"sells `{stats.get('sells1') or stats.get('sells24') or '—'}`"
                ),
                "inline": False,
            },
            {"name": "Supply", "value": dist_s, "inline": False},
            {"name": "Smart in", "value": smart_s, "inline": False},
            {
                "name": "Trade",
                "value": (
                    f"[AXI]({axiom})   [PHO]({photon})   [DEX]({pair})   "
                    f"[GMGN]({gmgn})   [PF]({pf})"
                ),
                "inline": False,
            },
        ],
        "footer": {"text": title_name},
    }
    if body:
        embed["fields"].append(
            {"name": "Desk", "value": str(body)[:1024], "inline": False}
        )
    if stats.get("image"):
        embed["thumbnail"] = {"url": stats["image"]}
    return embed


def dex_stats(mint):
    r = requests.get(f"https://api.dexscreener.com/tokens/v1/solana/{mint}", timeout=15)
    r.raise_for_status()
    pairs = r.json() or []
    if not pairs:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=15
        )
        pairs = (r.json() or {}).get("pairs") or []
    sol = [p for p in pairs if p.get("chainId") == "solana"] or pairs
    sol.sort(
        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True
    )
    p = sol[0] if sol else {}
    base = p.get("baseToken") or {}
    created = p.get("pairCreatedAt")
    age_h = None
    if created:
        age_h = (time.time() * 1000 - created) / 3_600_000
    vol = p.get("volume") or {}
    tx = p.get("txns") or {}
    chg = p.get("priceChange") or {}
    info = p.get("info") or {}
    return {
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "priceUsd": p.get("priceUsd"),
        "mc": p.get("marketCap") or p.get("fdv"),
        "fdv": p.get("fdv"),
        "liq": (p.get("liquidity") or {}).get("usd"),
        "vol24": vol.get("h24"),
        "vol5": vol.get("m5"),
        "vol1": vol.get("h1"),
        "buys5": (tx.get("m5") or {}).get("buys"),
        "sells5": (tx.get("m5") or {}).get("sells"),
        "buys1": (tx.get("h1") or {}).get("buys"),
        "sells1": (tx.get("h1") or {}).get("sells"),
        "buys24": (tx.get("h24") or {}).get("buys"),
        "sells24": (tx.get("h24") or {}).get("sells"),
        "chg1": chg.get("h1"),
        "chg24": chg.get("h24"),
        "dex": p.get("dexId"),
        "image": info.get("imageUrl"),
        "pairUrl": p.get("url"),
        "ageHours": round(age_h, 2) if age_h is not None else None,
        "pairCreatedAt": created,
        "rawPairCount": len(sol),
    }


def helius_asset(mint):
    r = requests.post(
        HELIUS_RPC,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAsset",
            "params": {"id": mint},
        },
        timeout=15,
    )
    r.raise_for_status()
    return (r.json() or {}).get("result") or {}


def extract_buys(payload):
    txs = payload if isinstance(payload, list) else [payload]
    buys = []
    for tx in txs:
        if tx.get("transactionError") is not None or tx.get("error"):
            continue
        sig = tx.get("signature")
        fee_payer = tx.get("feePayer")
        desc = tx.get("description") or ""
        received = []
        spent_sol = 0.0
        for t in tx.get("tokenTransfers") or []:
            mint = t.get("mint")
            to_ = t.get("toUserAccount")
            from_ = t.get("fromUserAccount")
            amt = float(t.get("tokenAmount") or 0)
            if mint == WSOL and from_ == WALLET:
                spent_sol += amt
            if to_ == WALLET and mint and mint not in IGNORE_MINTS and amt > 0:
                received.append({"mint": mint, "amount": amt})
        for n in tx.get("nativeTransfers") or []:
            if n.get("fromUserAccount") == WALLET:
                spent_sol += float(n.get("amount") or 0) / 1_000_000_000
        if not received:
            for acc in tx.get("accountData") or []:
                if acc.get("account") != WALLET:
                    continue
                for ch in acc.get("tokenBalanceChanges") or []:
                    mint = ch.get("mint")
                    raw = float(ch.get("rawTokenAmount", {}).get("tokenAmount") or 0)
                    dec = int(ch.get("rawTokenAmount", {}).get("decimals") or 0)
                    if mint and mint not in IGNORE_MINTS and raw > 0:
                        received.append(
                            {"mint": mint, "amount": raw / (10 ** dec if dec else 1)}
                        )
        if fee_payer != WALLET and WALLET not in desc and not received:
            continue
        if spent_sol > 0 and spent_sol < MIN_SOL:
            continue
        for rec in received:
            buys.append(
                {
                    "signature": sig,
                    "mint": rec["mint"],
                    "tokenAmount": rec["amount"],
                    "solSpent": spent_sol,
                    "description": desc,
                    "source": tx.get("source"),
                    "type": tx.get("type"),
                    "timestamp": tx.get("timestamp"),
                }
            )
    return buys


def grok_report(buy, stats, asset, dist, smarts):
    packet = {
        "wallet": WALLET,
        "buy": buy,
        "dexscreener": stats,
        "distribution": dist,
        "smartWallets": smarts,
        "heliusAsset": {
            "id": asset.get("id"),
            "content": (asset.get("content") or {}).get("metadata"),
            "token_info": asset.get("token_info"),
            "ownership": asset.get("ownership"),
            "authorities": asset.get("authorities"),
        },
    }
    r = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": XAI_MODEL,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": "Wallet just bought this token. Write the diagnostic.\n\n"
                    + json.dumps(packet, default=str)[:14000],
                },
            ],
        },
        timeout=90,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def remember(mint, stats):
    watch[mint] = {
        "ticker": stats.get("symbol") or mint[:6],
        "last_px": float(stats.get("priceUsd") or 0),
        "last_mc": float(stats.get("mc") or 0),
        "added": time.time(),
    }


def process_buy(buy):
    key = f"{buy['signature']}:{buy['mint']}"
    if key in seen:
        return
    seen.add(key)
    mint = buy["mint"]
    try:
        stats = dex_stats(mint)
    except Exception as e:
        stats = {"error": str(e)}
    try:
        asset = helius_asset(mint)
    except Exception as e:
        asset = {"error": str(e)}
    dist = supply_distribution(mint)
    smarts = smart_wallets(mint)

    remember(mint, stats)
    ticker = stats.get("symbol") or mint[:6]
    name = stats.get("name") or ticker

    report = None
    if mint not in grok_done:
        try:
            report = grok_report(buy, stats, asset, dist, smarts)
            grok_done.add(mint)
        except Exception:
            report = None

    body = report[:900] if report else ""
    embed = rick_embed(
        "new bag", ticker, name, mint, body, stats, 0x5865F2, dist, smarts
    )
    discord(embeds=[embed])


def handle_payload(payload):
    for buy in extract_buys(payload):
        process_buy(buy)


def poll_positions():
    while True:
        time.sleep(POLL_SECONDS)
        now = time.time()
        for mint, pos in list(watch.items()):
            try:
                s = dex_stats(mint)
            except Exception:
                continue
            px = float(s.get("priceUsd") or 0)
            mc = float(s.get("mc") or 0)
            vol5 = float(s.get("vol5") or 0)
            last = pos.get("last_px") or 0
            ticker = pos.get("ticker") or s.get("symbol") or mint[:6]

            if last and px:
                chg = (px - last) / last * 100
                if abs(chg) >= SURGE_PCT:
                    embed = rick_embed(
                        "surge" if chg > 0 else "dump",
                        ticker,
                        s.get("name") or ticker,
                        mint,
                        f"{chg:+.1f}% since last tick",
                        s,
                        0x3BA55C if chg > 0 else 0xED4245,
                    )
                    discord(embeds=[embed])

            last_w = last_whale_ping.get(mint, 0)
            if vol5 >= WHALE_5M_USD and now - last_w > 90:
                last_whale_ping[mint] = now
                embed = rick_embed(
                    "size on tape",
                    ticker,
                    s.get("name") or ticker,
                    mint,
                    f"5m vol {money(vol5)} · buys {s.get('buys5')}",
                    s,
                    0xFEE75C,
                )
                discord(embeds=[embed])

            pos["last_px"] = px
            pos["last_mc"] = mc
            pos["ticker"] = ticker


@app.get("/")
def health():
    return jsonify({"ok": True, "wallet": WALLET, "watching": list(watch.keys())})


@app.post("/helius")
def helius():
    if WEBHOOK_SECRET:
        got = request.headers.get("Authorization") or ""
        if got != WEBHOOK_SECRET and got != f"Bearer {WEBHOOK_SECRET}":
            return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(force=True, silent=True) or []
    threading.Thread(target=handle_payload, args=(payload,), daemon=True).start()
    return jsonify({"ok": True})


@app.post("/test")
def test():
    body = request.get_json(force=True) or {}
    mint = body.get("mint")
    if not mint:
        return jsonify({"error": "mint required"}), 400
    buy = {
        "signature": body.get("signature", "manual-test"),
        "mint": mint,
        "tokenAmount": body.get("tokenAmount", 0),
        "solSpent": float(body.get("solSpent", 0.5)),
        "description": "manual test",
        "source": "TEST",
        "type": "SWAP",
        "timestamp": int(time.time()),
    }
    threading.Thread(target=process_buy, args=(buy,), daemon=True).start()
    return jsonify({"ok": True})


@app.post("/watch")
def add_watch():
    body = request.get_json(force=True) or {}
    mint = body.get("mint")
    if not mint:
        return jsonify({"error": "mint required"}), 400
    try:
        s = dex_stats(mint)
    except Exception:
        s = {}
    remember(mint, s)
    return jsonify({"ok": True, "watching": list(watch.keys())})


if __name__ == "__main__":
    threading.Thread(target=poll_positions, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)