from pydantic import BaseModel


class RiskInfo(BaseModel):
    lawsuit_count: int
    executed_count: int
    abnormal_operation_count: int
    administrative_penalty_count: int
