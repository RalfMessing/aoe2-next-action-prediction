-- Schema of data/processed/aoe2.db. Applied by scripts/03_load_static.py, which drops and
-- recreates every table below, so re-running 03 resets the database (run 04+ again after it).
--
-- Static tables are filled by 03_load_static.py, replay tables by 04_load_replays.py.
-- Later phases (civ inference, resources) add their own tables.
--
-- Provenance: every static row has `source` ('2023' = aoe2techtree at commit
-- 9d2f8987b2852695e997cc8ca4bee077108d1fbc, '2026' = live-site fallback, 'manual' = a
-- hand-kept CSV in data/manual/) and a free-text `source_detail` naming the file and key.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS resource_snapshot;
DROP TABLE IF EXISTS session_age;
DROP TABLE IF EXISTS session_start;
DROP TABLE IF EXISTS setting;
DROP TABLE IF EXISTS event_object_link;
DROP TABLE IF EXISTS event;
DROP TABLE IF EXISTS session;
DROP TABLE IF EXISTS civ_group_member;
DROP TABLE IF EXISTS civ_group;
DROP TABLE IF EXISTS player;
DROP TABLE IF EXISTS match;
DROP TABLE IF EXISTS name_mapping;
DROP TABLE IF EXISTS dependency;
DROP TABLE IF EXISTS civilization_object;
DROP TABLE IF EXISTS civilization;
DROP TABLE IF EXISTS game_object;
DROP TABLE IF EXISTS source_file;

-- ------------------------------------------------------------------------------------------
-- Static data

CREATE TABLE source_file (
    name        TEXT PRIMARY KEY,           -- e.g. 'aoe2techtree_2023/data.json'
    version     TEXT NOT NULL,              -- '2023', '2026', 'manual', 'ocel'
    sha256      TEXT NOT NULL,
    origin      TEXT                        -- URL or commit
);

-- One row per building, unit or technology. Tree IDs are only unique per category, so
-- `id` is our own key and (type, tree_id) is unique.
-- A unit upgrade (Crossbowman, Elite Skirmisher, ...) appears twice: as a UNIT (what you
-- get, with its train cost) and as a TECHNOLOGY (the research, with the upgrade cost)
-- whose `upgrades_to` points to the unit. Research events map to the TECHNOLOGY.
CREATE TABLE game_object (
    id              INTEGER PRIMARY KEY,
    type            TEXT NOT NULL CHECK (type IN ('BUILDING', 'UNIT', 'TECHNOLOGY', 'UNKNOWN')),
    tree_id         INTEGER,                 -- ID in aoe2techtree (techs: the tech ID)
    name            TEXT NOT NULL,
    internal_name   TEXT,
    required_age    INTEGER CHECK (required_age BETWEEN 1 AND 4),   -- earliest over all civs
    time            INTEGER,                 -- train, build or research time in seconds
    cost_food       INTEGER NOT NULL DEFAULT 0,
    cost_wood       INTEGER NOT NULL DEFAULT 0,
    cost_gold       INTEGER NOT NULL DEFAULT 0,
    cost_stone      INTEGER NOT NULL DEFAULT 0,
    produced_at     INTEGER REFERENCES game_object (id),  -- most common producer over civs
    upgrades_to     INTEGER REFERENCES game_object (id),  -- only for upgrade technologies
    is_unique       INTEGER NOT NULL DEFAULT 0 CHECK (is_unique IN (0, 1)),
    source          TEXT NOT NULL CHECK (source IN ('2023', '2026', 'manual')),
    source_detail   TEXT,
    UNIQUE (type, tree_id)
);

CREATE TABLE civilization (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL CHECK (source IN ('2023', '2026', 'manual')),
    source_detail   TEXT
);

-- Availability per civ, with the civ's own age and producer (both vary by civ).
CREATE TABLE civilization_object (
    civ_id          INTEGER NOT NULL REFERENCES civilization (id),
    object_id       INTEGER NOT NULL REFERENCES game_object (id),
    required_age    INTEGER NOT NULL CHECK (required_age BETWEEN 1 AND 4),
    produced_at     INTEGER REFERENCES game_object (id),
    source          TEXT NOT NULL CHECK (source IN ('2023', '2026', 'manual')),
    source_detail   TEXT,
    PRIMARY KEY (civ_id, object_id)
);

-- Prerequisites besides age and producer (those are columns above).
-- A row without req_group is a plain requirement. Rows sharing (object_id, req_group) form
-- a group that is met when the summed `weight` of its met rows reaches `req_count`,
-- e.g. Imperial Age: 2 of {University, Monastery, Siege Workshop} or a Castle (weight 2).
CREATE TABLE dependency (
    object_id       INTEGER NOT NULL REFERENCES game_object (id),
    requires_id     INTEGER NOT NULL REFERENCES game_object (id),
    kind            TEXT NOT NULL CHECK (kind IN ('tree_link', 'manual')),
    req_group       TEXT,
    req_count       INTEGER,
    weight          INTEGER NOT NULL DEFAULT 1,
    source          TEXT NOT NULL CHECK (source IN ('2023', '2026', 'manual')),
    source_detail   TEXT,
    PRIMARY KEY (object_id, requires_id),
    CHECK ((req_group IS NULL) = (req_count IS NULL))
);

-- How each object name in the log maps to a game object. Names depend on the action
-- ("Camel Scout" means a Camel Rider in QUEUE but a Camel Scout in CREATE_INITIAL).
-- method NULL and game_object_id NULL: no decision yet (listed in reports/static_data.md).
-- method 'manual' and game_object_id NULL: decided that it is not a game object.
CREATE TABLE name_mapping (
    action          TEXT NOT NULL CHECK (action IN ('QUEUE', 'RESEARCH', 'BUILD', 'CREATE_INITIAL')),
    ocel_name       TEXT NOT NULL,
    game_object_id  INTEGER REFERENCES game_object (id),
    method          TEXT CHECK (method IN ('exact', 'normalized', 'manual')),
    note            TEXT,
    PRIMARY KEY (action, ocel_name)
);

-- ------------------------------------------------------------------------------------------
-- Replay data (filled by 04_load_replays.py)

CREATE TABLE match (
    id              INTEGER PRIMARY KEY,
    source_id       TEXT NOT NULL UNIQUE,    -- 'M271214755'
    start_time      TEXT,                    -- first event, as in the log
    duration        INTEGER                  -- seconds, last minus first event
);

CREATE TABLE player (
    id              INTEGER PRIMARY KEY,
    source_id       TEXT NOT NULL UNIQUE     -- 'P10531210'
);

-- Candidate civ sets (Phase 4, 05_civ_inference.py). Created here so that
-- session.civ_group_id can be a foreign key.
CREATE TABLE civ_group (
    id              INTEGER PRIMARY KEY,
    key             TEXT NOT NULL UNIQUE,    -- sorted civ IDs, e.g. '3,17,40'
    label           TEXT NOT NULL,
    description     TEXT
);

CREATE TABLE civ_group_member (
    group_id        INTEGER NOT NULL REFERENCES civ_group (id),
    civ_id          INTEGER NOT NULL REFERENCES civilization (id),
    PRIMARY KEY (group_id, civ_id)
);

-- One player in one match. Suspicious sessions are flagged, never deleted.
CREATE TABLE session (
    id                  INTEGER PRIMARY KEY,
    source_id           TEXT NOT NULL UNIQUE,  -- 'S271214755_10531210'
    match_id            INTEGER NOT NULL REFERENCES match (id),
    player_id           INTEGER NOT NULL REFERENCES player (id),
    civ_group_id        INTEGER REFERENCES civ_group (id),   -- Phase 4
    civ_conflict        TEXT,                  -- Phase 4: evidence that emptied the candidate set
    civ_conflict_object_id INTEGER REFERENCES game_object (id),
    short_match         INTEGER NOT NULL DEFAULT 0 CHECK (short_match IN (0, 1)),       -- match spans < 300 s
    no_commands         INTEGER NOT NULL DEFAULT 0 CHECK (no_commands IN (0, 1)),
    outlier_commands    INTEGER NOT NULL DEFAULT 0 CHECK (outlier_commands IN (0, 1)),
    has_unmapped_events INTEGER NOT NULL DEFAULT 0 CHECK (has_unmapped_events IN (0, 1))
);

-- One row per OCEL event. START and COMPLETE times are simulated by the dataset authors
-- (see README); COMMAND (and START for BUILD) are the player decisions.
CREATE TABLE event (
    id              INTEGER PRIMARY KEY,
    source_id       TEXT NOT NULL UNIQUE,      -- 'e_M271214755_408'
    session_id      INTEGER NOT NULL REFERENCES session (id),
    player_id       INTEGER REFERENCES player (id),   -- set when the event links to a Player
    action          TEXT NOT NULL CHECK (action IN ('QUEUE', 'RESEARCH', 'BUILD', 'GATHER',
                                                    'SET_GATHER_POINT', 'CREATE_INITIAL')),
    phase           TEXT CHECK (phase IN ('COMMAND', 'START', 'COMPLETE')),
    game_object_id  INTEGER REFERENCES game_object (id),
    resource        TEXT CHECK (resource IN ('FOOD', 'WOOD', 'GOLD', 'STONE', 'RELIC', 'NONE')),
    time_s          INTEGER NOT NULL,          -- seconds since the match's first event
    seq             INTEGER NOT NULL,          -- n of e_<match>_<n>, orders events within a second
    in_vocabulary   INTEGER NOT NULL CHECK (in_vocabulary IN (0, 1)),
    object_ref      TEXT,                      -- main OCEL object instance; may be a UO id
                                               -- that is missing from the source's object table
    ocel_type       TEXT NOT NULL
);
CREATE INDEX event_session_time ON event (session_id, time_s, seq);
CREATE INDEX event_object ON event (game_object_id);

-- Raw E2O links of the OCEL log, kept as they are (object_ref may be a UO id that the
-- source's object table does not contain).
CREATE TABLE event_object_link (
    event_id        INTEGER NOT NULL REFERENCES event (id),
    object_ref      TEXT NOT NULL,
    PRIMARY KEY (event_id, object_ref)
) WITHOUT ROWID;

-- ------------------------------------------------------------------------------------------
-- Resource and age estimates (filled by 06_resources.py). Everything here is estimated.

-- Settings a script ran with (deduction phase, starting resources, gather-rate bound, ...).
CREATE TABLE setting (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    note            TEXT
);

-- Starting resources and villagers per session: the standard random-map start, adjusted
-- only for sessions narrowed to exactly one civ (data/manual/start_resources.csv).
CREATE TABLE session_start (
    session_id      INTEGER PRIMARY KEY REFERENCES session (id),
    food            INTEGER NOT NULL,
    wood            INTEGER NOT NULL,
    gold            INTEGER NOT NULL,
    stone           INTEGER NOT NULL,
    villagers       INTEGER NOT NULL,        -- observed CREATE_INITIAL villagers
    civ_id          INTEGER REFERENCES civilization (id),   -- set when an adjustment was applied
    source_detail   TEXT,
    estimated       INTEGER NOT NULL DEFAULT 1 CHECK (estimated = 1)
);

-- When a session reached each age: the earlier of (first age command + research time) and
-- (first use of an object that needs the age). Ages never reached have no row.
CREATE TABLE session_age (
    session_id      INTEGER NOT NULL REFERENCES session (id),
    age             INTEGER NOT NULL CHECK (age BETWEEN 1 AND 4),
    reached_s       INTEGER NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('start', 'command', 'object', 'higher_age')),
    command_s       INTEGER,                 -- first COMMAND of the age tech
    object_s        INTEGER,                 -- first use of an object that needs this age or later
    object_id       INTEGER REFERENCES game_object (id),
    estimated       INTEGER NOT NULL DEFAULT 1 CHECK (estimated = 1),
    PRIMARY KEY (session_id, age)
);

-- State at every in-vocabulary event (after that event). spent_* is cumulative spending at
-- the configured deduction phase; min_gathered is what the player must at least have
-- gathered (total spent beyond the starting resources). Income itself is unknown.
CREATE TABLE resource_snapshot (
    event_id        INTEGER PRIMARY KEY REFERENCES event (id),
    session_id      INTEGER NOT NULL REFERENCES session (id),
    time_s          INTEGER NOT NULL,
    spent_food      INTEGER NOT NULL,
    spent_wood      INTEGER NOT NULL,
    spent_gold      INTEGER NOT NULL,
    spent_stone     INTEGER NOT NULL,
    min_gathered    INTEGER NOT NULL,
    villagers       INTEGER NOT NULL,        -- starting villagers + villagers completed so far
    age             INTEGER NOT NULL,
    age_source      TEXT NOT NULL,
    estimated       INTEGER NOT NULL DEFAULT 1 CHECK (estimated = 1)
);
CREATE INDEX resource_snapshot_session ON resource_snapshot (session_id, time_s);
