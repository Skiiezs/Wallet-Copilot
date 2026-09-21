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
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.6")
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
4. Holder profitability: early vs late cohort given age + vol/MC
5. Market-made / bundled / KOL-farm vs organic
6. Verdict: hold / trim / exit
7. Kill conditions

Do not invent holder % or dev % if not in the packet.
Keep it under 700 words. Discord markdown. No links.
"""


def discord(content=None, embeds=None):
    payload = {"allowed_mentions": {"parse": []}}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)


def card(title, desc, fields, color=0x57F287):
    return {
        "title": title,
        "description": desc,
        "color": color,
        "fields": [{"name": n, "value": str(v)[:1024], "inline": True} for n, v in fields],
    }

    def money(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"${n/1_000:.1f}K"
    if n >= 1:
        return f"${n:.2f}"
    return f"${n:.6f}".rstrip("0")


def rick_embed(kind, ticker, name, mint, body, stats, color):
    px = stats.get("priceUsd") or "—"
    chg = stats.get("chg24")
    chg_s = f"{float(chg):+.1f}%" if chg is not None else "—"
    desc = (
        f"**{name or ticker}**\n"
        f"`{mint}`\n"
        f"```\n"
        f"MC {money(stats.get('mc')):<10} Liq {money(stats.get('liq'))}\n"
        f"Vol {money(stats.get('vol24')):<9}  5m {money(stats.get('vol5'))}\n"
        f"Px  ${px}   24h {chg_s}\n"
        f"Age {stats.get('ageHours') or '—'}h   {stats.get('dex') or '—'}\n"
        f"```\n"
        f"{body}"
    )
    axiom = f"https://axiom.trade/t/{mint}"
    photon = f"https://photon-sol.tinyastro.io/en/lp/{mint}"
    dex = stats.get("pairUrl") or f"https://dexscreener.com/solana/{mint}"
    gmgn = f"https://gmgn.ai/sol/token/{mint}"
    embed = {
        "author": {"name": "SKYZ  ·  scan"},
        "title": f"${ticker}   {kind}",
        "url": dex,
        "description": desc[:3900],
        "color": color,
        "footer": {"text": "axiom  ·  photon  ·  dex  ·  gmgn"},
        "fields": [
            {
                "name": "venues",
                "value": f"[Axiom]({axiom}) · [Photon]({photon}) · [Dex]({dex}) · [GMGN]({gmgn})",
                "inline": False,
            }
        ],
    }
    if stats.get("image"):
        embed["thumbnail"] = {"url": stats["image"]}
    return embed


def dex_stats(mint):
    r = requests.get(f"https://api.dexscreener.com/tokens/v1/solana/{mint}", timeout=15)
    r.raise_for_status()
    pairs = r.json() or []
    if not pairs:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=15)
        pairs = (r.json() or {}).get("pairs") or []
    sol = [p for p in pairs if p.get("chainId") == "solana"] or pairs
    sol.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    p = sol[0] if sol else {}
    base = p.get("baseToken") or {}
    created = p.get("pairCreatedAt")
    age_h = None
    if created:
        age_h = (time.time() * 1000 - created) / 3_600_000
    vol = p.get("volume") or {}
    tx = p.get("txns") or {}
        info = p.get("info") or {}
    chg = (p.get("priceChange") or {}).get("h24")
    return {
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "priceUsd": p.get("priceUsd"),
        "mc": p.get("marketCap") or p.get("fdv"),
        "fdv": p.get("fdv"),
        "liq": (p.get("liquidity") or {}).get("usd"),
        "vol24": vol.get("h24"),
        "vol5": vol.get("m5"),
        "buys5": (tx.get("m5") or {}).get("buys"),
        "sells5": (tx.get("m5") or {}).get("sells"),
        "buys24": (tx.get("h24") or {}).get("buys"),
        "sells24": (tx.get("h24") or {}).get("sells"),
        "dex": p.get("dexId"),
        "pairUrl": p.get("url"),
        "ageHours": round(age_h, 2) if age_h is not None else None,
        "pairCreatedAt": created,
        "rawPairCount": len(sol),
        "image": info.get("imageUrl"),
        "chg24": chg,
    }


def helius_asset(mint):
    r = requests.post(
        f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
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


def grok_report(buy, stats, asset):
    packet = {
        "wallet": WALLET,
        "buy": buy,
        "dexscreener": stats,
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
                    + json.dumps(packet, default=str)[:12000],
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

    remember(mint, stats)
    ticker = stats.get("symbol") or mint[:6]

    report = None
    if mint not in grok_done:
        try:
            report = grok_report(buy, stats, asset)
            grok_done.add(mint)
        except Exception:
            report = None

    desc = report[:1800] if report else "Tape only. Grok already used on this CA or API failed."
    if mint in grok_done and report is None and key.endswith("manual-test"):
        desc = "Tape only. Grok API failed."
    embed = card(
        f"${ticker}  ·  new bag",
        f"`{mint}`\n{desc}",
        [
            ("Spent", f"{buy.get('solSpent', 0):.3f} SOL"),
            ("MC", f"${stats.get('mc')}"),
            ("Liq", f"${stats.get('liq')}"),
            ("Vol 24h", f"${stats.get('vol24')}"),
            ("Age", f"{stats.get('ageHours')}h"),
            ("Dex", stats.get("dex") or "—"),
        ],
    )
       name = stats.get("name") or ticker
    body = report[:1600] if report else "_tape only_"
    embed = rick_embed("new bag", ticker, name, mint, body, stats, 0x00C2A8)
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
                    color = 0x57F287 if chg > 0 else 0xED4245
                    kind = "surge" if chg > 0 else "dump"
                    discord(
                        embeds=[
                            card(
                                f"${ticker}  ·  {kind}",
                                f"`{mint}`",
                                [
                                    ("Move", f"{chg:+.1f}%"),
                                    ("MC", f"${mc:,.0f}" if mc else "—"),
                                    ("Px", f"${px}"),
                                    ("5m vol", f"${vol5:,.0f}"),
                                ],
                                color=color,
                            )
                        ]
                    )

            last_w = last_whale_ping.get(mint, 0)
            if vol5 >= WHALE_5M_USD and now - last_w > 90:
                last_whale_ping[mint] = now
                discord(
                    embeds=[
                        card(
                            f"${ticker}  ·  size on tape",
                            f"`{mint}`\n5m volume ${vol5:,.0f} · buys {s.get('buys5')}",
                            [
                                ("MC", f"${mc:,.0f}" if mc else "—"),
                                ("Px", f"${px}"),
                            ],
                            color=0xFEE75C,
                        )
                    ]
                )

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