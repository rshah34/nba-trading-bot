-- The bet-decision layer's persistence.
--
-- `bets`: one recorded paper bet per (game, model). Written pre-tip with the odds
-- taken and the sized stake; scored after the game with CLV (vs. the closing line)
-- and P&L. CLV is the north-star metric — it needs only the closing line, not the
-- outcome, so it's the durable signal; P&L is the noisy one.
CREATE TABLE bets (
    id                   SERIAL PRIMARY KEY,
    game_id              TEXT NOT NULL REFERENCES games(game_id),
    prediction_id        INTEGER REFERENCES predictions(id),
    model_version        TEXT NOT NULL,
    decided_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    side                 TEXT NOT NULL,          -- 'home' | 'away'
    model_prob           NUMERIC NOT NULL,       -- calibrated prob for the side bet
    market_prob          NUMERIC NOT NULL,       -- de-vigged market prob for that side
    edge                 NUMERIC NOT NULL,
    stake_fraction       NUMERIC NOT NULL,       -- fraction of bankroll (fractional Kelly)
    decimal_odds         NUMERIC NOT NULL,       -- odds taken at bet time
    -- filled in at settlement:
    closing_decimal_odds NUMERIC,
    clv                  NUMERIC,                -- prob points beaten vs. close
    won                  BOOLEAN,
    pnl                  NUMERIC,                -- per unit of bankroll at stake_fraction
    settled_at           TIMESTAMPTZ,
    CONSTRAINT bets_game_model_key UNIQUE (game_id, model_version)  -- one bet per game per model
);
CREATE INDEX idx_bets_unsettled ON bets(settled_at) WHERE settled_at IS NULL;

-- Fitted Platt calibration parameters per model version (from `nba-bot calibrate --save`).
-- The live pipeline applies these to raw probabilities before deciding bets. Empty =
-- apply no calibration (honest bootstrap until enough live games resolve to fit).
CREATE TABLE calibration_params (
    model_version TEXT PRIMARY KEY,
    a             NUMERIC NOT NULL,   -- logit slope (<1 shrinks toward 0.5)
    b             NUMERIC NOT NULL,   -- logit intercept
    n             INTEGER NOT NULL,   -- predictions fit on
    fitted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
