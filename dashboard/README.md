# Dashboard

A read-only web app over the bot's database: tonight's slate and picks, a slate
screener, fantasy projections, player and team stats, prop
projections, injury news, and the picks with their record.

## Run it

Two processes, from the repo root:

```bash
uv run nba-bot serve          # the API, on http://127.0.0.1:8000
cd dashboard && npm run dev   # the site, on http://localhost:3000
```

Needs Node 20+ (`npm install` once). The site reads the API address from
`NEXT_PUBLIC_API_BASE` in `dashboard/.env.local` (see `.env.example`).

## Pages

| Page | What it's for |
|------|---------------|
| **Tonight** (`/`) | Any day's games with the model's and the market's win probability, that day's picks, the strongest injury news, and who is heating up. Step through dates or jump to one. |
| **Game** (`/games/[id]`) | Both teams' projected box scores before the game; after it, what each player did next to what the model projected. Injury report, news, and the model's reasoning. |
| **Screener** (`/screener`) | Every player on the slate ranked in any of the 12 stats: projection, season and last-5 averages, the matchup, and — when a sportsbook line is in — the model's chance of the over and his recent hit rate. |
| **Fantasy** (`/fantasy`) | Projected fantasy points for the slate under ESPN, Sleeper or FanDuel/Yahoo scoring, against his season and last-5 averages. |
| **Players** (`/players`) | Every player's averages — season, last 10 or last 5 — sortable and filterable, with minutes and points trends. |
| **Player** (`/players/[id]`) | The prop explorer: pick any of 12 markets, set a line, and see how often he has cleared it (last 5/10/20/season, home/away) and what the model gives the over and under for his next game. Full game log and news. |
| **Teams** (`/teams`) | What each team allows and produces per game, shaded to make soft matchups stand out. |
| **Team** (`/teams/[id]`) | Roster with recent minutes and injury status, results, injury report, news. |
| **News** (`/news`) | Player reports read out of the news feeds, merged across articles, firmest first. |
| **Picks** (`/picks`) | Every recorded pick and how it finished, closing-line value, and the live game model's accuracy. |

Press `/` anywhere to search players and teams.

## Layout

- `src/app/` — one folder per page (Next.js app router, client-rendered).
- `src/components/` — the sortable table, game-log chart, search, and shared pieces.
- `src/lib/` — the API hook (`useApi`), wire types, formatters, the prop-market list, and the fantasy scoring tables.

The API side is `src/nba_bot/api/` in the repo root: `views.py` assembles what each page
needs, `app.py` exposes it. Nothing in the dashboard writes or triggers the pipeline.
