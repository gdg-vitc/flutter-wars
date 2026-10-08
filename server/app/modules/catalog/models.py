from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class Widget(SQLModel, table=True):
    __tablename__ = "widget"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(max_length=120)
    archived: bool = False
