#!/usr/bin/env python3
# Find the least-popular EDHREC commanders (as-commander) with rich filtering.
# Requires: aiohttp (pip install aiohttp)

import asyncio
import aiohttp
import sys
import re
import heapq
import argparse
from dataclasses import dataclass
from typing import Optional, List, Dict
from datetime import datetime, timedelta, UTC

# --------------------
# Config & constants
# --------------------

SCRYFALL_QUERY = 't:legendary type:creature legal:commander game:paper'
SCRYFALL_API = 'https://api.scryfall.com/cards/search'
HEADERS = {
    "User-Agent": "LeastPopularCommanders/1.5 (contact: your_email@example.com)"
}
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

DEFAULT_CONCURRENCY = 8
DEFAULT_DELAY_SEC = 0.15
DEFAULT_BOTTOM_K = 20  # bottom 20 by default

DECKS_RE = re.compile(r'(\d[\d,]*)\s+decks', re.I)

# Ability markers
PARTNER_MARKERS = ("partner with", "partner", "friends forever")
BACKGROUND_MARKER = "choose a background"
COMPANION_MARKER = "companion"
DOCTORS_COMPANION_MARKERS = ("doctor's companion", "doctor’s companion")

# --------------------
# Data structures
# --------------------

@dataclass
class Commander:
    name: str
    edhrec_route_url: str

@dataclass
class Result:
    name: str
    edhrec_url: str
    decks: Optional[int]
    error: Optional[str] = None

# --------------------
# Scryfall helpers
# --------------------

async def fetch_all_scryfall(session: aiohttp.ClientSession) -> List[Dict]:
    cards = []
    params = {"q": SCRYFALL_QUERY, "unique": "cards", "order": "name", "dir": "asc"}
    url = SCRYFALL_API
    while True:
        async with session.get(url, params=params) as r:
            r.raise_for_status()
            data = await r.json()
        cards.extend(data["data"])
        if not data.get("has_more"):
            break
        url = data["next_page"]
        params = None  # next_page already encodes params
        await asyncio.sleep(0.05)
    return cards

def is_commander_face(card: Dict) -> bool:
    tl = card.get("type_line", "")
    return ("Legendary" in tl) and ("Creature" in tl)

def commander_key(card: Dict) -> str:
    # collapse reprints by oracle_id where possible
    return card.get("oracle_id") or card.get("id")

def edhrec_route_url(card: Dict) -> str:
    related = card.get("related_uris") or {}
    if "edhrec" in related:
        return related["edhrec"]
    from urllib.parse import quote_plus
    return f'https://edhrec.com/route/?cc={quote_plus(card["name"])}'

def collapse_by_oracle(cards: List[Dict]) -> List[Dict]:
    by_oracle: Dict[str, Dict] = {}
    for c in cards:
        if not is_commander_face(c):
            continue
        k = commander_key(c)
        if k not in by_oracle:
            by_oracle[k] = c
    return list(by_oracle.values())

# --------------------
# Filter predicates
# --------------------

def is_funny_set(card: Dict) -> bool:
    # Unfinity/Unstable/Unhinged/Unglued, Mystery Booster Playtest, etc.
    return (card.get("set_type") == "funny")

def has_partner(card: Dict) -> bool:
    text = (card.get("oracle_text") or "").lower()
    return any(m in text for m in PARTNER_MARKERS)

def has_background_ability(card: Dict) -> bool:
    text = (card.get("oracle_text") or "").lower()
    return BACKGROUND_MARKER in text

def has_companion(card: Dict) -> bool:
    text = (card.get("oracle_text") or "").lower()
    return COMPANION_MARKER in text

def has_doctors_companion(card: Dict) -> bool:
    text = (card.get("oracle_text") or "").lower()
    return any(m in text for m in DOCTORS_COMPANION_MARKERS)

def is_vanilla(card: Dict) -> bool:
    # No rules text -> vanilla (keywords like Flying appear in oracle_text, so they won't be counted as vanilla)
    text = (card.get("oracle_text") or "").strip()
    return len(text) == 0

def is_recent(card: Dict, days: int) -> bool:
    """Return True if this printing's release date is within 'days' of today (UTC).
       Note: a recent reprint can mark an old commander as 'recent' in fast mode.
    """
    if days <= 0:
        return False
    ra = card.get("released_at")
    if not ra:
        return False
    try:
        rel_date = datetime.strptime(ra, "%Y-%m-%d").date()
    except Exception:
        return False
    today_utc = datetime.now(UTC).date()
    return (today_utc - rel_date) <= timedelta(days=days)

def is_doctor(card: Dict) -> bool:
    """Exclude the Doctors themselves (e.g., The Tenth/Eleventh/Fifteenth Doctor, promos included).
       Match any set_name containing 'Doctor Who' (covers promos) AND require 'Time Lord' in type_line,
       to avoid false positives.
    """
    tl = (card.get("type_line") or "").lower()
    set_name = (card.get("set_name") or "").lower()
    return ("doctor who" in set_name) and ("time lord" in tl)

def looks_like_ptk_fast(card: Dict) -> bool:
    """Fast heuristic: representative printing is PTK (may miss later reprints)."""
    set_code = (card.get("set") or "").lower()
    set_name = (card.get("set_name") or "").lower()
    return set_code == "ptk" or "portal three kingdoms" in set_name

async def is_ptk_strict(session: aiohttp.ClientSession, card: Dict) -> bool:
    """Accurate but slower: checks all printings for set 'ptk'."""
    uri = card.get("prints_search_uri")
    if not uri:
        return looks_like_ptk_fast(card)
    try:
        while uri:
            async with session.get(uri) as r:
                r.raise_for_status()
                data = await r.json()
            for printing in data.get("data", []):
                if (printing.get("set") or "").lower() == "ptk":
                    return True
            uri = data.get("next_page")
            await asyncio.sleep(0.02)
    except Exception:
        # fall back to fast heuristic on error
        return looks_like_ptk_fast(card)
    return False

# --------------------
# Apply filters (async for PTK strict)
# --------------------

async def apply_filters_async(
    session: aiohttp.ClientSession,
    pool: List[Dict],
    *,
    exclude_funny=True,
    exclude_partner=True,
    exclude_background=True,
    exclude_companion=True,
    exclude_doctors_companion=True,
    exclude_vanilla=True,
    exclude_recent=True,
    recent_days=90,
    exclude_ptk=True,
    ptk_strict=False,
    exclude_doctors=True,
    concurrency: int = 8
) -> List[Dict]:
    # First pass: cheap synchronous filters
    prelim: List[Dict] = []
    for c in pool:
        if exclude_funny and is_funny_set(c):               continue
        if exclude_partner and has_partner(c):              continue
        if exclude_background and has_background_ability(c):continue
        if exclude_companion and has_companion(c):          continue
        if exclude_doctors_companion and has_doctors_companion(c): continue
        if exclude_vanilla and is_vanilla(c):               continue
        if exclude_recent and is_recent(c, recent_days):    continue
        if exclude_doctors and is_doctor(c):                continue
        prelim.append(c)

    # PTK filter
    if not exclude_ptk:
        return prelim

    # Fast mode: use heuristic only
    if not ptk_strict:
        return [c for c in prelim if not looks_like_ptk_fast(c)]

    # Strict mode: check all printings, limited concurrency
    out: List[Dict] = []
    sem = asyncio.Semaphore(max(2, concurrency))

    async def check(c):
        await sem.acquire()
        try:
            keep = not await is_ptk_strict(session, c)
            return c if keep else None
        finally:
            sem.release()

    tasks = [check(c) for c in prelim]
    for fut in asyncio.as_completed(tasks):
        kept = await fut
        if kept is not None:
            out.append(kept)
    return out

# --------------------
# EDHREC fetch & reduce
# --------------------

def extract_deck_count(html: str) -> Optional[int]:
    m = DECKS_RE.search(html)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))

async def fetch_deck_count(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    cmdr: Commander,
    delay: float
) -> Result:
    await sem.acquire()
    try:
        async with session.get(cmdr.edhrec_route_url, allow_redirects=True) as r:
            final_url = str(r.url)
            html = await r.text()
        decks = extract_deck_count(html)
        return Result(name=cmdr.name, edhrec_url=final_url, decks=decks)
    except Exception as e:
        return Result(name=cmdr.name, edhrec_url=cmdr.edhrec_route_url, decks=None, error=str(e))
    finally:
        await asyncio.sleep(delay)
        sem.release()

# --------------------
# Main
# --------------------

async def main_async(args):
    async with aiohttp.ClientSession(headers=HEADERS, timeout=REQUEST_TIMEOUT) as session:
        sys.stderr.write("Fetching commander pool from Scryfall…\n")
        cards = await fetch_all_scryfall(session)
        pool = collapse_by_oracle(cards)

        # Show recent cutoff info (for visibility)
        if not args.include_recent and args.recent_days > 0:
            cutoff_date = (datetime.now(UTC).date() - timedelta(days=args.recent_days)).isoformat()
            sys.stderr.write(f"Excluding commanders released after: {cutoff_date} (last {args.recent_days} days)\n")

        pool = await apply_filters_async(
            session,
            pool,
            exclude_funny=not args.include_funny_sets,
            exclude_partner=not args.include_partners,
            exclude_background=not args.include_backgrounds,
            exclude_companion=not args.include_companions,
            exclude_doctors_companion=not args.include_doctors_companions,
            exclude_vanilla=not args.include_vanilla,
            exclude_recent=not args.include_recent,
            recent_days=args.recent_days,
            exclude_ptk=not args.include_ptk,
            ptk_strict=args.ptk_strict,
            exclude_doctors=not args.include_doctors,
            concurrency=max(2, args.concurrency // 2)
        )
        sys.stderr.write(f"After filters, {len(pool)} commanders remain.\n")

        sem = asyncio.Semaphore(args.concurrency)
        commanders = [Commander(name=c["name"], edhrec_route_url=edhrec_route_url(c)) for c in pool]

        big = 10**12  # sentinel for None deck counts (when not showing errors)
        heap = []
        seq = 0

        tasks = [fetch_deck_count(session, sem, cmdr, args.delay) for cmdr in commanders]
        processed = 0

        for coro in asyncio.as_completed(tasks):
            res = await coro
            val = res.decks if isinstance(res.decks, int) else (big if not args.include_errors else 0)

            # Optional: skip zeros if requested
            if args.only_positive and isinstance(res.decks, int) and res.decks <= 0:
                pass
            else:
                # Maintain a max-heap of size K by deck count (keep the K smallest)
                heapq.heappush(heap, (-val, seq, res))
                seq += 1
                if len(heap) > args.bottom_k:
                    heapq.heappop(heap)

            processed += 1
            if processed % 50 == 0:
                sys.stderr.write(f"…processed {processed}/{len(tasks)}\n")

        bottom = [t[2] for t in heap]
        bottom.sort(key=lambda r: (r.decks if r.decks is not None else big, r.name))
        display = [r for r in bottom if (r.decks is not None) or args.include_errors]

        if args.csv:
            import csv
            with open(args.csv, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["decks", "name", "edhrec_url", "error"])
                for r in display:
                    w.writerow([r.decks if r.decks is not None else "", r.name, r.edhrec_url, r.error or ""])
            print(f"Saved CSV to {args.csv}")
        else:
            print("\n=== Least-popular commanders on EDHREC (as-commander) ===")
            if display:
                cutoff = display[-1].decks if display[-1].decks is not None else 0
                print(f"(Bottom {args.bottom_k}; cutoff ≈ {cutoff} decks; filters active)\n")
            for r in display:
                decks_str = str(r.decks) if r.decks is not None else "?"
                tail = f" — ERROR: {r.error}" if r.error and args.include_errors else ""
                print(f"{decks_str:>6}  —  {r.name}  —  {r.edhrec_url}{tail}")

def parse_args():
    p = argparse.ArgumentParser(description="Least-popular EDHREC commanders (as-commander), with robust filters.")
    # Output / performance
    p.add_argument("--bottom-k", type=int, default=DEFAULT_BOTTOM_K, dest="bottom_k",
                   help="How many least-popular to keep (ties may not fully expand). Default: 20")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help="Concurrent EDHREC requests. Default: 8")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY_SEC,
                   help="Per-request delay (seconds). Default: 0.15")
    p.add_argument("--csv", type=str, default=None,
                   help="If set, write results to CSV at this path.")
    p.add_argument("--only-positive", action="store_true",
                   help="Exclude commanders with 0 decks.")
    p.add_argument("--include-errors", action="store_true",
                   help="Show entries that failed to fetch (as '? decks').")

    # Filters (defaults EXCLUDE these categories)
    p.add_argument("--include-partners", action="store_true",
                   help="Include Partner / Partner With / Friends Forever commanders.")
    p.add_argument("--include-backgrounds", action="store_true",
                   help="Include commanders with 'Choose a Background'.")
    p.add_argument("--include-companions", action="store_true",
                   help="Include commanders with the Companion ability.")
    p.add_argument("--include-doctors-companions", action="store_true",
                   help="Include commanders with the “Doctor’s companion” mechanic.")
    p.add_argument("--include-funny-sets", action="store_true",
                   help="Include sets with set_type='funny' (Un-sets/playtest).")
    p.add_argument("--include-vanilla", action="store_true",
                   help="Include commanders with no rules text (vanilla).")
    p.add_argument("--include-ptk", action="store_true",
                   help="Include Portal Three Kingdoms (PTK) commanders.")
    p.add_argument("--ptk-strict", action="store_true",
                   help="Accurately detect PTK by scanning all printings (slower).")
    p.add_argument("--include-doctors", action="store_true",
                   help="Include the Doctors themselves (Time Lord) from any Doctor Who set/promos.")

    # Recent filter: switch + adjustable days
    p.add_argument("--include-recent", action="store_true",
                   help="Include commanders printed in the last N days (see --recent-days).")
    p.add_argument("--recent-days", type=int, default=90,
                   help="Day window for 'recent' commander exclusion (default 90). Set 0 to disable recency logic.")

    return p.parse_args()

def main():
    args = parse_args()
    asyncio.run(main_async(args))

if __name__ == "__main__":
    main()
