# edh-anti-meta

This script queries [Scryfall](https://scryfall.com) and [EDHREC](https://edhrec.com) to find the **least-popular commanders** with filtering options.

## Get Started:
### 1. Install [Python](https://wiki.python.org/moin/BeginnersGuide/Download)
### 2. Install [AIOHTTP](https://docs.aiohttp.org/en/stable/)
### 3. Download the script and open a terminal at that folder location.
### 4. Run the script from the terminal: ```python <script_name>.py```

## Command-Line Arguments:

### Filtering:
| Argument                       | Description                                                                  |
| ------------------------------ | ---------------------------------------------------------------------------- |
| `--include-partners`           | Include Partner / Partner With / Friends Forever commanders.                 |
| `--include-backgrounds`        | Include commanders with “Choose a Background”.                               |
| `--include-companions`         | Include commanders with the Companion ability.                               |
| `--include-doctors-companions` | Include commanders with the “Doctor’s companion” mechanic.                   |
| `--include-funny-sets`         | Include sets with `set_type='funny'` (Un-sets, Playtest cards).              |
| `--include-vanilla`            | Include commanders with no rules text (vanilla creatures).                   |
| `--include-ptk`                | Include Portal Three Kingdoms commanders.                                    |
| `--ptk-strict`                 | Check **all printings** for PTK set code (slower, accurate).                 |
| `--include-doctors`            | Include **The Doctors** themselves (Time Lords) from Doctor Who sets/promos. |

### Recent Commander Filtering:
| Argument           | Description                                                                                   |
| ------------------ | --------------------------------------------------------------------------------------------- |
| `--recent-days N`  | Number of days to treat a commander as “recent” (default: `90`). Set `0` to disable entirely. |
| `--include-recent` | Include recent commanders regardless of `--recent-days`.                                      |

### Output & Performance:
| Argument           | Description                                                                        |
| ------------------ | ---------------------------------------------------------------------------------- |
| `--bottom-k N`     | Show the bottom **N** commanders (default: `20`).                                  |
| `--concurrency N`  | Number of concurrent EDHREC requests (default: `8`). Lower if you hit rate limits. |
| `--delay SECONDS`  | Delay between requests in seconds (default: `0.15`). Increase if rate-limited.     |
| `--csv PATH`       | Save output to a CSV file at `PATH`.                                               |
| `--only-positive`  | Exclude commanders with `0` decks.                                                 |
| `--include-errors` | Show entries that failed to fetch deck counts (`? decks`).                         |

### Sample Usage: 
```python <script_name>.py --bottom-k 50 --include-recent --csv bottom50.csv```

## Notes:
1. Run time: The script hits Scryfall once per commander, then EDHREC once per commander. With concurrency 8 and delay 0.15s, expect ~5–10 minutes depending on filters.
2. Rate limiting: If EDHREC slows or blocks requests, increase ```--delay``` and/or reduce ```--concurrency```.
3. Recent filter: Uses the released_at of the Scryfall representative printing. In “fast” mode, a recent reprint can make an old commander appear “recent”.
