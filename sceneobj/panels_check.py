"""The *Check* panel of a scene object (IC Game tab): a Check button and a Problems box.

*Check* works out what Export Entity Folder would write, in memory, and runs three checkers over it (``scenecheck``): the model's load rules
(``validate_scene_sgm``), the blueprint's 14 rules (``ICParse.entity``, ``EBP-V01`` .. ``EBP-V14``) and the hub's validator over the item folder
(``ICParse.hub.check_files``), plus whether the model file changed on disk since it was opened here (``scenesafety.source_error``). Nothing is
written. The findings are kept on the entity (``bz_check``) and listed in the Problems box until the next Check. Export Entity Folder runs the
same checks and refuses on an error unless *Export anyway* is ticked.
"""
from __future__ import annotations

import bpy
from . import ui_kit

from . import panels_blueprint as BP

ORDER = 90
KEY_CHECK = "bz_check"                     # on the entity's holder: the last Check's findings (scenecheck.to_json)
KEY_CHECK_HEAD = "bz_check_head"           # ... and its one-line summary
SHOW_CHECK_PANEL = False                   # hidden: the Game panel of the IC Game tab draws this as its step 1, Check
MAX_LINES = 12                             # lines of the Problems box before "... and N more"


def run_check(context, obj, mode: str = BP.MODE_NEW) -> list:
    """The findings of a Check of the entity ``obj`` belongs to (a list of ``scenecheck.Problem``, worst first). ``mode``: which Export Entity
    Folder choice to check, ``MODE_NEW`` (the project's own name) or ``MODE_REPLACE`` (the name and folder of the thing it replaces: the item
    path, the hub's rules and the blueprint's name rules differ). Never raises for a problem with the entity itself: that is a finding."""
    sc = BP._addon("scenecheck")
    ent = BP.entity_of(obj)
    problems = []
    if ent is None:
        return [sc.Problem(sc.ERROR, "file", "", "Select a gatherer, character, structure or prop.")]
    changed = BP._addon("scenesafety").source_error(ent.parts[0])
    if changed:
        problems.append(sc.Problem(sc.ERROR, "file", "the model file", changed))
    if BP._stamp(ent, "bz_project") == "EFFECT":                          # an effect model: no blueprint; its model and effect files are what there is to check
        return sc.unique(sorted(problems + _effect_problems(context, obj, ent, sc), key=lambda p: -sc.RANK[p.severity]))
    try:
        b = BP.build_entity(context, obj, mode=mode, refuse_errors=False)
    except BP.Refused as e:
        problems.append(sc.Problem(sc.ERROR, "export", "", str(e)))
        return problems
    except Exception as e:                                                # noqa: BLE001 -- a crash of the build is a finding, not a broken button
        problems.append(sc.Problem(sc.ERROR, "export", "", f"The export could not be worked out: {e}"))
        return problems
    problems += BP.check_build(context, b)
    problems += [sc.Problem(sc.WARN if is_warning_note(n) else sc.INFO, "export", "", n) for n in b.notes]
    return sc.unique(sorted(problems, key=lambda p: -sc.RANK[p.severity]))


def is_warning_note(note: str) -> bool:
    """Whether an export note is something the modder has to act on: a clip nothing plays (a new animation that does nothing in the game)."""
    return BP._addon("notelevels").is_warning(note)


def _effect_problems(context, obj, ent, sc) -> list:
    """The findings for an effect model project (``fxpanel``): the model's load rules over what would be written, and the effect files'
    properties against the game's tables (``fxcore.export_problems``: a refusal is an error, the rest warnings)."""
    out = []
    fx = BP._addon("fxpanel")
    try:
        files, _rel, item, _name, _folder, _rebuilt, _kept, _notes, _fxnotes = fx.effect_item_files(context, ent, refuse_errors=False)
        textures = {p[len("data/"):]: b for p, b in files.items() if p.startswith("data/art/textures/")}
        sgm = next(b for p, b in files.items() if p.startswith("data/art/fx/") and p.endswith(".sgm"))
        out += sc.model_problems(sgm, files=textures, resolver=None)
        out += sc.effect_item_problems(files, item, sc.stock_index(BP.game_dir()[0]))     # the hub checker over the item, said in modder's words
    except BP.Refused as e:
        out.append(sc.Problem(sc.ERROR, "export", "", str(e)))
    except Exception as e:                                                # noqa: BLE001 -- a crash of the build is a finding
        out.append(sc.Problem(sc.ERROR, "export", "", f"The export could not be worked out: {e}"))
    refused, notes = BP._addon("fxcore").export_problems(fx._source(ent))
    out += [sc.Problem(sc.ERROR, "effect", "", r) for r in refused]
    out += [sc.Problem(sc.WARN, "effect", "", n) for n in notes]
    return out


def head_of(problems) -> str:
    n_err = sum(1 for p in problems if p.severity == "ERROR")
    n_warn = sum(1 for p in problems if p.severity == "WARN")
    if not n_err and not n_warn:
        return "No problems: the game would accept these files."
    return (f"{n_err} error{'s' if n_err != 1 else ''}, {n_warn} warning{'s' if n_warn != 1 else ''}"
            + (": the game would refuse or mis-load these files." if n_err else "."))


class BZ_OT_scene_check(bpy.types.Operator):
    """Check what Export Entity Folder would write against the game's rules (nothing is written)"""
    bl_idname = "bz.scene_check"
    bl_label = "Check"
    bl_options = {"REGISTER"}
    mode: bpy.props.EnumProperty(name="As", items=[(BP.MODE_NEW, "A new name", "Check the export under a name of your own"),
                                                    (BP.MODE_REPLACE, "Replace the original", "Check the export that replaces the original thing")],
                                 default=BP.MODE_NEW)

    @classmethod
    def poll(cls, context):
        return BP._entity_poll(context)

    def execute(self, context):
        sc = BP._addon("scenecheck")
        ent = BP.entity_of(context.active_object)
        problems = run_check(context, context.active_object, self.mode)
        ent.holder[KEY_CHECK] = sc.to_json(problems)
        head = head_of(problems)
        if self.mode == BP.MODE_REPLACE:
            head += " (checked as a replacement of the original)"
        ent.holder[KEY_CHECK_HEAD] = head
        self.report({"WARNING"} if sc.worst(problems) in ("ERROR", "WARN") else {"INFO"}, f"Check: {head}")
        return {"FINISHED"}


def draw_check(layout, obj) -> None:
    """The panel's body for ``obj``: the button, the last Check's summary and its findings. Never changes anything."""
    ent = BP.entity_of(obj)
    if ent is None:
        layout.label(text="Select a gatherer, character, structure or prop.")
        return
    sc = BP._addon("scenecheck")
    layout.operator("bz.scene_check", text="Check", icon="CHECKMARK").mode = BP.MODE_NEW
    target = BP.replace_target(ent)
    if target is not None:
        shown = f"{target.folder + '/' if target.folder else ''}{target.name}"
        layout.operator("bz.scene_check", text=f"Check as replacing {shown}", icon="CHECKMARK").mode = BP.MODE_REPLACE
    raw = ent.holder.get(KEY_CHECK)
    if raw is None:
        BP._hint(layout, "Tests what Export Entity Folder would write against the game's rules. Nothing is written.")
        return
    problems = sc.from_json(str(raw))
    box = layout.box()
    box.label(text="Problems", icon="ERROR" if sc.worst(problems) == "ERROR" else "CHECKMARK" if not sc.worst(problems) else "INFO")
    BP._hint(box, str(ent.holder.get(KEY_CHECK_HEAD, "")) + " (as of the last Check: press Check again after changes.)")
    shown = [p for p in problems if p.severity != "INFO"][:MAX_LINES]
    for p in shown:
        if p.severity == "ERROR":
            BP._alert(box, p.line())
        else:
            BP._hint(box, "Warning: " + p.line())
    more = len([p for p in problems if p.severity != "INFO"]) - len(shown)
    if more > 0:
        BP._hint(box, f"... and {more} more.")


class BZ_PT_scene_check(bpy.types.Panel):
    bl_label = "Check"
    bl_idname = "BZ_PT_scene_check"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_order = 50

    @classmethod
    def poll(cls, context):
        return SHOW_CHECK_PANEL and BP.entity_of(context.active_object) is not None

    def draw(self, context):
        draw_check(self.layout, context.active_object)


CLASSES = (BZ_OT_scene_check, BZ_PT_scene_check)
