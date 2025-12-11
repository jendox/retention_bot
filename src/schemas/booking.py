from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Booking(BaseModel):
    id: int
    starts_at: datetime
    ends_at: datetime
    client_id: int

    model_config = ConfigDict(
        from_attributes=True,
    )
