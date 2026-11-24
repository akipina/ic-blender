"""Stitch the chapter fragments into index.html.

    .venv/bin/python3 blender_native/docs/guide/build_guide.py

Chapters are HTML fragments in chapters/<name>.html (one <section id=name> each, h2 title, h3 subsections);
the navigation rail and the title cards are generated from their headings. Missing chapters are skipped
with a warning so a partial guide still builds.
"""
import datetime
import html
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = [("start", "Getting started"), ("edit", "Editing an existing animal"), ("topology", "Topology"),
         ("sculpt", "Sculpting"), ("scratch", "A rhino from scratch"), ("fit", "Fitting a mesh"),
         ("retarget", "Retargeting"), ("skin", "Skinning and re-skinning"), ("texture", "Textures"),
         ("anim", "Animations"), ("boner", "Bones, limbs and connections"),
         ("scene", "Gatherers, characters, structures and props"), ("blueprint", "The Blueprint panel"),
         ("maps", "Maps and scenarios")]
BLURB = {"start": "Install, import a stock creature, find your way around the SGM and Boner panels.",
         "edit": "Reshape a stock animal: patch feel, control points, symmetrize, validate, export.",
         "topology": "Delete, grow, weld, fill, subdivide, extrude, mirror and attach -- and what a patch costs.",
         "sculpt": "Sculpt Mode brushes on the exact surface, refitted into the patches you touched.",
         "scratch": "New Creature scaffold to a crude rhino with a horn, skin, clips and a combine check.",
         "fit": "Any mesh becomes a patch creature: budgets, the real ceiling, and the fit-to-rig split.",
         "retarget": "Make a working creature take another shape -- and the limits of a closest-point fit.",
         "skin": "Which bone moves which control point, and how to change it.",
         "texture": "Paint the creature's own texture, edit UVs, transfer a source mesh's skin.",
         "anim": "Play, edit and create clips as Blender Actions and export them.",
         "boner": "Limbs, classes, connection rings, ring starts, bone edits, joint limits and IK chains.",
         "scene": "Start from a stock gatherer, character, structure or prop, edit its mesh any way, export only what changed.",
         "blueprint": "Name, kind, footprint, offset and sounds of a scene object, and a folder ready to copy into the game.",
         "maps": "Open one of the game's maps, change its ground, water, ground textures and placed things, and save it again."}


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
    try:
        ver = subprocess.run(["/snap/bin/blender", "--version"], capture_output=True, text=True, timeout=20).stdout.split("\n")[0].replace("Blender ", "")
    except Exception:
        ver = "5.2"
    out = (tpl.replace("{{NAV}}", "\n".join(nav)).replace("{{CARDS}}", "\n".join(cards))
           .replace("{{CHAPTERS}}", "\n\n".join(body)).replace("{{DATE}}", datetime.date.today().isoformat())
           .replace("{{BLENDER}}", ver))
    open(os.path.join(HERE, "index.html"), "w").write(out)
    imgs = set(re.findall(r'src="img/([^"]+)"', out))
    missing = [i for i in imgs if not os.path.exists(os.path.join(HERE, "img", i))]
    print(f"index.html: {len(body)} chapters, {len(imgs)} images" + (f", MISSING {missing}" if missing else ""))


if __name__ == "__main__":
    main()
