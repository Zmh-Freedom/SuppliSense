from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import DomainError, domain_error_handler


def test_domain_error_handler_preserves_business_code():
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/boom")
    def boom():
        raise DomainError("COMPANY_CONFLICT", "企业冲突", 409, {"company_id": "c-1"})

    response = TestClient(app).get("/boom")

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "COMPANY_CONFLICT",
            "message": "企业冲突",
            "detail": {"company_id": "c-1"},
        }
    }
