import hashlib
from types import SimpleNamespace

import pytest

from agent_core.storyboard_support import (
    attach_references, prepare_references, reference_errors, reference_legend,
)
from agent_core.videogen.providers import ProviderError


@pytest.fixture
def setup(tmp_path):
    settings = SimpleNamespace(assets=tmp_path / "assets", work_dir=tmp_path / "work")
    path = settings.assets / "creative_series" / "family" / "cast.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"image")
    ref = {"id": "cast-a", "character_id": "a", "path": str(path),
           "sha256": hashlib.sha256(b"image").hexdigest(), "rights": "owned"}
    brief = {"creation_branch": "template_series", "template_references": [ref],
             "template": {"style": "watercolor", "characters": [{"id": "a", "name": "An"}]}}
    return settings, brief, path


def test_trusted_cast_and_server_owned_snapshot(setup):
    settings, brief, path = setup
    brief["template"].update(setting="family home", continuity_rules=["stable wardrobe"])
    refs = prepare_references(settings, "job123", brief, {}, None, [])
    plan = attach_references({"style": "wrong", "characters": [], "scenes": [{}]}, brief, refs)
    assert plan["style"] == "watercolor"
    assert plan["setting"] == "family home"
    assert plan["continuity_rules"] == ["stable wardrobe"]
    assert plan["characters"][0]["reference_id"] == "cast-a"
    assert plan["scenes"][0]["reference_ids"] == ["cast-a"]
    assert reference_errors(plan, brief, refs) == []
    assert reference_legend(refs) == "Attachment 1: ID=cast-a; role=character:a"
    assert "template_references" in brief


@pytest.mark.parametrize("change", ["hash", "rights", "outside"])
def test_reject_untrusted_series(setup, tmp_path, change):
    settings, brief, path = setup
    ref = brief["template_references"][0]
    if change == "hash":
        path.write_bytes(b"changed")
    elif change == "rights":
        ref["rights"] = "unknown"
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"image")
        ref["path"] = str(outside)
    with pytest.raises(ProviderError):
        prepare_references(settings, "job123", brief, {}, None, [])


def test_upload_containment_and_rights(setup, tmp_path):
    settings, brief, path = setup
    base = settings.assets / "clips" / "job123"
    base.mkdir(parents=True)
    (base / "valid.png").write_bytes(b"upload")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    clips = [{"id": "ok", "kind": "image", "file": "valid.png"},
             {"id": "bad", "kind": "image", "file": str(outside)},
             {"id": "rights", "kind": "image", "file": "valid.png", "rights": "unknown"}]
    refs = prepare_references(settings, "job123", brief, {}, path, clips)
    assert [r["id"] for r in refs] == ["product-main", "cast-a", "clip-ok"]


def test_unknown_model_refs_rejected(setup):
    settings, brief, path = setup
    with pytest.raises(ProviderError):
        attach_references({"scenes": [{"reference_ids": ["invented"]}]}, brief, [])


def test_video_thumbnail_containment_rights_and_attachment_order(setup, tmp_path):
    settings, brief, path = setup
    base = settings.assets / "clips" / "job123"
    base.mkdir(parents=True)
    (base / "frame.jpg").write_bytes(b"frame")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    clips = [{"id": "video", "kind": "video", "thumb": "frame.jpg"},
             {"id": "escape", "kind": "video", "thumb": str(outside)},
             {"id": "missing", "kind": "video", "file": "video.mp4"},
             {"id": "denied", "kind": "video", "thumb": "frame.jpg", "rights": "unknown"}]
    refs = prepare_references(settings, "job123", brief, {}, None, clips)
    assert [r["id"] for r in refs] == ["cast-a", "clip-video"]
    assert refs[1]["role"] == "video-thumbnail"
    assert refs[1]["path"] == str((base / "frame.jpg").resolve())
    assert reference_legend(refs).splitlines()[1] == "Attachment 2: ID=clip-video; role=video-thumbnail"


def test_reference_free_draft_reports_generation_blocker(setup):
    settings, brief, path = setup
    plan = attach_references({"scenes": [{}]}, brief, [])
    assert plan["scenes"][0]["reference_ids"] == []
    assert reference_errors(plan, brief, []) == ["Missing reference image for character a"]


def test_scene_limit_and_cast_first(setup):
    settings, brief, path = setup
    refs = [{"id": f"p{i}", "role": "product"} for i in range(8)]
    refs.append({"id": "cast-a", "role": "character:a"})
    plan = attach_references({"scenes": [{}]}, brief, refs)
    assert plan["scenes"][0]["reference_ids"] == ["cast-a", "p0", "p1", "p2", "p3", "p4"]
    with pytest.raises(ProviderError):
        attach_references({"scenes": [{"reference_ids": [f"p{i}" for i in range(7)]}]}, brief, refs)


def test_explicit_series_references_include_cast_without_silent_truncation(setup):
    settings, brief, path = setup
    refs = [{"id": "cast-a", "role": "character:a"}]
    refs += [{"id": f"p{i}", "role": "product"} for i in range(6)]
    plan = attach_references({"scenes": [{"reference_ids": ["p0"]}]}, brief, refs)
    assert plan["scenes"][0]["reference_ids"] == ["cast-a", "p0"]
    with pytest.raises(ProviderError):
        attach_references({"scenes": [{"reference_ids": [f"p{i}" for i in range(6)]}]}, brief, refs)
