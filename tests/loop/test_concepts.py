from PIL import Image

from loop.adaptive import concepts as cl


def test_teach_save_load_roundtrip(tmp_path):
    lib = cl.empty_library("sts2")
    img = Image.new("RGB", (200, 100), (10, 20, 30))
    taught = cl.teach(lib, img, [{"category": "card", "bbox": [20, 10, 60, 90], "label": "Defend"}], library_dir=tmp_path)
    assert taught.revision.parent_id == lib.revision.revision_id
    card = taught.concept("card")
    assert card.exemplars[0].bbox == (0.1, 0.1, 0.3, 0.9) and card.exemplars[0].label == "Defend"
    sha = cl.store_artifact(tmp_path, b"weights", ".bin")
    art = cl.StudentArtifact("test", sha, (cl.Prototype("card/0", (0,), 0.8),))
    compiled = cl.with_students(taught, {"card": art})
    cl.save(lib, tmp_path); cl.save(taught, tmp_path); cl.save(compiled, tmp_path)
    loaded = cl.load(tmp_path)
    assert loaded == compiled
    assert cl.artifact_path(tmp_path, sha).read_bytes() == b"weights"
    # New evidence invalidates the compiled student.
    again = cl.teach(loaded, img, [{"category": "card", "bbox": [100, 10, 140, 90]}], library_dir=tmp_path)
    assert again.concept("card").student is None and len(again.concept("card").exemplars) == 2
    # Tampering is detected.
    p = tmp_path / f"{compiled.revision.revision_id}.json"
    p.write_text(p.read_text().replace('"Defend"', '"Strike"'))
    try:
        cl.load(tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("tamper not detected")
