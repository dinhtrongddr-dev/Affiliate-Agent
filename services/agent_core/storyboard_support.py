"""Local, server-owned reference snapshots for storyboard drafting and generation."""
import copy
import hashlib
from pathlib import Path

from .clips import IMAGE_EXTENSIONS, clip_dir, is_allowed_source
from .creation_branches import normalise_characters, reference_id, validate_branch
from .videogen.providers import ProviderError

_RIGHTS = {"owned", "licensed", "permission", "official"}


def _entry(rid, role, path):
    path = Path(path).resolve()
    return {"id": rid, "role": role, "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def prepare_references(settings, job_id, brief, product, main_image, clips) -> list[dict]:
    """Accept an already-authorized product image and contained local snapshots only."""
    result = []
    if main_image and Path(main_image).is_file():
        result.append(_entry("product-main", "product", main_image))
    if brief.get("creation_branch") == "template_series":
        snapshots = brief.get("template_references", [])
        if not isinstance(snapshots, list):
            raise ProviderError("Invalid series reference snapshot")
        root = (Path(settings.assets) / "creative_series").resolve()
        for raw in snapshots:
            if not isinstance(raw, dict):
                raise ProviderError("Invalid series reference snapshot")
            rid = reference_id(raw.get("id"))
            cid = reference_id(raw.get("character_id"))
            path = Path(raw.get("path", "")).resolve()
            if (not rid or not cid or raw.get("rights") not in _RIGHTS
                    or not path.is_relative_to(root) or not path.is_file()
                    or path.suffix.lower() not in IMAGE_EXTENSIONS):
                raise ProviderError("Series reference is missing, outside assets, or lacks rights")
            entry = _entry(rid, "character:" + cid, path)
            if entry["sha256"] != raw.get("sha256"):
                raise ProviderError("Series reference hash changed")
            result.append(entry)
    base = clip_dir(settings, job_id).resolve()
    for clip in clips or []:
        if not isinstance(clip, dict) or clip.get("kind") not in {"image", "video"}:
            continue
        cid = reference_id(clip.get("id"))
        filename = clip.get("thumb") if clip.get("kind") == "video" else clip.get("file")
        if not filename:
            continue
        path = (base / str(filename)).resolve()
        if (not cid or not path.is_relative_to(base) or not path.is_file()
                or path.suffix.lower() not in IMAGE_EXTENSIONS
                or clip.get("rights", "owned") not in _RIGHTS
                or not is_allowed_source(settings, {"id": job_id}, str(path))):
            continue
        role = "video-thumbnail" if clip.get("kind") == "video" else "uploaded"
        result.append(_entry("clip-" + cid, role, path))
    ids = [item["id"] for item in result]
    if len(ids) != len(set(ids)):
        raise ProviderError("Duplicate reference IDs")
    if len(result) > 12:
        raise ProviderError("Storyboard supports at most 12 reference images")
    return result


def reference_legend(manifest) -> str:
    return "\n".join(f"Attachment {index}: ID={item['id']}; role={item['role']}"
                     for index, item in enumerate(manifest, 1))


def attach_references(plan, brief, manifest) -> dict:
    branch = validate_branch(brief.get("creation_branch"))
    plan["creation_branch"] = branch
    plan["storyboard_version"] = 1
    plan["reference_manifest"] = copy.deepcopy(manifest)
    allowed = {item["id"] for item in manifest}
    if branch == "template_series":
        template = brief.get("template", {})
        plan["style"] = template.get("style", "")
        plan["setting"] = template.get("setting", "")
        plan["continuity_rules"] = copy.deepcopy(template.get("continuity_rules", []))
        plan["characters"] = normalise_characters(template.get("characters", []))
        for character in plan["characters"]:
            matches = [item["id"] for item in manifest
                       if item["role"] == "character:" + character["id"]]
            if matches:
                character["reference_id"] = matches[-1]
            else:
                character.pop("reference_id", None)
    cast = [c["reference_id"] for c in plan.get("characters", [])
            if c.get("reference_id") in allowed]
    defaults = list(dict.fromkeys(cast + [item["id"] for item in manifest]))[:6]
    for scene in plan.get("scenes", []):
        refs = scene.get("reference_ids")
        if refs is None or refs == []:
            refs = defaults.copy()
        if (not isinstance(refs, list) or len(refs) > 6
                or any(not isinstance(rid, str) or rid not in allowed for rid in refs)
                or len(refs) != len(set(refs))):
            raise ProviderError("Invalid storyboard reference IDs (maximum 6 per scene)")
        if branch == "template_series":
            refs = list(dict.fromkeys(cast + refs))
            if len(refs) > 6:
                raise ProviderError("Cast and scene references exceed maximum 6 per scene")
        scene["reference_ids"] = refs
    return plan


def reference_errors(plan, brief, manifest) -> list[str]:
    """Missing cast images block generation, while reference-free drafts are allowed."""
    if brief.get("creation_branch") != "template_series":
        return []
    roles = {item["role"] for item in manifest}
    characters = normalise_characters(brief.get("template", {}).get("characters", []))
    return [f"Missing reference image for character {c['id']}" for c in characters
            if "character:" + c["id"] not in roles]
