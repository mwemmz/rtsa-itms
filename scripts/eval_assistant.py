"""Score the Highway Code assistant's retrieval: for each everyday question, did an accepted rule come back?

Run: python scripts/eval_assistant.py   (needs data/highway_code.jsonl). Prints top-1 and top-4 hit rates and the misses.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# question -> rule numbers that correctly answer it (checked by reading the rules)
CASES = [
    ("Can I use my phone while driving?", {115}),
    ("Do I have to wear a seat belt?", {125, 126}),
    ("Do children need seat belts?", {127}),
    ("What is the legal alcohol limit for driving?", {111}),
    ("Can I drive after drinking beer?", {111}),
    ("Can I drive on medication?", {114}),
    ("What should I do if I have an accident?", {119, 121}),
    ("Do I have to report a crash to the police?", {121, 122}),
    ("What do I do if my brakes fail?", {135}),
    ("What do I do if my car breaks down?", {262}),
    ("How far should I stay from the car in front?", {141, 180, 181}),
    ("Who is at fault when I hit someone from behind?", {141, 180, 181}),
    ("What is the speed limit on gravel roads?", {185}),
    ("Can I overtake on a bend?", {170}),
    ("Which side should I overtake on?", {164}),
    ("When should I not overtake?", {170, 171}),
    ("Who has right of way at a roundabout?", {228, 229, 230, 231, 232}),
    ("What do I do at a zebra crossing?", {22, 198, 199, 200}),
    ("Can I park on a pedestrian crossing?", {199}),
    ("Where am I not allowed to park?", {250}),
    ("Can I park on a yellow line?", {250, 254}),
    ("What does a red traffic light mean?", {72, 222, 282}),
    ("What must I do at a stop sign?", {218}),
    ("What does a give way sign mean?", {217}),
    ("When must I use my headlights?", {291, 246, 244}),
    ("Should I use headlights in fog?", {246, 291}),
    ("When can I use my horn?", {248, 249, 285}),
    ("How do I reverse safely?", {233, 234, 238, 239}),
    ("What do I do at a railway level crossing?", {256, 257, 258, 36}),
    ("What do I do when an ambulance comes behind me?", {187, 266, 32}),
    ("What is the age for a learner to get a provisional licence?", {95, 96}),
    ("Who can supervise a learner driver?", {97}),
    ("What tests must I pass to get a licence?", {98}),
    ("How do I drive in the rain?", {292, 186}),
    ("What should I do on a dusty road?", {296, 297, 167}),
    ("Can a motorcyclist ride without a helmet?", {73}),
    ("How should I carry animals?", {60, 58, 59}),
    ("Do I need to rest on a long journey?", {105, 103, 106}),
    ("What do I do when I feel sleepy while driving?", {106, 103, 177}),
    ("How do I tow another vehicle?", {191}),
    ("Can I overload my vehicle?", {193}),
    ("How should cyclists ride?", {80, 83}),
    ("Can I drive near a school?", {208, 209}),
    ("What happens if I skid?", {136}),
    ("What do I do if I am dazzled by headlights?", {242, 108}),
    ("When should I give way to pedestrians at a junction?", {205, 13}),
    ("What do I do at a pelican crossing?", {27, 203, 204}),
    ("Can children sit in the front seat?", {41, 42, 127}),
    ("How do I change lanes?", {152, 147}),
    ("What lane do I use on a dual carriageway?", {154, 155, 156}),
]


def run(verbose=True):
    from app.services import highway_code
    idx = highway_code.load_index()
    if idx is None:
        raise SystemExit("data/highway_code.jsonl not found")
    top1 = top4 = 0
    misses = []
    for q, ok in CASES:
        hits = [r.number for r, s in idx.search(q, 4) if s > 0]
        if hits and hits[0] in ok:
            top1 += 1
        if set(hits) & ok:
            top4 += 1
        else:
            misses.append((q, hits, sorted(ok)))
    n = len(CASES)
    if verbose:
        print(f"top-1 {top1}/{n} ({100*top1//n}%)   top-4 {top4}/{n} ({100*top4//n}%)")
        for q, hits, ok in misses:
            print("MISS:", q, "got", hits, "want one of", ok)
    return top1 / n, top4 / n


if __name__ == "__main__":
    run()
