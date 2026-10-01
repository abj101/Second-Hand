import numpy as np
import pytest
from PIL import Image, ImageDraw

from loop.adaptive import concepts as cl
from loop.adaptive import student as st


def test_cluster_embeddings_groups_by_appearance_and_caps():
    a, b = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    near = a + np.array([0.0, 0.05, 0.0])
    assert st.cluster_embeddings([a, b, near, b]) == [[0, 2], [1, 3]]
    many = np.eye(10)
    assert len(st.cluster_embeddings(many, cap=4)) == 4


def test_pick_threshold_and_prototype_thresholds_are_independent():
    # perfect separation: cut at the lowest true positive, minus the margin
    t = st.pick_threshold([0.9, 0.85, 0.5, 0.4], [True, True, False, False], 2)
    assert t == pytest.approx(0.85 - st.THRESHOLD_MARGIN)
    assert st.pick_threshold([0.9], [False], 1) is None  # no true positive: no evidence
    # prototype 0 (Strike-like) scores high, prototype 1 (Defend-like) scores 0.74-0.89.
    # A GT belongs to the prototype that matched it; frames: (scores, proto, gt idx, n_gt)
    frames = [
        (np.array([0.97, 0.96, 0.60]), np.array([0, 0, 0]), [0, -1, -1], 1),
        (np.array([0.80, 0.78, 0.76, 0.55]), np.array([1, 1, 1, 1]), [0, 1, 2, -1], 3),
    ]
    p0, p1 = st.prototype_thresholds(frames, 2)
    assert p0 is None  # one GT only: below MIN_EVIDENCE, caller falls back
    assert p1 == pytest.approx(0.76 - st.THRESHOLD_MARGIN)
    assert st.concept_threshold(frames) < 0.8  # the shared threshold still reaches the low mode


def test_suppress_duplicates_drops_shifted_copy_but_keeps_neighbour():
    boxes = np.array([[0.0, 0.0, 0.2, 0.3], [0.03, 0.02, 0.23, 0.32], [0.15, 0.0, 0.35, 0.3]])
    scores = np.array([0.9, 0.8, 0.7])
    assert st.suppress_duplicates(boxes, scores, 0.6).tolist() == [0, 2]
    assert st.suppress_duplicates(boxes, scores, 0.99).tolist() == [0, 1, 2]


def test_match_boxes_is_one_to_one_and_score_ordered():
    gt = [(0.0, 0.0, 0.2, 0.2)]
    preds = [(0.0, 0.0, 0.18, 0.2), (0.0, 0.0, 0.2, 0.2)]
    assert st.match_boxes(preds, gt, False, scores=[0.9, 0.8]) == [0, -1]  # best score claims it
    assert st.match_boxes([(0.09, 0.09, 0.12, 0.12)], gt, True) == [0]  # small: center-in-box


def test_cosine_distance_on_embedding_bytes():
    a = np.array([1, 0, 0, 0], np.float16).tobytes()
    assert st.cosine_distance(a, a) == pytest.approx(0.0, abs=1e-6)
    assert st.cosine_distance(a, np.array([0, 1, 0, 0], np.float16).tobytes()) == pytest.approx(1.0)
    assert st.cosine_distance(a, b"") == float("inf")


def _scene(x: int) -> Image.Image:
    img = Image.fromarray(np.random.default_rng(0).integers(0, 40, (360, 640, 3), dtype=np.uint8))
    d = ImageDraw.Draw(img)
    d.rectangle((x, 100, x + 84, 184), fill=(220, 30, 30))
    d.ellipse((x + 15, 115, x + 69, 169), fill=(250, 250, 40))
    d.rectangle((x + 30, 130, x + 54, 154), fill=(20, 40, 220))
    return img


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("needs CUDA")
    d = tmp_path_factory.mktemp("lib")
    lib = cl.teach(cl.empty_library("g"), _scene(100), [{"category": "thing", "bbox": [100, 100, 184, 184]}], library_dir=d)
    try:
        compiled = st.compile_library(lib, d, mode="phase0")
    except OSError:
        pytest.skip("DINOv2-small weights not cached")
    cl.save(lib, d)
    cl.save(compiled, d)
    return d


def test_recognizer_finds_moved_object_and_tracker_keeps_identity(library):
    rec = st.StudentRecognizer(library)
    dets = rec.detect(_scene(400))
    best = max(dets, key=lambda d: d.score)
    assert best.concept_id == "thing" and best.score > 0.8
    assert abs((best.bbox[0] + best.bbox[2]) / 2 * 640 - 442) < 25
    assert best.embedding.dtype == np.float16 and best.embedding.shape == (384,)

    tr = st.StudentTracker(library)
    assert tr.status == "ok" and tr.diagnostics == {"available": False}
    first = [h for h in tr.update(_scene(400)) if h.detector_label == "thing" and h.confidence > 0.9]
    again = [h for h in tr.update(_scene(404)) if h.detector_label == "thing" and h.confidence > 0.9]
    assert first and again and first[0].track_id == again[0].track_id
    assert tr.diagnostics["available"] and tr.diagnostics["detections"] >= 1


def test_tracker_without_library_reports_status_instead_of_raising(tmp_path):
    tr = st.StudentTracker(tmp_path)
    assert tr.status != "ok" and tr.update(Image.new("RGB", (64, 64))) == ()
