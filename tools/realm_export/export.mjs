// realm_export/export.mjs — read-only dump of osu!lazer client.realm to JSON.
// Usage: node export.mjs <realm-copy>
// Prints ONE JSON object to stdout:
//   {"beatmaps":[{onlineId,setOnlineId,md5,hash,stars,status,statusName,mode,
//                 difficultyName,title,artist,creator,bpm,length,dateAdded,plays,grade}],
//    "scores":[{beatmapOnlineId,beatmapMd5,beatmapHash,ruleset,rank}],
//    "sets":{setOnlineId:[{filename,hash}]},
//    "meta":{realmVersion,exportedAt}}
//
// Notes:
// - osu-lazer-db-reader@0.1.6 was tried first but cannot open the current
//   database: it pins realm@^10.17.0 which rejects Realm file format v24
//   ("Opening Realm files of format version 24 is not supported") and its
//   schemas are pinned at schemaVersion 14 (current file is version 51 with
//   new columns such as OnlineMD5Hash, DateSubmitted/DateRanked, UserTags,
//   TotalScoreWithoutMods, etc.). So this helper uses the `realm` package
//   directly (realm@20.2.0, read-only, no schema supplied) and discovers the
//   schema from the file itself.
// - BeatmapOnlineStatus mapping is from ppy/osu osu.Game/Beatmaps/
//   BeatmapOnlineStatus.cs (LocallyModified=-4, None=-3, Graveyard=-2,
//   WIP=-1, Pending=0, Ranked=1, Approved=2, Qualified=3, Loved=4).
//   Unmapped ints -> "unknown", never throws.
// - ScoreRank mapping is from ppy/osu osu.Game/Scoring/ScoreRank.cs
//   (F=-1, D=0, C=1, B=2, A=3, S=4, SH=5, X=6, XH=7).
// - Ruleset ShortName "fruits" (lazer internal name) is mapped to "catch".
// - NEVER open the live client.realm for writing: callers must copy it first
//   and pass the copy. This script opens with readOnly:true.

import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { Realm } = require("realm");

const STATUS_NAMES = {
  "-4": "locally_modified",
  "-3": "none",
  "-2": "graveyard",
  "-1": "wip",
  "0": "pending",
  "1": "ranked",
  "2": "approved",
  "3": "qualified",
  "4": "loved",
};

const RANK_NAMES = {
  "-1": "F",
  "0": "D",
  "1": "C",
  "2": "B",
  "3": "A",
  "4": "S",
  "5": "SH",
  "6": "X",
  "7": "XH",
};

function statusNameOf(status) {
  const k = String(status);
  return Object.prototype.hasOwnProperty.call(STATUS_NAMES, k)
    ? STATUS_NAMES[k]
    : "unknown";
}

function rankNameOf(rank) {
  const k = String(rank);
  return Object.prototype.hasOwnProperty.call(RANK_NAMES, k)
    ? RANK_NAMES[k]
    : "";
}

function modeOf(shortName) {
  const s = String(shortName || "").toLowerCase();
  if (s === "fruits") return "catch";
  if (s === "osu" || s === "taiko" || s === "catch" || s === "mania") return s;
  return s || "osu";
}

function safeStr(v) {
  return v === null || v === undefined ? "" : String(v);
}

function main() {
  const realmPath = process.argv[2];
  if (!realmPath) {
    process.stderr.write("usage: node export.mjs <realm-copy>\n");
    process.exit(2);
  }
  let realm;
  return Realm.open({ path: realmPath, readOnly: true })
    .then((r) => {
      realm = r;
      const schemaVersion =
        typeof r.schemaVersion === "number" ? r.schemaVersion : null;
      const beatmaps = r.objects("Beatmap");
      const sets = r.objects("BeatmapSet");
      const scores = r.objects("Score");

      // Group scores by linked Beatmap ID string for per-difficulty plays/grade.
      const playsByBeatmapId = new Map();
      const bestRankByBeatmapId = new Map();
      const scoreRows = [];
      for (const s of scores) {
        let onlineId = -1;
        let md5 = "";
        let hash = "";
        let linkId = null;
        try {
          if (s.BeatmapInfo) {
            linkId = s.BeatmapInfo.ID ? s.BeatmapInfo.ID.toString() : null;
            const oid = s.BeatmapInfo.OnlineID;
            onlineId =
              typeof oid === "number" && Number.isFinite(oid) ? oid : -1;
            md5 = s.BeatmapInfo.MD5Hash || "";
            hash = s.BeatmapInfo.Hash || "";
          } else if (s.BeatmapHash) {
            hash = s.BeatmapHash || "";
          }
        } catch {
          // never crash on odd rows
        }
        let ruleset = "osu";
        try {
          ruleset = modeOf(s.Ruleset && s.Ruleset.ShortName);
        } catch {
          ruleset = "osu";
        }
        const rank =
          typeof s.Rank === "number" && Number.isFinite(s.Rank)
            ? Math.trunc(s.Rank)
            : -1;
        scoreRows.push({
          beatmapOnlineId: onlineId,
          beatmapMd5: md5,
          beatmapHash: hash,
          ruleset,
          rank,
        });
        if (linkId) {
          playsByBeatmapId.set(linkId, (playsByBeatmapId.get(linkId) || 0) + 1);
          const prev = bestRankByBeatmapId.get(linkId);
          if (prev === undefined || rank > prev) {
            bestRankByBeatmapId.set(linkId, rank);
          }
        }
      }

      const beatmapRows = [];
      for (const b of beatmaps) {
        let idStr = null;
        try {
          idStr = b.ID ? b.ID.toString() : null;
        } catch {
          idStr = null;
        }
        const onlineId =
          typeof b.OnlineID === "number" && Number.isFinite(b.OnlineID)
            ? Math.trunc(b.OnlineID)
            : -1;
        let setOnlineId = -1;
        let dateAdded = "";
        try {
          if (b.BeatmapSet) {
            const soid = b.BeatmapSet.OnlineID;
            setOnlineId =
              typeof soid === "number" && Number.isFinite(soid)
                ? Math.trunc(soid)
                : -1;
            const da = b.BeatmapSet.DateAdded;
            if (da instanceof Date && !Number.isNaN(da.getTime())) {
              dateAdded = da.toISOString();
            } else if (typeof da === "string" && da) {
              dateAdded = da;
            }
          }
        } catch {
          // keep defaults
        }
        const status =
          typeof b.Status === "number" && Number.isFinite(b.Status)
            ? Math.trunc(b.Status)
            : -3;
        let mode = "osu";
        try {
          mode = modeOf(b.Ruleset && b.Ruleset.ShortName);
        } catch {
          mode = "osu";
        }
        let title = "";
        let artist = "";
        let creator = "";
        try {
          title = safeStr(b.Metadata && b.Metadata.Title);
          artist = safeStr(b.Metadata && b.Metadata.Artist);
          const author = b.Metadata && b.Metadata.Author;
          creator = safeStr(author && author.Username);
        } catch {
          // keep defaults
        }
        const plays = idStr ? playsByBeatmapId.get(idStr) || 0 : 0;
        const bestRank = idStr ? bestRankByBeatmapId.get(idStr) : undefined;
        const grade =
          bestRank === undefined ? "" : rankNameOf(bestRank);
        beatmapRows.push({
          onlineId,
          setOnlineId,
          md5: safeStr(b.MD5Hash),
          hash: safeStr(b.Hash),
          stars: typeof b.StarRating === "number" ? b.StarRating : 0.0,
          status,
          statusName: statusNameOf(status),
          mode,
          difficultyName: safeStr(b.DifficultyName),
          title,
          artist,
          creator,
          bpm: typeof b.BPM === "number" ? b.BPM : 0.0,
          length: typeof b.Length === "number" ? b.Length : 0,
          dateAdded,
          plays,
          grade,
        });
      }

      const setsObj = {};
      for (const s of sets) {
        const soid =
          typeof s.OnlineID === "number" && Number.isFinite(s.OnlineID)
            ? Math.trunc(s.OnlineID)
            : -1;
        const files = [];
        try {
          for (const f of s.Files || []) {
            let filename = "";
            let hash = "";
            try {
              filename = safeStr(f.Filename);
              hash = safeStr(f.File && f.File.Hash);
            } catch {
              // skip odd entries
            }
            if (!filename) continue;
            files.push({ filename, hash });
          }
        } catch {
          // keep what we have
        }
        setsObj[String(soid)] = files;
      }

      const out = {
        beatmaps: beatmapRows,
        scores: scoreRows,
        sets: setsObj,
        meta: {
          realmVersion: schemaVersion,
          exportedAt: new Date().toISOString(),
        },
      };
      // Use sync write to fd 1 so large dumps fully flush through pipes
      // before exit (process.stdout.write + immediate exit truncates).
      const fs = require("node:fs");
      fs.writeFileSync(1, JSON.stringify(out) + "\n");
      try {
        if (realm) realm.close();
      } catch {
        // ignore
      }
      realm = null;
      process.exit(0);
    })
    .catch((e) => {
      try {
        if (realm) realm.close();
      } catch {
        // ignore
      }
      const msg = e && e.message ? e.message : String(e);
      process.stderr.write(`realm export failed: ${msg}\n`);
      process.exit(1);
    });
}

await main();
