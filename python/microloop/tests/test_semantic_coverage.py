"""Unit tests for the Semantic Coverage Engine."""

import numpy as np
from microloop.internal.coverage import (
    CoverageEngine,
    SemanticRegion,
    TextVectorizer,
    calibrate_semantic_boundaries,
)


def test_text_vectorizer_fit_transform():
    texts = [
        "I was charged twice for my subscription",
        "Duplicate charge on my card statement",
        "Please cancel my wire transfer immediately",
    ]
    vec = TextVectorizer.fit(texts, max_features=50)
    assert len(vec.vocab) > 5
    v1 = vec.transform("I was charged twice")
    assert isinstance(v1, np.ndarray)
    assert np.isclose(np.linalg.norm(v1), 1.0)
    
    # Serialization
    d = vec.to_dict()
    vec2 = TextVectorizer.from_dict(d)
    v2 = vec2.transform("I was charged twice")
    assert np.allclose(v1, v2)

def test_semantic_region_containment_and_margin():
    proto_v = [1.0, 0.0, 0.0]
    region = SemanticRegion(
        region_id="sem-001",
        site="test.site",
        choice="refund",
        prototype_state={"request": "refund please"},
        prototype_vector=proto_v,
        radius=0.3,
        negative_margin=0.5,
        member_count=5,
        confidence=0.9,
        status="ACTIVE",
    )
    
    # Exact match: distance 0.0 <= 0.3 -> inside
    inside, dist = region.contains(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    assert inside is True
    assert dist == 0.0
    
    # Far away vector: distance 1.0 > 0.3 -> outside
    inside, dist = region.contains(np.array([0.0, 1.0, 0.0], dtype=np.float32))
    assert inside is False
    assert dist == 1.0
    
    # Near negative margin: distance 0.4 > negative_margin * 0.75 (0.375) -> outside
    inside, dist = region.contains(np.array([0.6, 0.8, 0.0], dtype=np.float32))
    assert inside is False

def test_coverage_engine_hierarchy():
    vec = TextVectorizer.fit(["refund please", "cancel transfer", "stolen card"])
    v_refund = vec.transform("refund please")
    
    reg_active = SemanticRegion(
        region_id="sem-refund",
        site="test",
        choice="refund",
        prototype_state={"request": "refund please"},
        prototype_vector=v_refund.tolist(),
        radius=0.4,
        negative_margin=0.8,
        member_count=5,
        confidence=0.9,
        status="ACTIVE",
    )
    
    reg_shadow = SemanticRegion(
        region_id="sem-shadow",
        site="test",
        choice="specialist",
        prototype_state={"request": "stolen card"},
        prototype_vector=vec.transform("stolen card").tolist(),
        radius=0.4,
        negative_margin=0.8,
        member_count=5,
        confidence=0.8,
        status="SHADOW",
    )
    
    engine = CoverageEngine(
        exact_coverage={'{"request":"exact match"}': {"choice": "refund", "confidence": 1.0}},
        semantic_regions=[reg_active, reg_shadow],
        vectorizer=vec,
    )
    
    # 1. Exact match level
    level, reg, conf = engine.route({"request": "exact match"})
    assert level == "exact"
    assert reg["choice"] == "refund"
    
    # 2. Semantic active level
    level, reg, conf = engine.route({"request": "refund please"})
    assert level == "semantic"
    assert reg["choice"] == "refund"
    
    # 3. Shadow candidate level (never served directly)
    level, reg, conf = engine.route({"request": "stolen card"})
    assert level == "shadow"
    
    # 4. Unknown outside coverage level
    level, reg, conf = engine.route({"request": "completely unknown request unrelated to banking"})
    assert level == "outside_coverage"

def test_coverage_engine_ambiguity_abstention():
    # Two competing regions with different choices covering the same region
    v = [1.0, 0.0]
    reg_a = SemanticRegion("a", "site", "refund", {}, v, 0.5, 0.8, 3, 0.9, "ACTIVE")
    reg_b = SemanticRegion("b", "site", "specialist", {}, v, 0.5, 0.8, 3, 0.9, "ACTIVE")
    
    vec = TextVectorizer(vocab={"a": 0, "b": 1}, idf={"a": 1.0, "b": 1.0})
    engine = CoverageEngine(
        exact_coverage={},
        semantic_regions=[reg_a, reg_b],
        vectorizer=vec,
    )
    
    level, reg, dist = engine.route({"request": "a"})
    assert level == "ambiguous"
    assert reg is None

def test_calibrate_semantic_boundaries_negative_margins():
    texts = [
        "refund my duplicate charge",
        "please refund second payment",
        "double charge refund request",
        "where is my pending transfer",
        "status of incoming wire",
        "check transfer clearance time",
    ]
    vec = TextVectorizer.fit(texts)
    records = [
        {"state": {"request": "refund my duplicate charge"}, "choice": "refund"},
        {"state": {"request": "please refund second payment"}, "choice": "refund"},
        {"state": {"request": "double charge refund request"}, "choice": "refund"},
        {"state": {"request": "where is my pending transfer"}, "choice": "request_information"},
        {"state": {"request": "status of incoming wire"}, "choice": "request_information"},
        {"state": {"request": "check transfer clearance time"}, "choice": "request_information"},
    ]
    
    regions = calibrate_semantic_boundaries(
        records, vec, site_version="test-site", min_region_samples=2
    )
    assert len(regions) > 0
    for r in regions:
        assert r.status == "SHADOW"
        assert r.negative_margin > 0.0
        assert r.radius <= r.negative_margin
        assert r.choice in ("refund", "request_information")


def test_adaptive_margin_tightening_and_counterexample_ingestion():
    vec = TextVectorizer.fit(["refund payment", "do not refund", "wire clearance"])
    proto_v = vec.transform("refund payment")
    region = SemanticRegion(
        region_id="sem-001",
        site="site-v1",
        choice="refund",
        prototype_state={"request": "refund payment"},
        prototype_vector=proto_v.tolist(),
        radius=0.40,
        negative_margin=0.85,
        member_count=5,
        confidence=0.85,
        status="ACTIVE",
    )
    # A counterexample arrives at distance < 0.50 (narrowing negative margin)
    counter_v = vec.transform("refund wire")
    dist_initial = region.distance(counter_v)
    assert dist_initial < region.negative_margin

    tightened = region.tighten_margin(counter_v, safety_factor=0.60)
    assert tightened is True
    assert region.negative_margin == round(dist_initial, 4)
    assert region.radius <= round(dist_initial * 0.60, 4)

    # Far counterexample: must not expand radius
    far_v = np.zeros_like(proto_v)
    assert not region.tighten_margin(far_v)

    # Encroaching counterexample: margin collapse triggers demotion to SHADOW
    encroaching_v = proto_v * 0.98
    region.tighten_margin(encroaching_v)
    assert region.status == "SHADOW"

    # Ingest in CoverageEngine
    fresh_region = SemanticRegion(
        region_id="sem-002",
        site="site-v1",
        choice="refund",
        prototype_state={"request": "refund payment"},
        prototype_vector=proto_v.tolist(),
        radius=0.40,
        negative_margin=0.85,
        member_count=5,
        confidence=0.85,
        status="ACTIVE",
    )
    engine = CoverageEngine(semantic_regions=[fresh_region], vectorizer=vec)
    affected = engine.ingest_counterexample({"request": "refund wire"}, counter_choice="specialist")
    assert "sem-002" in affected
