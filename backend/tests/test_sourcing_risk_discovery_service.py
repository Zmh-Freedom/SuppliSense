"""Behavior tests for local-first sourcing-risk supplier discovery."""

from unittest.mock import Mock

from app.domains.sourcing_risk import discovery_service
from app.domains.sourcing import supplier_repo


def _requirement(**overrides: object) -> dict:
    return {
        "category": "摄像头",
        "specification": "IP67",
        "region": "华东",
        "qualifications": "ISO9001",
        **overrides,
    }


def _policy(**overrides: object) -> dict:
    return {"minimum_candidate_count": 3, **overrides}


def _candidate(name: str, **overrides: object) -> dict:
    return {
        "supplier_id": f"supplier-{name}",
        "supplier_name": name,
        "categories": ["摄像头", "IP67"],
        "specifications": ["IP67"],
        "regions": ["华东"],
        "status": "active",
        "qualifications": ["ISO9001"],
        "capacity": {"available": 100},
        "updated_at": "2026-08-12T00:00:00+00:00",
        "match_reasons": ["category:摄像头"],
        **overrides,
    }


def test_local_candidates_prevent_external_provider_call(monkeypatch):
    """Removing local sufficiency short-circuit would make unnecessary external calls."""
    local = [_candidate("a"), _candidate("b"), _candidate("c")]
    monkeypatch.setattr(discovery_service, "search_local_suppliers", lambda *_: local)
    tianyancha = Mock()
    web = Mock()
    contact_enrichment = Mock()
    monkeypatch.setattr(discovery_service, "_search_tianyancha_candidates", tianyancha)
    monkeypatch.setattr(discovery_service, "_search_web_candidates", web)
    monkeypatch.setattr(discovery_service, "_enrich_external_contacts", contact_enrichment)

    result = discovery_service.discover_candidates(_requirement(), _policy())

    assert result["source"] == "local"
    assert result["local_candidates"] == local
    assert result["external_status"] == "not_required"
    tianyancha.assert_not_called()
    web.assert_not_called()
    contact_enrichment.assert_not_called()


def test_insufficient_local_candidates_retain_local_results_and_stage_external(monkeypatch):
    """Replacing local evidence on fallback would hide the local-first search result."""
    local = [_candidate("local")]
    monkeypatch.setattr(discovery_service, "search_local_suppliers", lambda *_: local)
    monkeypatch.setattr(
        discovery_service,
        "_search_tianyancha_candidates",
        lambda *_: [
            {
                "supplier_name": "外部公司一",
                "categories": ["摄像头"],
                "specifications": ["IP67"],
                "regions": ["华东"],
                "qualifications": ["ISO9001"],
                "source_reference": "tyc:1",
            },
            {
                "supplier_name": "外部公司二",
                "categories": ["摄像头"],
                "specifications": ["IP67"],
                "regions": ["华东"],
                "qualifications": ["ISO9001"],
                "source_reference": "tyc:2",
            },
        ],
    )
    monkeypatch.setattr(discovery_service, "_enrich_external_contacts", lambda candidates: candidates)

    result = discovery_service.discover_candidates(_requirement(), _policy())

    assert result["source"] == "local_and_external"
    assert result["local_candidates"] == local
    assert result["external_status"] == "staged"
    assert [item["supplier_name"] for item in result["external_candidates"]] == ["外部公司一", "外部公司二"]
    assert all(item["status"] == "staged_candidate" for item in result["external_candidates"])
    assert all(item["supplier_id"] is None and item["company_id"] is None for item in result["external_candidates"])


def test_external_candidates_are_staged_without_supplier_or_company_id():
    """External discovery must not create or resolve durable supplier identities."""
    staged = discovery_service.stage_external_candidates(
        "run-id", [{"name": "外部公司", "source_reference": "tyc:1"}]
    )

    assert staged[0]["status"] == "staged_candidate"
    assert staged[0]["supplier_id"] is None
    assert staged[0]["company_id"] is None
    assert staged[0]["run_id"] == "run-id"


def test_web_provider_returns_unverified_company_leads_without_writing(monkeypatch):
    class Response:
        text = """
        <li class="b_algo">
          <h2><a href="https://steel.example.com">华东钢材供应有限公司 - 产品中心</a></h2>
          <div class="b_caption"><p>主营钢板、型钢和不锈钢材料。</p></div>
        </li>
        <li class="b_algo">
          <h2><a href="https://noise.example.com">钢材行业资讯</a></h2>
          <div class="b_caption"><p>行业新闻，不是供应商。</p></div>
        </li>
        """

        def raise_for_status(self):
            return None

    request = Mock(return_value=Response())
    monkeypatch.setattr(discovery_service.httpx, "get", request)
    monkeypatch.setattr(discovery_service, "_search_tianyancha_candidates", lambda *_: [])
    monkeypatch.setattr(
        "app.services.tianyancha_client.search_companies",
        lambda **_kwargs: {"items": [], "total": 0},
    )
    monkeypatch.setattr(discovery_service, "_enrich_external_contacts", lambda candidates: candidates)
    monkeypatch.setattr(discovery_service.settings, "SUPPLIER_DISCOVERY_WEB_MAX_RESULTS", 5)
    monkeypatch.setattr(discovery_service.settings, "SUPPLIER_DISCOVERY_WEB_ENABLED", True)

    result = discovery_service.search_external_provider({"category": "钢材", "region": "华东"})

    assert len(result) == 1
    assert result[0]["supplier_name"] == "华东钢材供应有限公司"
    assert result[0]["verification_status"] == "unverified"
    assert result[0]["source_reference"] == "https://steel.example.com"
    assert request.call_count == 1


def test_web_candidate_is_verified_by_exact_tianyancha_identity_without_writing(monkeypatch):
    monkeypatch.setattr(
        "app.services.tianyancha_client.search_companies",
        lambda **_kwargs: {
            "items": [{
                "name": "华东钢材供应有限公司",
                "unifiedSocialCreditCode": "91310000TEST",
                "legalPersonName": "张三",
                "regStatus": "存续",
            }],
            "total": 1,
        },
    )

    result = discovery_service._verify_web_candidates_with_tianyancha([
        {
            "supplier_name": "华东钢材供应有限公司",
            "source": "web_search",
            "verification_status": "unverified",
        },
    ])

    assert result[0]["identity_status"] == "exact"
    assert result[0]["identity_confidence"] == 0.98
    assert result[0]["tianyancha_verified"] is True
    assert result[0]["tianyancha_unified_social_credit_code"] == "91310000TEST"


def test_web_candidate_with_multiple_similar_tianyancha_results_requires_review(monkeypatch):
    monkeypatch.setattr(
        "app.services.tianyancha_client.search_companies",
        lambda **_kwargs: {
            "items": [
                {"name": "华东钢材供应有限公司"},
                {"name": "华东钢材供应集团有限公司"},
            ],
            "total": 2,
        },
    )

    result = discovery_service._verify_web_candidates_with_tianyancha([
        {"supplier_name": "华东钢材供应", "source": "web_search"},
    ])

    assert result[0]["identity_status"] == "ambiguous"
    assert result[0]["tianyancha_verified"] is False


def test_staged_external_candidate_gets_stable_id_without_creating_identity():
    staged = discovery_service.stage_external_candidates(
        "run-id", [{"supplier_name": "外部公司", "source": "web_search"}]
    )

    assert staged[0]["candidate_id"]
    assert staged[0]["supplier_id"] is None
    assert staged[0]["company_id"] is None


def test_tianyancha_candidates_enrich_contact_details_without_importing(monkeypatch):
    def search_companies(**_kwargs):
        return {"items": [{"name": "华东钢材供应有限公司", "regNumber": "91310000TEST"}], "total": 1}

    monkeypatch.setattr("app.services.tianyancha_client.search_companies", search_companies)
    monkeypatch.setattr(
        "app.services.tianyancha_client.get_company_contact",
        lambda _name: {
            "website_url": "https://www.huadong-steel.example.com",
            "contact_phone": "021-12345678",
            "contact_email": "sales@huadong-steel.example.com",
        },
    )
    monkeypatch.setattr(discovery_service, "_fetch_website_contact_details", lambda _url: {})
    monkeypatch.setattr(discovery_service.settings, "SUPPLIER_DISCOVERY_CONTACT_ENRICHMENT_MAX_CANDIDATES", 1)
    monkeypatch.setattr(discovery_service.settings, "SUPPLIER_DISCOVERY_WEB_MAX_RESULTS", 1)

    result = discovery_service._search_tianyancha_candidates("钢材", "", "")
    enriched = discovery_service._enrich_external_contacts(result)

    assert enriched[0]["website_url"] == "https://www.huadong-steel.example.com"
    assert enriched[0]["contact_phone"] == "021-12345678"
    assert enriched[0]["contact_email"] == "sales@huadong-steel.example.com"
    assert enriched[0]["website_status"] == "unverified"
    assert enriched[0]["contact_status"] == "unverified"


def test_website_contact_enrichment_reads_same_site_contact_page(monkeypatch):
    class Response:
        def __init__(self, url: str, text: str):
            self.url = url
            self.text = text
            self.headers = {"content-type": "text/html; charset=utf-8"}

        def raise_for_status(self):
            return None

    pages = {
        "https://steel.example.com": "<a href='/contact'>联系我们</a>",
        "https://steel.example.com/contact": "联系电话：021-12345678 邮箱：sales@steel.example.com",
    }
    monkeypatch.setattr(
        discovery_service.httpx,
        "get",
        lambda url, **_kwargs: Response(url, pages[url]),
    )

    result = discovery_service._fetch_website_contact_details("https://steel.example.com")

    assert result["website_url"] == "https://steel.example.com"
    assert result["contact_phone"] == "021-12345678"
    assert result["contact_email"] == "sales@steel.example.com"


def test_web_provider_can_be_disabled(monkeypatch):
    request = Mock()
    monkeypatch.setattr(discovery_service.httpx, "get", request)
    monkeypatch.setattr(discovery_service, "_search_tianyancha_candidates", lambda *_: [])
    monkeypatch.setattr(discovery_service.settings, "SUPPLIER_DISCOVERY_WEB_ENABLED", False)

    assert discovery_service.search_external_provider({"category": "钢材"}) == []
    request.assert_not_called()


def test_external_provider_combines_tianyancha_and_web_candidates(monkeypatch):
    tianyancha = [{
        "supplier_name": "华东钢材供应有限公司",
        "source": "tianyancha_search",
    }, {
        "supplier_name": "河北钢铁供应有限公司",
        "source": "tianyancha_search",
    }]
    web = [{
        "supplier_name": "华东钢材供应有限公司",
        "source": "web_search",
    }, {
        "supplier_name": "山东钢材供应有限公司",
        "source": "web_search",
    }]
    monkeypatch.setattr(discovery_service, "_search_tianyancha_candidates", lambda *_: tianyancha)
    monkeypatch.setattr(discovery_service, "_search_web_candidates", lambda *_: web)
    monkeypatch.setattr(discovery_service.settings, "SUPPLIER_DISCOVERY_WEB_ENABLED", True)

    result = discovery_service.search_external_provider({"category": "钢材"})

    assert [item["supplier_name"] for item in result] == [
        "华东钢材供应有限公司",
        "河北钢铁供应有限公司",
        "山东钢材供应有限公司",
    ]


def test_tianyancha_provider_maps_steel_to_industry_codes(monkeypatch):
    requested_codes: list[str] = []

    def search_companies(**kwargs):
        code = kwargs.get("industry", "")
        requested_codes.append(code)
        return {
            "items": [{
                "name": f"钢材企业{code}有限公司",
                "regNumber": f"code-{code}",
            }],
            "total": 1,
        }

    monkeypatch.setattr("app.services.tianyancha_client.search_companies", search_companies)
    monkeypatch.setattr(discovery_service.settings, "SUPPLIER_DISCOVERY_WEB_MAX_RESULTS", 10)

    result = discovery_service._search_tianyancha_candidates("钢材/金属材料", "", "")

    assert set(requested_codes) == {"311", "312", "313", "331"}
    assert len(result) == 4
    assert all(item["verification_status"] == "unverified" for item in result)


def test_company_name_extraction_removes_search_title_prefixes():
    assert discovery_service._extract_company_name(
        "第六届上海大宗商品周优质供应商TOP20推荐天津市瑞达钢材销售有限公司"
    ) == "天津市瑞达钢材销售有限公司"
    assert discovery_service._extract_company_name(
        "网站首页-上海铸然供应链（集团）有限公司"
    ) == "上海铸然供应链（集团）有限公司"
    assert discovery_service._extract_company_name(
        "振石集团东方特钢有限公司"
    ) == "振石集团东方特钢有限公司"


def test_sufficiency_requires_every_explicit_constraint_to_be_covered():
    """Counting candidates alone would allow a required qualification to be missed."""
    candidates = [
        _candidate("a"),
        _candidate("b"),
        _candidate("c", qualifications=[]),
    ]

    assert discovery_service.is_candidate_supply_sufficient(
        candidates, _requirement(qualifications="ISO9001, ISO14001"), _policy()
    ) is False


def test_category_coverage_without_specification_coverage_uses_external_provider(monkeypatch):
    """Checking specifications against categories would skip needed external discovery."""
    local = [
        _candidate(
            name,
            categories=["摄像头", "IP67"],
            specifications=["IP65"],
        )
        for name in ("a", "b", "c")
    ]
    monkeypatch.setattr(discovery_service, "search_local_suppliers", lambda *_: local)
    tianyancha = Mock(return_value=[])
    web = Mock(return_value=[])
    monkeypatch.setattr(discovery_service, "_search_tianyancha_candidates", tianyancha)
    monkeypatch.setattr(discovery_service, "_search_web_candidates", web)

    result = discovery_service.discover_candidates(_requirement(), _policy())

    assert result["source"] == "local"
    assert result["external_status"] == "not_found"
    tianyancha.assert_called_once()
    web.assert_called_once()


def test_local_repository_search_filters_active_category_specification_region_and_qualification(
    monkeypatch,
):
    """Relaxing any local filter would pass candidates that violate the request."""
    documents = [
        {
            "_id": "match", "name": "符合公司", "status": "active",
            "categories": ["摄像头"], "specifications": ["IP67"],
            "regions": ["华东"], "qualifications": ["ISO9001"],
            "capacity": {"available": 10}, "updated_at": "today",
        },
        {
            "_id": "inactive", "name": "停用公司", "status": "inactive",
            "categories": ["摄像头"], "specifications": ["IP67"],
            "regions": ["华东"], "qualifications": ["ISO9001"],
        },
        {
            "_id": "wrong-spec", "name": "规格错误公司", "status": "active",
            "categories": ["摄像头"], "specifications": ["IP65"],
            "regions": ["华东"], "qualifications": ["ISO9001"],
        },
    ]

    class Suppliers:
        def find(self, query):
            assert query == {"status": "active"}
            return [document for document in documents if document["status"] == "active"]

    monkeypatch.setattr(supplier_repo, "get_db", lambda: {"suppliers": Suppliers()})
    monkeypatch.setattr(
        supplier_repo,
        "resolve_supplier_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must remain read-only")),
    )

    candidates = supplier_repo.search_for_sourcing_v2(_requirement())

    assert [candidate["supplier_id"] for candidate in candidates] == ["match"]
    assert candidates[0]["match_reasons"] == [
        "category:摄像头", "specification:IP67", "region:华东", "qualifications:ISO9001"
    ]


def test_local_repository_merges_feishu_capability_and_contact_snapshots(monkeypatch):
    """Formal sourcing must match and expose facts from all three Feishu snapshots."""
    supplier = {
        "_id": "feishu:supplier-1",
        "supplier_id": "feishu:supplier-1",
        "supplier_code": "SUP-001",
        "name": "工业相机供应商",
        "status": "active",
        "categories": [],
        "regions": [],
        "source": "feishu_bitable",
        "sync_status": "current",
    }
    capability = {
        "supplier_id": "feishu:supplier-1",
        "supplier_code": "SUP-001",
        "source": "feishu_bitable",
        "sync_status": "current",
        "category": "工业相机",
        "product_name": "4K工业相机模组",
        "product_keywords": ["Sony IMX", "GigE"],
        "supply_regions": ["华南"],
        "qualifications": "ISO9001",
        "capability_status": "已验证",
    }
    contact = {
        "supplier_id": "feishu:supplier-1",
        "supplier_code": "SUP-001",
        "source": "feishu_bitable",
        "sync_status": "current",
        "contact_name": "张三",
        "phone": "0755-12345678",
        "email": "sales@example.com",
        "is_primary_contact": True,
    }

    class Collection:
        def __init__(self, name, documents):
            self.name = name
            self.documents = documents

        def find(self, _query):
            return self.documents

    database = {
        "supplier_master_snapshots": Collection("supplier_master_snapshots", [supplier]),
        "supplier_capability_snapshots": Collection("supplier_capability_snapshots", [capability]),
        "supplier_contact_snapshots": Collection("supplier_contact_snapshots", [contact]),
    }
    monkeypatch.setattr(supplier_repo, "get_db", lambda: database)
    monkeypatch.setattr(
        supplier_repo,
        "_supplier_read_collection",
        lambda _db: database["supplier_master_snapshots"],
    )

    candidates = supplier_repo.search_for_sourcing_v2({
        "category": "工业相机",
        "specification": "4K",
        "region": "华南",
        "qualifications": "ISO9001",
    })

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["supplier_code"] == "SUP-001"
    assert candidate["categories"] == ["工业相机"]
    assert "4K工业相机模组" in candidate["specifications"]
    assert candidate["regions"] == ["华南"]
    assert candidate["qualifications"] == ["ISO9001"]
    assert candidate["contact_phone"] == "0755-12345678"
    assert candidate["contacts"][0]["email"] == "sales@example.com"
