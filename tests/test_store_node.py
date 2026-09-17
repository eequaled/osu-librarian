"""Node-side checks for web/store.js pure logic. Skipped when node is absent."""
import json
import os
import shutil
import subprocess
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")

SCRIPT = r"""
import("./web/store.js").then(s => {
  const maps = [
    {id:"a", set_id:"1", artist:"X", title:"Alpha", creator:"m", diff:"Insane",
     mode_name:"osu", stars:5, played_local:true, played_online:false, beatmap_id:1, ranked:"ranked", grade:"A"},
    {id:"b", set_id:"1", artist:"X", title:"Alpha", creator:"m", diff:"Normal",
     mode_name:"osu", stars:2, played_local:false, played_online:false, beatmap_id:2, ranked:"ranked", grade:""},
    {id:"c", set_id:"2", artist:"Y", title:"Beta", creator:"n", diff:"Hard",
     mode_name:"mania", stars:3, played_local:false, played_online:true, beatmap_id:3, ranked:"loved", grade:""}
  ];
  const F = (o) => ({q:"", played:"all", mode:"all", smin:0, smax:99,
                     ranked:"all", sort:"title", view:"difficulty", ...o});
  const out = {
    unplayed: s.visibleMaps(maps, F({played:"unplayed"})).map(m => m.id),
    mania: s.visibleMaps(maps, F({mode:"mania"})).map(m => m.id),
    stars: s.visibleMaps(maps, F({smin:4})).map(m => m.id),
    loved: s.visibleMaps(maps, F({ranked:"loved"})).map(m => m.id),
    text: s.visibleMaps(maps, F({q:"beta"})).map(m => m.id),
    sets: s.groupIntoSets(maps).length,
    rowsDiff: s.buildRows(maps, F({}), new Set()).length,
    rowsCollapsed: s.buildRows(maps, F({view:"mapset"}), new Set()).length,
    rowsExpanded: s.buildRows(maps, F({view:"mapset"}), new Set(["1","2"])).length,
    summary: s.summarize(maps),
    esc: s.escapeHtml("<b>&\"")
  };
  console.log(JSON.stringify(out));
});
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class StoreJsTests(unittest.TestCase):
    def test_filter_group_select_parity(self):
        r = subprocess.run(["node", "-e", SCRIPT], capture_output=True,
                           text=True, cwd=REPO, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["unplayed"], ["b"])
        self.assertEqual(out["mania"], ["c"])
        self.assertEqual(out["stars"], ["a"])
        self.assertEqual(out["loved"], ["c"])
        self.assertEqual(out["text"], ["c"])
        self.assertEqual(out["sets"], 2)
        self.assertEqual(out["rowsDiff"], 3)
        self.assertEqual(out["rowsCollapsed"], 2)
        self.assertEqual(out["rowsExpanded"], 5)
        self.assertEqual(out["summary"],
                         {"diffs": 3, "sets": 2, "played": 2, "unplayed": 1})
        self.assertEqual(out["esc"], "&lt;b&gt;&amp;&quot;")


if __name__ == "__main__":
    unittest.main()
