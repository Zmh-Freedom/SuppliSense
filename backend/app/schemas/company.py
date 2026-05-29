from pydantic import BaseModel


class CompanyProfile(BaseModel):
    company_name: str
    legal_person: str
    registered_capital: str
    establish_time: str
    is_listed: bool
