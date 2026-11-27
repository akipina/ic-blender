"""Stitch the chapter fragments into index.html.

    .venv/bin/python3 ic_blender/docs/guide/build_guide.py

Chapters are HTML fragments in chapters/<name>.html (one <section id=name> each, h2 title, h3 subsections);
the navigation rail and the title cards are generated from their headings. Missing chapters are skipped
with a warning so a partial guide still builds.
"""
import datetime
import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = [("start", "Start here"), ("replace", "Replace one of the game's animals"),
         ("newcreature", "A new creature from a game animal"), ("mesh", "A creature from your own mesh"),
         ("numbers", "Gameplay numbers"), ("anim", "Animations and motions"),
         ("structures", "Structures and props"), ("gatherers", "Gatherers and characters"),
         ("effects", "Effects, events and sounds"), ("ship", "Check, export, install, remove and share"),
         ("trouble", "Troubleshooting"),
         # reference chapters: shaping a creature in detail, the Blueprint panel, maps
         ("tour", "Finding your way around a creature"), ("edit", "Editing an existing animal"), ("topology", "Topology"),
         ("sculpt", "Sculpting"), ("scratch", "A rhino from scratch"), ("fit", "Fitting a mesh"),
         ("retarget", "Retargeting"), ("skin", "Skinning and re-skinning"), ("texture", "Textures"),
         ("boner", "Bones, limbs and connections"), ("blueprint", "The Blueprint panel"),
         ("maps", "Maps and scenarios")]
BLURB = {"start": "Install the add-on, point it at your game, make a test copy, and learn how a project works.",
         "replace": "The shortest way to see your own work in the game: change the wolf and play it.",
         "newcreature": "A creature of your own, under a new name, in the creature creator.",
         "mesh": "Two routes from a model made elsewhere: Retarget (it combines) and Fit (shape only).",
         "numbers": "Hit points, speed, attacks and cost of a creature, a building, a worker or a prop.",
         "anim": "Play and change animations, make a new one, tell the game when to play it, fix walking speeds.",
         "structures": "Parts, textures, bones, attachment points and damage stages of a building or a prop.",
         "gatherers": "A worker or a story character: the body, the skin, the skeleton, the animations.",
         "effects": "Copy and retune an effect, hook it to an animation, add your own sound.",
         "ship": "Check, export, install into a test copy, remove, share a zip, install someone else's.",
         "trouble": "Every message you are likely to meet, in the add-on's own words, and what to do.",
         "tour": "What is on screen when a creature is open: points, patches, rig, panels, combine preview.",
         "edit": "Reshape an animal: patch feel, control points, symmetry, loop cut, hard edges.",
         "topology": "Delete, grow, weld, fill, subdivide, extrude, mirror and attach -- and what a patch costs.",
         "sculpt": "Sculpt Mode brushes on the exact surface, refitted into the patches you touched.",
         "scratch": "From the New Creature scaffold to a crude rhino with a horn, skin, clips and a combine check.",
         "fit": "Any mesh becomes a patch creature: budgets, the real ceiling, and the fit-to-rig split.",
         "retarget": "Make a working creature take another shape.",
         "skin": "Which bone moves which control point, and how to change it.",
         "texture": "Paint the creature's own texture, edit UVs, transfer a source mesh's skin.",
         "boner": "Limbs, classes, connection rings, ring starts, bone edits, joint limits and IK chains.",
         "blueprint": "The Blueprint panel: name, kind, footprint, offset, sounds and icon of a building, worker or prop.",
         "maps": "Open one of the game's maps, change its ground, water, textures and placed things, and save it."}


def main():
    tpl = open(os.path.join(HERE, "guide_template.html")).read()
    nav, cards, body = [], [], []
    for n, (key, title) in enumerate(ORDER, 1):
        path = os.path.join(HERE, "chapters", f"{key}.html")
        if not os.path.exists(path):
            print(f"warning: no chapter {key}", file=sys.stderr)
            continue
        frag = open(path).read()
        h2 = re.search(r"<h2[^>]*>(.*?)</h2>", frag, re.S)
        title = re.sub(r"<[^>]+>", "", h2.group(1)).strip() if h2 else title
        # give every h3 an id so the rail can link to it
        subs = []

        def sub_id(m):
            text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            sid = f"{key}-" + re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
            subs.append((sid, text))
            return f'<h3 id="{sid}"{m.group(1)}>{m.group(2)}</h3>'
        frag = re.sub(r"<h3([^>]*)>(.*?)</h3>", sub_id, frag, flags=re.S)
        if f'id="{key}"' not in frag:
            frag = frag.replace("<section", f'<section id="{key}"', 1)
        body.append(frag)
        nav.append(f'<li class="chapter"><a href="#{key}" data-n="{n}">{html.escape(title)}</a><ol>' +
                   "".join(f'<li><a href="#{sid}">{html.escape(t)}</a></li>' for sid, t in subs) + "</ol></li>")
        cards.append(f'<a href="#{key}"><b data-n="{n}">{html.escape(title)}</b><span>{html.escape(BLURB[key])}</span></a>')
    out = (tpl.replace("{{NAV}}", "\n".join(nav)).replace("{{CARDS}}", "\n".join(cards))
           .replace("{{CHAPTERS}}", "\n\n".join(body)).replace("{{DATE}}", datetime.date.today().isoformat()))
    open(os.path.join(HERE, "index.html"), "w").write(out)
    imgs = set(re.findall(r'src="img/([^"]+)"', out))
    missing = [i for i in imgs if not os.path.exists(os.path.join(HERE, "img", i))]
    print(f"index.html: {len(body)} chapters, {len(imgs)} images" + (f", MISSING {missing}" if missing else ""))


if __name__ == "__main__":
    main()
